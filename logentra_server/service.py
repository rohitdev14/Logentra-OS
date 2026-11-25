from __future__ import annotations

from typing import Any

from sqlalchemy import and_, exists, func, or_, tuple_
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from fastapi import HTTPException

from logentra_server import events
from logentra_server.models import (
    Annotation,
    Area,
    Document,
    Site,
    AuditLog,
    Equipment,
    EquipmentFieldValue,
    FieldDefinition,
    ParamPreset,
    Parameter,
    Punch,
    Reading,
    ReadingPhoto,
    Record,
    RecordColumn,
    RecordItem,
    RecordValue,
    Setting,
    Signoff,
    Template,
    TemplateColumn,
    TemplateItem,
    WorkOrder,
)
from logentra_server.branding import APP_VERSION, PRODUCT_NAME
from logentra_server.pagination import clamp_page, count_rows, page_result
from logentra_server.utils import as_float, dumps, loads, out_of_range, parse_ts, split_ts, uid, utcnow_iso


def audit(db: Session, action: str, *, actor: str | None = "api", entity_type: str | None = None,
          entity_id: str | None = None, detail: Any = None, user_id: str | None = None) -> None:
    db.add(AuditLog(
        id=uid("aud"), ts=utcnow_iso(), actor=actor, user_id=user_id, action=action,
        entity_type=entity_type, entity_id=entity_id, detail=dumps(detail) if detail is not None else None,
    ))


def default_fields(now: str | None = None) -> list[dict[str, Any]]:
    now = now or utcnow_iso()
    return [
        {"id": "fld_tag", "key": "tag", "label": "Tag", "type": "text", "locked": True, "options": [], "sort_order": 0},
        {"id": "fld_category", "key": "category", "label": "Category", "type": "text", "locked": False, "options": [], "sort_order": 1},
    ]


def ensure_default_fields(db: Session) -> None:
    if db.query(FieldDefinition.id).limit(1).first():
        return
    now = utcnow_iso()
    for i, f in enumerate(default_fields(now)):
        db.add(FieldDefinition(
            id=f["id"], key=f["key"], label=f["label"], type=f["type"],
            options=dumps(f["options"]), locked=1 if f["locked"] else 0,
            sort_order=i, created_at=now, updated_at=now,
        ))
    db.flush()


# ---------- serializers (v0.2-compatible UI state) ----------

def field_to_ui(f: FieldDefinition) -> dict[str, Any]:
    return {
        "id": f.id, "key": f.key, "label": f.label, "type": f.type,
        "options": loads(f.options, []) or [], "locked": bool(f.locked),
    }


def param_to_ui(p: Parameter | ParamPreset) -> dict[str, Any]:
    return {
        "key": p.key, "label": p.label, "unit": p.unit or "",
        "min": "" if p.min_value is None else p.min_value,
        "max": "" if p.max_value is None else p.max_value,
    }


def equipment_to_ui(eq: Equipment) -> dict[str, Any]:
    return {
        "id": eq.id,
        "values": {v.field_id: (v.value if v.value is not None else "") for v in eq.values},
        "params": [param_to_ui(p) for p in sorted(eq.parameters, key=lambda x: (x.sort_order, x.key))],
        "site_id": eq.site_id,
        "area_id": eq.area_id,
        "tag": eq.tag,
        "qr_code_issued": bool(eq.qr_code),
        "createdAt": eq.created_at[:10] if eq.created_at else "",
    }


def equipment_index_row(eq: Equipment) -> dict[str, Any]:
    return {"id": eq.id, "tag": eq.tag, "site_id": eq.site_id, "area_id": eq.area_id}


def apply_scope(query, model, principal) -> Any:
    """Limit a query to the caller's site/area when those are set on the user."""
    if principal is None:
        return query
    site_id = getattr(principal, "site_id", None)
    area_id = getattr(principal, "area_id", None)
    if site_id and hasattr(model, "site_id"):
        query = query.filter(model.site_id == site_id)
    if area_id and hasattr(model, "area_id"):
        query = query.filter(model.area_id == area_id)
    return query


def apply_evidence_scope(db, query, model, principal):
    """Fail closed on historical links whose current scope disagrees with the parent."""
    query = apply_scope(query, model, principal)
    if not principal or not principal.scoped:
        return query
    assets = apply_scope(db.query(Equipment.id), Equipment, principal).statement
    records = db.query(Record.id).filter(Record.equipment_id.in_(assets)).statement

    def permits(column, ids):
        return or_(column.is_(None), column.in_(ids))

    def scoped_work(orders):
        readings = db.query(Reading.id).filter(Reading.equipment_id.in_(assets)).statement
        annotations = db.query(Annotation.id).filter(Annotation.reading_id.in_(readings)).statement
        punches = db.query(Punch.id).filter(Punch.record_id.in_(records)).statement
        return orders.filter(
            permits(WorkOrder.equipment_id, assets), permits(WorkOrder.linked_record_id, records),
            permits(WorkOrder.linked_reading_id, readings), permits(WorkOrder.linked_annotation_id, annotations),
            permits(WorkOrder.linked_punch_id, punches),
        )

    if model is WorkOrder:
        return scoped_work(query)
    orders = scoped_work(apply_scope(db.query(WorkOrder.id), WorkOrder, principal)).statement
    return query.filter(permits(Document.equipment_id, assets), permits(Document.record_id, records),
                        permits(Document.work_order_id, orders))



def substring_match(term: str, *columns, extra=None):
    """Match an exact value or a case-insensitive substring (btree + pg_trgm)."""
    term = (term or "").strip()
    like = f"%{term}%"
    clauses = []
    for column in columns:
        clauses.append(column == term)
        clauses.append(column.ilike(like))
    if extra is not None:
        clauses.append(extra)
    return or_(*clauses)


def list_equipment(
    db: Session,
    *,
    q: str | None = None,
    site_id: str | None = None,
    area_id: str | None = None,
    category: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    principal=None,
    paginate: bool = False,
    serialize=equipment_to_ui,
) -> Any:
    query = db.query(Equipment).options(selectinload(Equipment.values), selectinload(Equipment.parameters))
    query = apply_scope(query, Equipment, principal)
    if site_id:
        query = query.filter(Equipment.site_id == site_id)
    if area_id:
        query = query.filter(Equipment.area_id == area_id)
    if q:
        term = q.strip()
        like = f"%{term}%"
        val_exists = exists().where(
            EquipmentFieldValue.equipment_id == Equipment.id,
            or_(EquipmentFieldValue.value == term, EquipmentFieldValue.value.ilike(like)),
        )
        query = query.filter(substring_match(term, Equipment.tag, Equipment.id, extra=val_exists))
    if category:
        # category lives in the EAV bag — join field_definitions.key == 'category'
        from logentra_server.models import FieldDefinition
        cat_exists = exists().where(
            EquipmentFieldValue.equipment_id == Equipment.id,
            EquipmentFieldValue.field_id == FieldDefinition.id,
            FieldDefinition.key == "category",
            EquipmentFieldValue.value == category,
        )
        query = query.filter(cat_exists)
    query = query.order_by(Equipment.tag, Equipment.id)
    if not paginate and page is None:
        return [serialize(e) for e in query.all()]
    page, page_size = clamp_page(page, page_size)
    total = count_rows(query, Equipment.id)
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return page_result([serialize(e) for e in rows], total, page, page_size)


def list_templates(db: Session, *, q: str | None = None, page: int | None = None, page_size: int | None = None, paginate: bool = False):
    query = db.query(Template).options(selectinload(Template.columns), selectinload(Template.items))
    if q:
        query = query.filter(substring_match(q, Template.name, Template.kind))
    query = query.order_by(Template.name, Template.id)
    if not paginate and page is None:
        return [template_to_ui(t) for t in query.all()]
    page, page_size = clamp_page(page, page_size)
    total = count_rows(query, Template.id)
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return page_result([template_to_ui(t) for t in rows], total, page, page_size)


def list_records(
    db: Session,
    *,
    q: str | None = None,
    equipment_id: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    paginate: bool = False,
    principal=None,
):
    query = db.query(Record).options(
        selectinload(Record.columns),
        selectinload(Record.items).selectinload(RecordItem.values),
        selectinload(Record.punches),
        selectinload(Record.signoff),
    )
    if equipment_id:
        query = query.filter(Record.equipment_id == equipment_id)
    if principal and principal.scoped:
        scoped_equipment = apply_scope(db.query(Equipment.id), Equipment, principal)
        query = query.filter(Record.equipment_id.in_(scoped_equipment.statement))
    if q:
        query = query.filter(substring_match(q, Record.template_name, Record.id, Record.notes))
    query = query.order_by(Record.created_at.desc(), Record.id.desc())
    if not paginate and page is None:
        return [record_to_ui(r) for r in query.all()]
    page, page_size = clamp_page(page, page_size)
    total = count_rows(query, Record.id)
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return page_result([record_to_ui(r) for r in rows], total, page, page_size)


def add_log_photos(db, entries):
    by_key = {(entry["id"], entry["equipmentId"]): entry for entry in entries}
    keys = list(by_key)
    for offset in range(0, len(keys), 400):
        photos = db.query(ReadingPhoto).filter(
            tuple_(ReadingPhoto.entry_id, ReadingPhoto.equipment_id).in_(keys[offset:offset + 400])
        ).order_by(ReadingPhoto.created_at, ReadingPhoto.id)
        for photo in photos:
            by_key[(photo.entry_id, photo.equipment_id)].setdefault("photos", []).append(
                {"id": photo.id, "filename": photo.filename}
            )
    return entries


def list_logs(
    db: Session,
    *,
    equipment_id: str | None = None,
    q: str | None = None,
    ts_from: str | None = None,
    ts_to: str | None = None,
    page: int | None = 1,
    page_size: int | None = 50,
    principal=None,
) -> dict[str, Any]:
    """Paginate grouped log-book lines (one row per entry_id)."""
    page, page_size = clamp_page(page, page_size)
    entry_q = db.query(Reading.entry_id, func.min(Reading.ts).label("ts"))
    if equipment_id:
        entry_q = entry_q.filter(Reading.equipment_id == equipment_id)
    def scoped_readings(query):
        if principal and principal.scoped:
            equipment = apply_scope(db.query(Equipment.id), Equipment, principal)
            query = query.filter(Reading.equipment_id.in_(equipment.statement))
        return query

    entry_q = scoped_readings(entry_q)
    if ts_from:
        start = parse_ts(ts=ts_from) if ("T" in ts_from or len(ts_from) > 10) else (ts_from + "T00:00:00")
        entry_q = entry_q.filter(Reading.ts >= start)
    if ts_to:
        end = ts_to if ("T" in ts_to or len(ts_to) > 10) else (ts_to + "T23:59:59")
        entry_q = entry_q.filter(Reading.ts <= end)
    if q:
        entry_q = entry_q.filter(substring_match(q, Reading.entry_id, Reading.parameter_key, Reading.remarks))
    entry_q = entry_q.group_by(Reading.entry_id)
    total = int(entry_q.order_by(None).enable_eagerloads(False).count() or 0)
    page_ids = (
        entry_q.order_by(func.min(Reading.ts).desc(), Reading.entry_id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    ids = [row[0] for row in page_ids]
    if not ids:
        return page_result([], total, page, page_size)
    # Reuse logs_to_ui grouping on just this page of entries
    rows = (
        scoped_readings(db.query(Reading))
        .options(selectinload(Reading.annotation))
        .filter(Reading.entry_id.in_(ids))
        .order_by(Reading.ts, Reading.id)
        .all()
    )
    # temporarily group
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.entry_id not in groups:
            date, time = split_ts(r.ts)
            groups[r.entry_id] = {
                "id": r.entry_id,
                "equipmentId": r.equipment_id,
                "date": date,
                "time": time,
                "readings": {},
                "remarks": r.remarks or "",
                "enteredBy": r.entered_by or "",
                "annotations": {},
            }
        g = groups[r.entry_id]
        g["readings"][r.parameter_key] = r.value
        if r.remarks:
            g["remarks"] = r.remarks
        if r.entered_by:
            g["enteredBy"] = r.entered_by
        if r.annotation and (r.annotation.cause or r.annotation.action or r.annotation.annotated_by):
            g["annotations"][r.parameter_key] = {
                "cause": r.annotation.cause or "",
                "action": r.annotation.action or "",
                "by": r.annotation.annotated_by or "",
                "at": r.annotation.annotated_at or "",
            }
    items = [groups[i] for i in ids if i in groups]
    if principal is None or principal.can("readings.read"):
        add_log_photos(db, items)
    return page_result(items, total, page, page_size)


def template_to_ui(t: Template) -> dict[str, Any]:
    return {
        "id": t.id, "name": t.name, "kind": t.kind, "category": t.category or "",
        "columns": [{
            "id": c.id, "key": c.key, "label": c.label, "type": c.type,
            "options": loads(c.options, []) or [],
        } for c in sorted(t.columns, key=lambda x: x.sort_order)],
        "rows": [{"id": r.id, "label": r.label, "hint": r.hint or ""} for r in sorted(t.items, key=lambda x: x.sort_order)],
    }


def record_to_ui(r: Record) -> dict[str, Any]:
    cols = sorted(r.columns, key=lambda x: x.sort_order)
    items_out = []
    for it in sorted(r.items, key=lambda x: x.sort_order):
        vals = {v.column_id: (v.value or "") for v in it.values}
        items_out.append({"id": it.id, "label": it.label, "hint": it.hint or "", "values": vals})
    punches = []
    for p in r.punches:
        punches.append({
            "id": p.id, "description": p.description, "priority": p.priority or "",
            "status": p.status, "owner": p.owner or "", "raised": p.raised_at or "",
            "closed": p.closed_at or "",
        })
    return {
        "id": r.id, "revision": r.revision, "templateId": r.template_id or "", "templateName": r.template_name or "",
        "kind": r.kind or "", "equipmentId": r.equipment_id or "", "createdAt": (r.created_at or "")[:10],
        "notes": r.notes or "", "columns": [{
            "id": c.id, "key": c.key, "label": c.label, "type": c.type,
            "options": loads(c.options, []) or [],
        } for c in cols],
        "items": items_out, "punches": punches,
        "signoffName": r.signoff.name if r.signoff else "",
        "signoffDate": r.signoff.signed_at[:10] if r.signoff else "",
    }


def logs_to_ui(db: Session, *, include_photos: bool = True) -> list[dict[str, Any]]:
    q = (
        db.query(Reading)
        .options(selectinload(Reading.annotation))
        .order_by(Reading.ts, Reading.id)
    )
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in q:
        if r.entry_id not in groups:
            date, time = split_ts(r.ts)
            groups[r.entry_id] = {
                "id": r.entry_id,
                "equipmentId": r.equipment_id,
                "date": date,
                "time": time,
                "readings": {},
                "remarks": r.remarks or "",
                "enteredBy": r.entered_by or "",
                "annotations": {},
            }
            order.append(r.entry_id)
        g = groups[r.entry_id]
        g["readings"][r.parameter_key] = r.value
        if r.remarks:
            g["remarks"] = r.remarks
        if r.entered_by:
            g["enteredBy"] = r.entered_by
        if r.annotation and (r.annotation.cause or r.annotation.action or r.annotation.annotated_by):
            g["annotations"][r.parameter_key] = {
                "cause": r.annotation.cause or "",
                "action": r.annotation.action or "",
                "by": r.annotation.annotated_by or "",
                "at": r.annotation.annotated_at or "",
            }
    entries = [groups[k] for k in order]
    return add_log_photos(db, entries) if include_photos else entries


def bootstrap(db: Session, *, backend: str = "sqlite", equipment_cap: int = 1500, principal=None) -> dict[str, Any]:
    ensure_default_fields(db)
    from logentra_server import seed as _seed
    _seed.ensure_default_routes(db)
    db.commit()
    fields = db.query(FieldDefinition).order_by(FieldDefinition.sort_order, FieldDefinition.key).all()
    revision = catalog_revision(db)
    eq_query = apply_scope(db.query(Equipment), Equipment, principal)
    eq_count = eq_query.count()
    readings = db.query(func.count(Reading.id))
    scoped = principal and principal.scoped
    if scoped:
        readings = readings.filter(Reading.equipment_id.in_(eq_query.with_entities(Equipment.id).statement))
    reading_count = readings.scalar() or 0
    paginated = bool(scoped or eq_count > equipment_cap or reading_count > 8000)
    if paginated:
        equipment: list[dict[str, Any]] = []
        index_rows = apply_scope(db.query(Equipment), Equipment, principal).order_by(Equipment.tag).all()
        equipment_index = [equipment_index_row(e) for e in index_rows]
        logs = list_logs(db, page=1, page_size=200, principal=principal).get("items", [])
        rec_page = list_records(db, page=1, page_size=200, paginate=True, principal=principal)
        records = rec_page["items"]
        record_total = rec_page["total"]
    else:
        equipment_rows = eq_query.options(
            selectinload(Equipment.values), selectinload(Equipment.parameters)
        ).order_by(Equipment.tag, Equipment.id).all()
        equipment = [equipment_to_ui(e) for e in equipment_rows]
        equipment_index = [equipment_index_row(e) for e in equipment_rows]
        rec_query = db.query(Record).options(
            selectinload(Record.columns),
            selectinload(Record.items).selectinload(RecordItem.values),
            selectinload(Record.punches),
            selectinload(Record.signoff),
        )
        records = [record_to_ui(r) for r in rec_query.all()]
        record_total = len(records)
        logs = logs_to_ui(db, include_photos=principal is None or principal.can("readings.read"))
    templates = db.query(Template).options(
        selectinload(Template.columns), selectinload(Template.items)
    ).all()
    presets: dict[str, list] = {}
    for p in db.query(ParamPreset).order_by(ParamPreset.category, ParamPreset.sort_order).all():
        presets.setdefault(p.category, []).append(param_to_ui(p))
    demo_flag = db.get(Setting, "demo")
    updated = db.get(Setting, "updated_at")
    if revision != catalog_revision(db):
        raise HTTPException(409, "Catalog changed during loading. Retry the request.")
    return {
        "meta": {
            "version": 4,
            "app": APP_VERSION,
            "product": PRODUCT_NAME,
            "updatedAt": updated.value if updated else None,
            "catalogRevision": revision,
            "demo": (demo_flag.value == "1") if demo_flag else False,
            "backend": backend,
            "store": "database",
            "equipmentTotal": eq_count,
            "readingsTotal": reading_count,
            "recordTotal": record_total,
            "paginated": paginated,
        },
        "equipmentFields": [field_to_ui(f) for f in fields],
        "equipment": equipment,
        "equipmentIndex": equipment_index,
        "templates": [template_to_ui(t) for t in templates],
        "records": records,
        "paramPresets": presets,
        "logs": logs,
    }


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    now = utcnow_iso()
    if row:
        row.value = value
        row.updated_at = now
    else:
        db.add(Setting(key=key, value=value, updated_at=now))


def catalog_revision(db: Session) -> str | None:
    return db.query(Setting.value).filter_by(key="catalog_revision").scalar()


def advance_catalog(db: Session, expected: str | None = None) -> str:
    revision = uid("catalog")
    query = db.query(Setting).filter_by(key="catalog_revision")
    if expected is not None:
        query = query.filter_by(value=expected)
    changed = query.update({"value": revision, "updated_at": utcnow_iso()}, synchronize_session=False)
    if not changed:
        if expected is not None:
            raise HTTPException(409, "Catalog changed. Reload before saving.")
        set_setting(db, "catalog_revision", revision)
        db.flush()
    return revision


# ---------- catalog upsert (UI persist) ----------

def _upsert_fields(db: Session, fields: list[dict[str, Any]]) -> None:
    now = utcnow_iso()
    for i, f in enumerate(fields or []):
        fid = f.get("id") or uid("fld")
        row = db.get(FieldDefinition, fid)
        opts = dumps(f.get("options") or [])
        if row:
            row.key = f.get("key") or row.key
            row.label = f.get("label") or row.label
            row.type = f.get("type") or row.type
            row.options = opts
            row.locked = 1 if f.get("locked") else 0
            row.sort_order = i
            row.updated_at = now
        else:
            db.add(FieldDefinition(
                id=fid, key=f.get("key") or fid, label=f.get("label") or "Field",
                type=f.get("type") or "text", options=opts,
                locked=1 if f.get("locked") else 0, sort_order=i,
                created_at=now, updated_at=now,
            ))
    db.flush()


def _set_eq_values(db: Session, eq: Equipment, values: dict[str, Any], fields: list[FieldDefinition]) -> str:
    """Write EAV values; return tag."""
    by_id = {f.id: f for f in fields}
    by_key = {f.key: f for f in fields}
    tag = eq.tag or ""
    for k, v in (values or {}).items():
        field = by_id.get(k) or by_key.get(k)
        fid = field.id if field else k
        row = db.get(EquipmentFieldValue, {"equipment_id": eq.id, "field_id": fid})
        sval = "" if v is None else str(v)
        if row:
            row.value = sval
        else:
            # skip values for unknown fields that are not in field_definitions (FK)
            if field is None and db.get(FieldDefinition, fid) is None:
                continue
            db.add(EquipmentFieldValue(equipment_id=eq.id, field_id=fid, value=sval))
        if field and field.key == "tag" and sval.strip():
            tag = sval.strip()
    return tag


def _upsert_params(db: Session, equipment_id: str, params: list[dict[str, Any]]) -> None:
    seen = set()
    for i, p in enumerate(params or []):
        key = p.get("key")
        if not key:
            continue
        seen.add(key)
        row = db.query(Parameter).filter_by(equipment_id=equipment_id, key=key).one_or_none()
        mn, mx = as_float(p.get("min")), as_float(p.get("max"))
        if row:
            row.label = p.get("label") or row.label
            row.unit = p.get("unit") or ""
            row.min_value, row.max_value = mn, mx
            row.sort_order = i
        else:
            db.add(Parameter(
                id=uid("prm"), equipment_id=equipment_id, key=key,
                label=p.get("label") or key, unit=p.get("unit") or "",
                min_value=mn, max_value=mx, sort_order=i,
            ))
    if params is not None:
        for row in db.query(Parameter).filter_by(equipment_id=equipment_id).all():
            if row.key not in seen:
                db.delete(row)


def validate_location(db: Session, site_id, area_id) -> None:
    if site_id and db.get(Site, site_id) is None:
        raise HTTPException(400, "Site not found")
    if area_id:
        area = db.get(Area, area_id)
        if not area or area.site_id != site_id:
            raise HTTPException(400, "Area does not belong to the selected site")


def protect_document_links(db: Session, column, identifier) -> None:
    if db.query(Document.id).filter(column == identifier, or_(
        Document.locked == 1, Document.status == "approved"
    )).first():
        raise HTTPException(409, "This object is linked to approved evidence")


def upsert_equipment_list(db: Session, items: list[dict[str, Any]]) -> list[Equipment]:
    fields = db.query(FieldDefinition).all()
    now = utcnow_iso()
    out = []
    for e in items or []:
        eid = e.get("id") or uid("eq")
        eq = db.get(Equipment, eid)
        created = e.get("createdAt") or now
        if not eq:
            eq = Equipment(id=eid, tag=eid, created_at=created, updated_at=now)
            db.add(eq)
            db.flush()
        tag = _set_eq_values(db, eq, e.get("values") or {}, fields)
        if not tag:
            tag = eq.tag or eid
        if "site_id" in e or "area_id" in e:
            site_id = e.get("site_id", eq.site_id) or None
            area_id = e.get("area_id", eq.area_id) or None
            validate_location(db, site_id, area_id)
            eq.site_id, eq.area_id = site_id, area_id
        # Equipment tags are a global identifier for legacy REST and MQTT clients.
        other = db.query(Equipment).filter(Equipment.tag == tag, Equipment.id != eq.id).one_or_none()
        if other:
            raise HTTPException(409, "Equipment tag already exists")
        eq.tag = tag
        eq.updated_at = now
        if "params" in e:
            _upsert_params(db, eq.id, e.get("params") or [])
        out.append(eq)
    db.flush()
    return out


def upsert_templates(db: Session, items: list[dict[str, Any]]) -> None:
    now = utcnow_iso()
    for t in items or []:
        tid = t.get("id") or uid("tpl")
        row = db.get(Template, tid)
        if not row:
            row = Template(id=tid, name=t.get("name") or "Template", kind=t.get("kind") or "custom",
                           category=t.get("category") or "", created_at=now, updated_at=now)
            db.add(row)
            db.flush()
        else:
            row.name = t.get("name") or row.name
            row.kind = t.get("kind") or row.kind
            row.category = t.get("category") if "category" in t else row.category
            row.updated_at = now
        # replace columns/items when provided
        if "columns" in t:
            db.query(TemplateColumn).filter_by(template_id=tid).delete()
            for i, c in enumerate(t.get("columns") or []):
                db.add(TemplateColumn(
                    id=c.get("id") or uid("col"), template_id=tid,
                    key=c.get("key") or f"c{i}", label=c.get("label") or "Column",
                    type=c.get("type") or "text", options=dumps(c.get("options") or []),
                    sort_order=i,
                ))
        if "rows" in t:
            db.query(TemplateItem).filter_by(template_id=tid).delete()
            for i, r in enumerate(t.get("rows") or []):
                db.add(TemplateItem(
                    id=r.get("id") or uid("ti"), template_id=tid,
                    label=r.get("label") or "Item", hint=r.get("hint") or "", sort_order=i,
                ))
    db.flush()


def upsert_records(db: Session, items: list[dict[str, Any]]) -> None:
    now = utcnow_iso()
    for rec in items or []:
        rid = rec.get("id") or uid("rec")
        row = db.get(Record, rid)
        if not row:
            row = Record(
                id=rid, template_id=rec.get("templateId") or None,
                template_name=rec.get("templateName") or "",
                kind=rec.get("kind") or "", equipment_id=rec.get("equipmentId") or None,
                notes=rec.get("notes") or "", created_at=rec.get("createdAt") or now, updated_at=now,
            )
            db.add(row)
            db.flush()
        else:
            previous = record_to_ui(row)
            changed = any(rec[key] != previous[key] for key in previous.keys() & rec.keys() - {"revision"})
            if not changed:
                continue
            if row.signoff:
                raise HTTPException(409, "Signed records cannot be changed")
            if rec.get("revision") != row.revision:
                raise HTTPException(409, "Record changed. Reload before saving.")
            audit(db, "revise", entity_type="record", entity_id=rid, detail=previous)
            row.revision += 1
            row.template_name = rec.get("templateName", row.template_name)
            row.kind = rec.get("kind", row.kind)
            row.equipment_id = rec.get("equipmentId", row.equipment_id) or row.equipment_id
            row.notes = rec.get("notes", row.notes)
            row.updated_at = now
            db.flush()
        if "columns" in rec or "items" in rec:
            col_ids = [c.id for c in db.query(RecordColumn).filter_by(record_id=rid).all()]
            item_ids = [i.id for i in db.query(RecordItem).filter_by(record_id=rid).all()]
            if col_ids or item_ids:
                qv = db.query(RecordValue)
                if item_ids:
                    qv = qv.filter(RecordValue.item_id.in_(item_ids))
                elif col_ids:
                    qv = qv.filter(RecordValue.column_id.in_(col_ids))
                qv.delete(synchronize_session=False)
            db.query(RecordItem).filter_by(record_id=rid).delete()
            db.query(RecordColumn).filter_by(record_id=rid).delete()
            db.flush()
        col_map: dict[str, str] = {}
        if "columns" in rec:
            for i, c in enumerate(rec.get("columns") or []):
                old_id = c.get("id") or uid("col")
                new_id = old_id
                if db.get(RecordColumn, new_id) or new_id in col_map.values():
                    new_id = uid("col")
                col_map[old_id] = new_id
                db.add(RecordColumn(
                    id=new_id, record_id=rid, key=c.get("key") or f"c{i}",
                    label=c.get("label") or "Column", type=c.get("type") or "text",
                    options=dumps(c.get("options") or []), sort_order=i,
                ))
            db.flush()
        if "items" in rec:
            for i, it in enumerate(rec.get("items") or []):
                old_iid = it.get("id") or uid("it")
                new_iid = old_iid
                if db.get(RecordItem, new_iid):
                    new_iid = uid("it")
                item = RecordItem(
                    id=new_iid, record_id=rid,
                    label=it.get("label") or "Item", hint=it.get("hint") or "", sort_order=i,
                )
                db.add(item)
                db.flush()
                for cid, val in (it.get("values") or {}).items():
                    db.add(RecordValue(
                        item_id=item.id, column_id=col_map.get(cid, cid),
                        value="" if val is None else str(val),
                    ))
        if "punches" in rec:
            db.query(Punch).filter_by(record_id=rid).delete()
            for p in rec.get("punches") or []:
                db.add(Punch(
                    id=p.get("id") or uid("pn"), record_id=rid,
                    description=p.get("description") or "", priority=p.get("priority") or "",
                    status=p.get("status") or "Open", owner=p.get("owner") or "",
                    raised_at=p.get("raised") or p.get("raised_at") or "",
                    closed_at=p.get("closed") or p.get("closed_at") or "",
                    created_at=now,
                ))
        name = rec.get("signoffName") or ""
        date = rec.get("signoffDate") or ""
        existing = db.query(Signoff).filter_by(record_id=rid).one_or_none()
        if name and date:
            if existing:
                existing.name, existing.signed_at = name, date
            else:
                db.add(Signoff(id=uid("sg"), record_id=rid, name=name, signed_at=date))
        elif existing and not name:
            db.delete(existing)
    db.flush()


def upsert_presets(db: Session, presets: dict[str, list]) -> None:
    db.query(ParamPreset).delete()
    for cat, items in (presets or {}).items():
        for i, p in enumerate(items or []):
            db.add(ParamPreset(
                id=uid("pp"), category=cat, key=p.get("key") or f"p{i}",
                label=p.get("label") or p.get("key") or "Parameter",
                unit=p.get("unit") or "", min_value=as_float(p.get("min")),
                max_value=as_float(p.get("max")), sort_order=i,
            ))
    db.flush()


def require_signoff_permission(db: Session, principal, record: dict) -> None:
    if not record.get("signoffName") or not record.get("signoffDate"):
        return
    old = db.get(Record, record["id"]) if record.get("id") else None
    if not old or not old.signoff or (
        old.signoff.name != record["signoffName"] or old.signoff.signed_at[:10] != record["signoffDate"]
    ):
        principal.require("signoff")

def scoped_equipment_input(db, principal, payload):
    if not principal.scoped:
        return payload
    existing = db.get(Equipment, payload["id"]) if payload.get("id") else None
    if existing and not principal.allows_scope(existing.site_id, existing.area_id):
        raise HTTPException(404, "Equipment not found")
    old_site, old_area = (existing.site_id, existing.area_id) if existing else (principal.site_id, principal.area_id)
    site = payload.get("site_id", old_site)
    area = payload.get("area_id", old_area)
    if not principal.allows_scope(site, area):
        raise HTTPException(403, "Equipment location is outside your scope")
    if existing and (site, area) != (old_site, old_area):
        raise HTTPException(403, "Changing equipment location requires unscoped access")
    return {**payload, "site_id": site, "area_id": area}

def require_record_scope(db, principal, record_id, equipment_id=None):
    if not principal.scoped:
        return
    existing = db.get(Record, record_id) if record_id else None
    if existing and (not existing.equipment or not principal.allows_scope(
        existing.equipment.site_id, existing.equipment.area_id
    )):
        raise HTTPException(404, "Record not found")
    if existing and equipment_id and equipment_id != existing.equipment_id:
        raise HTTPException(403, "Moving records requires unscoped access")
    if not existing:
        equipment = db.get(Equipment, equipment_id) if equipment_id else None
        if not equipment or not principal.allows_scope(equipment.site_id, equipment.area_id):
            raise HTTPException(404, "Equipment not found")

def sync_catalog(db: Session, state: dict[str, Any], *, actor: str = "ui",
                 expected: str | None = None, commit: bool = True, principal=None) -> dict[str, Any]:
    revision = advance_catalog(db, expected)
    if principal:
        if principal.scoped:
            if set(state) - {"equipment", "records"}:
                principal.require_global()
            if "equipment" in state:
                state["equipment"] = [scoped_equipment_input(db, principal, item) for item in state["equipment"]]
        for record in state.get("records", []):
            require_record_scope(db, principal, record.get("id"), record.get("equipmentId"))
            require_signoff_permission(db, principal, record)
    ensure_default_fields(db)
    if "equipmentFields" in state:
        _upsert_fields(db, state["equipmentFields"])
    if "equipment" in state:
        upsert_equipment_list(db, state["equipment"])
    if "templates" in state:
        upsert_templates(db, state["templates"])
    if "records" in state:
        upsert_records(db, state["records"])
    if "paramPresets" in state:
        upsert_presets(db, state["paramPresets"])
    set_setting(db, "updated_at", utcnow_iso())
    audit(db, "sync", actor=actor, entity_type="catalog", detail={"keys": list(state.keys())})
    if commit:
        db.commit()
    else:
        db.flush()
    return {"ok": True, "updatedAt": utcnow_iso(), "catalogRevision": revision,
            "recordRevisions": {row.id: row.revision for row in db.query(Record).filter(Record.id.in_(
                [record["id"] for record in state.get("records", []) if record.get("id")]
            ))}}


# ---------- readings ----------

def _limits_for(db: Session, equipment_id: str, key: str) -> tuple[Parameter | None, float | None, float | None]:
    p = db.query(Parameter).filter_by(equipment_id=equipment_id, key=key).one_or_none()
    if p:
        return p, p.min_value, p.max_value
    return None, None, None


def ensure_parameter(db: Session, equipment_id: str, key: str, label: str | None = None, unit: str | None = None) -> Parameter:
    p, _, _ = _limits_for(db, equipment_id, key)
    if p:
        return p
    p = Parameter(
        id=uid("prm"), equipment_id=equipment_id, key=key,
        label=label or key, unit=unit or "", min_value=None, max_value=None, sort_order=99,
    )
    db.add(p)
    db.flush()
    return p


def get_equipment(db: Session, *, equipment_id: str | None = None, tag: str | None = None) -> Equipment | None:
    if equipment_id:
        eq = db.get(Equipment, equipment_id)
        if eq:
            return eq
    if tag:
        return db.query(Equipment).filter(Equipment.tag == tag.strip()).one_or_none()
    return None


def ingest_readings(
    db: Session,
    items: list[dict[str, Any]],
    *,
    source: str = "api",
    actor: str = "api",
    create_missing_equipment: bool = False,
    fire_webhooks: bool = True,
    commit: bool = True,
    principal=None,
) -> dict[str, Any]:
    """Insert many readings. Each item: tag or equipment_id, parameter, value, optional ts/date/time, remarks, entered_by, entry_id, unit, label."""
    ensure_default_fields(db)
    created = []
    skipped = []
    oor_events = []
    fields = db.query(FieldDefinition).all()
    tag_field = next((f for f in fields if f.key == "tag"), None)
    now = utcnow_iso()
    if principal and principal.scoped and create_missing_equipment:
        raise HTTPException(403, "Scoped ingestion requires existing equipment")
    for raw in items:
        tag = str(raw.get("tag") or raw.get("equipment") or "").strip()
        reading_id = raw.get("id") or uid("rd")
        eid = raw.get("equipment_id") or raw.get("equipmentId")
        eq = get_equipment(db, equipment_id=eid, tag=tag or None)
        if principal and principal.scoped and (not eq or not principal.allows_scope(eq.site_id, eq.area_id)):
            raise HTTPException(404, "Equipment not found")
        if not eq:
            if create_missing_equipment and tag:
                advance_catalog(db)
                eq = Equipment(id=uid("eq"), tag=tag, created_at=now, updated_at=now)
                db.add(eq)
                db.flush()
                if tag_field:
                    db.add(EquipmentFieldValue(equipment_id=eq.id, field_id=tag_field.id, value=tag))
            else:
                skipped.append({"reason": "unknown_equipment", "item": {"tag": tag, "equipment_id": eid}})
                continue
        key_raw = str(raw.get("parameter") or raw.get("parameter_key") or raw.get("key") or "").strip()
        if not key_raw:
            skipped.append({"reason": "missing_parameter", "item": raw})
            continue
        # allow label match
        param = (
            db.query(Parameter)
            .filter(Parameter.equipment_id == eq.id, or_(Parameter.key == key_raw, Parameter.label == key_raw))
            .order_by((Parameter.key == key_raw).desc())
            .first()
        )
        key = param.key if param else key_raw
        if not param:
            if principal and principal.scoped:
                principal.require("equipment.write")
            advance_catalog(db)
            param = ensure_parameter(db, eq.id, key, label=raw.get("label") or key_raw, unit=raw.get("unit"))
        value = as_float(raw.get("value") if "value" in raw else raw.get("reading"))
        if value is None:
            skipped.append({"reason": "invalid_value", "item": raw})
            continue
        try:
            ts = parse_ts(raw.get("date"), raw.get("time"), raw.get("ts") or raw.get("timestamp"))
        except (ValueError, TypeError, AttributeError):
            skipped.append({"reason": "invalid_timestamp"})
            continue
        entry_id = raw.get("entry_id") or raw.get("entryId") or uid("log")
        reading = Reading(
            id=reading_id,
            entry_id=entry_id,
            equipment_id=eq.id,
            parameter_key=param.key,
            ts=ts,
            value=value,
            remarks=raw.get("remarks") or raw.get("comment") or "",
            entered_by=raw.get("entered_by") or raw.get("enteredBy") or raw.get("by") or "",
            source=raw.get("source") or source,
            limit_min=param.min_value,
            limit_max=param.max_value,
            created_at=now,
        )
        reading.out_of_range = 1 if out_of_range(value, param.min_value, param.max_value) else 0
        if raw.get("id"):
            insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else postgres_insert
            values = {column.name: getattr(reading, column.name) for column in Reading.__table__.columns}
            inserted = db.execute(insert(Reading).values(**values).on_conflict_do_nothing(
                index_elements=[Reading.id]
            ).returning(Reading)).scalar_one_or_none()
            if inserted is None:
                existing = db.get(Reading, reading_id)
                fields = ["equipment_id", "parameter_key", "ts", "value", "remarks", "entered_by", "source"]
                if raw.get("entry_id") or raw.get("entryId"):
                    fields.append("entry_id")
                if any(getattr(existing, key) != getattr(reading, key) for key in fields):
                    raise HTTPException(409, "Reading ID already belongs to different data")
                skipped.append({"reason": "duplicate", "id": reading_id})
                continue
            reading = inserted
        else:
            db.add(reading)
            db.flush()
        # optional annotation in the same payload
        cause = raw.get("annot_cause") or raw.get("cause")
        action = raw.get("annot_action") or raw.get("action")
        by = raw.get("annot_by")
        at = raw.get("annot_at")
        if cause or action or by:
            db.add(Annotation(
                id=uid("an"), reading_id=reading.id, cause=cause or "", action=action or "",
                annotated_by=by or "", annotated_at=at or now, created_at=now,
            ))
        created.append(reading)
        if out_of_range(value, param.min_value, param.max_value):
            oor_events.append((reading, eq.tag, param.label))
    audit(db, "ingest", actor=actor, entity_type="reading", detail={"created": len(created), "skipped": len(skipped), "source": source})
    if db.query(Setting.value).filter_by(key="updated_at").scalar() != now:
        dialect_insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else postgres_insert
        statement = dialect_insert(Setting).values(key="updated_at", value=now, updated_at=now)
        db.execute(statement.on_conflict_do_update(
            index_elements=[Setting.key], set_={"value": now, "updated_at": now},
            where=Setting.updated_at < now,
        ))
    hook_results = []
    auto_wo: list[str] = []
    if oor_events:
        if fire_webhooks:
            for reading, tag, label in oor_events:
                hook_results.extend(events.enqueue(db, "out_of_range", events.oor_payload(reading, tag, label)))
        from logentra_server.workorders import maybe_auto_create
        for reading, tag, label in oor_events:
            wo = maybe_auto_create(db, reading, tag, label)
            if wo:
                auto_wo.append(wo.id)
    if commit:
        db.commit()
    return {
        "added": len(created),
        "skipped": len(skipped),
        "skipped_items": skipped,
        "out_of_range": len(oor_events),
        "ids": [r.id for r in created],
        "entry_ids": list({r.entry_id for r in created}),
        "webhooks": hook_results,
        "work_orders": auto_wo,
    }


def reading_to_dict(r: Reading, tag: str | None = None) -> dict[str, Any]:
    date, time = split_ts(r.ts)
    an = r.annotation
    oor = out_of_range(r.value, r.limit_min, r.limit_max)
    filled = bool(an and (an.cause or an.action or an.annotated_by))
    return {
        "id": r.id,
        "entry_id": r.entry_id,
        "equipment_id": r.equipment_id,
        "tag": tag,
        "parameter": r.parameter_key,
        "ts": r.ts,
        "date": date,
        "time": time,
        "value": r.value,
        "remarks": r.remarks or "",
        "entered_by": r.entered_by or "",
        "source": r.source,
        "min": r.limit_min,
        "max": r.limit_max,
        "out_of_range": oor,
        "unacknowledged": bool(oor and not filled),
        "annotation": None if not filled else {
            "id": an.id, "cause": an.cause or "", "action": an.action or "",
            "by": an.annotated_by or "", "at": an.annotated_at,
        },
    }


def query_readings(
    db: Session,
    *,
    equipment_id: str | None = None,
    tag: str | None = None,
    parameter: str | None = None,
    ts_from: str | None = None,
    ts_to: str | None = None,
    source: str | None = None,
    limit: int = 1000,
    offset: int = 0,
    principal=None,
) -> list[dict[str, Any]]:
    q = db.query(Reading).options(selectinload(Reading.annotation), selectinload(Reading.equipment))
    if principal and principal.scoped:
        equipment = apply_scope(db.query(Equipment.id), Equipment, principal)
        q = q.filter(Reading.equipment_id.in_(equipment.statement))
    if equipment_id:
        q = q.filter(Reading.equipment_id == equipment_id)
    if tag:
        eq = get_equipment(db, tag=tag)
        if not eq:
            return []
        q = q.filter(Reading.equipment_id == eq.id)
    if parameter:
        keys = {parameter, parameter.lower()}
        q = q.filter(Reading.parameter_key.in_(list(keys)))
    if ts_from:
        start = parse_ts(ts=ts_from) if ("T" in ts_from or len(ts_from) > 10) else (ts_from + "T00:00:00")
        q = q.filter(Reading.ts >= start)
    if ts_to:
        end = ts_to if ("T" in ts_to or len(ts_to) > 10) else (ts_to + "T23:59:59")
        q = q.filter(Reading.ts <= end)
    if source:
        q = q.filter(Reading.source == source)
    rows = q.order_by(Reading.ts.asc(), Reading.id.asc()).offset(offset).limit(min(limit, 10000)).all()
    return [reading_to_dict(r, r.equipment.tag if r.equipment else None) for r in rows]


def query_events(
    db: Session,
    *,
    unacknowledged: bool | None = None,
    equipment_id: str | None = None,
    tag: str | None = None,
    ts_from: str | None = None,
    ts_to: str | None = None,
    limit: int = 500,
    offset: int = 0,
    principal=None,
) -> list[dict[str, Any]]:
    q = db.query(Reading).options(selectinload(Reading.annotation), selectinload(Reading.equipment))
    if principal and principal.scoped:
        equipment = apply_scope(db.query(Equipment.id), Equipment, principal)
        q = q.filter(Reading.equipment_id.in_(equipment.statement))
    # Use the denormalised flag so ix_readings_oor_ts can satisfy recent-event lists.
    q = q.filter(Reading.out_of_range == 1)
    if equipment_id:
        q = q.filter(Reading.equipment_id == equipment_id)
    if tag:
        eq = get_equipment(db, tag=tag)
        if not eq:
            return []
        q = q.filter(Reading.equipment_id == eq.id)
    if ts_from:
        start = parse_ts(ts=ts_from) if ("T" in ts_from or len(ts_from) > 10) else (ts_from + "T00:00:00")
        q = q.filter(Reading.ts >= start)
    if ts_to:
        end = ts_to if ("T" in ts_to or len(ts_to) > 10) else (ts_to + "T23:59:59")
        q = q.filter(Reading.ts <= end)
    if unacknowledged is True:
        q = q.outerjoin(Annotation, Annotation.reading_id == Reading.id).filter(
            or_(
                Annotation.id.is_(None),
                and_(
                    func.coalesce(Annotation.cause, "") == "",
                    func.coalesce(Annotation.action, "") == "",
                    func.coalesce(Annotation.annotated_by, "") == "",
                ),
            )
        )
    elif unacknowledged is False:
        q = q.join(Annotation, Annotation.reading_id == Reading.id).filter(
            or_(Annotation.cause != "", Annotation.action != "", Annotation.annotated_by != "")
        )
    rows = q.order_by(Reading.ts.desc(), Reading.id.desc()).offset(offset).limit(min(limit, 10000)).all()
    return [reading_to_dict(r, r.equipment.tag if r.equipment else None) for r in rows]


def upsert_annotation(db: Session, reading_id: str, data: dict[str, Any], *, actor: str = "ui") -> Annotation:
    r = db.get(Reading, reading_id)
    if not r:
        raise KeyError("reading not found")
    now = utcnow_iso()
    an = r.annotation
    if not an:
        an = Annotation(id=uid("an"), reading_id=r.id, created_at=now, annotated_at=data.get("at") or now)
        db.add(an)
    an.cause = data.get("cause") or ""
    an.action = data.get("action") or ""
    an.annotated_by = data.get("by") or data.get("annotated_by") or ""
    an.annotated_at = data.get("at") or data.get("annotated_at") or now
    audit(db, "annotate", actor=actor, entity_type="reading", entity_id=r.id)
    db.commit()
    db.refresh(an)
    return an


def delete_entry(db: Session, entry_id: str) -> int:
    return db.query(Reading).filter_by(entry_id=entry_id).delete()


def clear_all(db: Session, *, actor: str = "ui") -> None:
    # order respects FKs; cascade covers most. Users/roles/sessions are kept.
    from logentra_server.models import (
        ApprovalRoute,
        ApprovalRouteStep,
        AutoWorkOrderRule,
        Document,
        DocumentApprovalStep,
        DocumentComment,
        DocumentVersion,
        WorkOrder,
        WorkOrderAttachment,
        WorkOrderComment,
        WorkOrderLabour,
    )
    advance_catalog(db)
    if db.query(Signoff).first() or db.query(Document).filter(
        or_(Document.locked == 1, Document.status == "approved")
    ).first():
        raise HTTPException(409, "This operation would remove signed or approved evidence")
    db.query(DocumentComment).delete()
    db.query(DocumentApprovalStep).delete()
    db.query(DocumentVersion).delete()
    db.query(Document).delete()
    db.query(ApprovalRouteStep).delete()
    db.query(ApprovalRoute).delete()
    db.query(WorkOrderLabour).delete()
    db.query(WorkOrderAttachment).delete()
    db.query(WorkOrderComment).delete()
    db.query(WorkOrder).delete()
    db.query(AutoWorkOrderRule).delete()
    db.query(Annotation).delete()
    db.query(Reading).delete()
    db.query(RecordValue).delete()
    db.query(RecordItem).delete()
    db.query(RecordColumn).delete()
    db.query(Signoff).delete()
    db.query(Punch).delete()
    db.query(Record).delete()
    db.query(TemplateItem).delete()
    db.query(TemplateColumn).delete()
    db.query(Template).delete()
    db.query(Parameter).delete()
    db.query(ParamPreset).delete()
    db.query(EquipmentFieldValue).delete()
    db.query(Equipment).delete()
    db.query(FieldDefinition).delete()
    db.query(Setting).delete()
    audit(db, "clear", actor=actor, entity_type="database")
    db.flush()
    set_setting(db, "catalog_revision", uid("catalog"))
    ensure_default_fields(db)
    from logentra_server import seed as _seed
    _seed.ensure_default_routes(db)
    db.commit()
