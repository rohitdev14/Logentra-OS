from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, selectinload

from logbook_server import events
from logbook_server.models import (
    Annotation,
    AuditLog,
    Equipment,
    EquipmentFieldValue,
    FieldDefinition,
    OutboundWebhook,
    ParamPreset,
    Parameter,
    Punch,
    Reading,
    Record,
    RecordColumn,
    RecordItem,
    RecordValue,
    Setting,
    Signoff,
    WebhookDelivery,
    Template,
    TemplateColumn,
    TemplateItem,
)
from logbook_server.utils import as_float, dumps, loads, out_of_range, parse_ts, split_ts, uid, utcnow_iso

APP_VERSION = "0.3.0"


def audit(db: Session, action: str, *, actor: str | None = "api", entity_type: str | None = None,
          entity_id: str | None = None, detail: Any = None) -> None:
    db.add(AuditLog(
        id=uid("aud"), ts=utcnow_iso(), actor=actor, action=action,
        entity_type=entity_type, entity_id=entity_id, detail=dumps(detail) if detail is not None else None,
    ))


def default_fields(now: str | None = None) -> list[dict[str, Any]]:
    now = now or utcnow_iso()
    return [
        {"id": "fld_tag", "key": "tag", "label": "Tag", "type": "text", "locked": True, "options": [], "sort_order": 0},
        {"id": "fld_category", "key": "category", "label": "Category", "type": "text", "locked": False, "options": [], "sort_order": 1},
    ]


def ensure_default_fields(db: Session) -> None:
    if db.query(FieldDefinition).count():
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
        "createdAt": eq.created_at[:10] if eq.created_at else "",
    }


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
        "id": r.id, "templateId": r.template_id or "", "templateName": r.template_name or "",
        "kind": r.kind or "", "equipmentId": r.equipment_id or "", "createdAt": (r.created_at or "")[:10],
        "notes": r.notes or "", "columns": [{
            "id": c.id, "key": c.key, "label": c.label, "type": c.type,
            "options": loads(c.options, []) or [],
        } for c in cols],
        "items": items_out, "punches": punches,
        "signoffName": r.signoff.name if r.signoff else "",
        "signoffDate": r.signoff.signed_at[:10] if r.signoff else "",
    }


def logs_to_ui(db: Session) -> list[dict[str, Any]]:
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
    return [groups[k] for k in order]


def bootstrap(db: Session, *, backend: str = "sqlite") -> dict[str, Any]:
    ensure_default_fields(db)
    db.commit()
    fields = db.query(FieldDefinition).order_by(FieldDefinition.sort_order, FieldDefinition.key).all()
    equipment = db.query(Equipment).options(
        selectinload(Equipment.values), selectinload(Equipment.parameters)
    ).all()
    templates = db.query(Template).options(
        selectinload(Template.columns), selectinload(Template.items)
    ).all()
    records = db.query(Record).options(
        selectinload(Record.columns),
        selectinload(Record.items).selectinload(RecordItem.values),
        selectinload(Record.punches),
        selectinload(Record.signoff),
    ).all()
    presets: dict[str, list] = {}
    for p in db.query(ParamPreset).order_by(ParamPreset.category, ParamPreset.sort_order).all():
        presets.setdefault(p.category, []).append(param_to_ui(p))
    demo_flag = db.get(Setting, "demo")
    updated = db.get(Setting, "updated_at")
    return {
        "meta": {
            "version": 3,
            "app": APP_VERSION,
            "updatedAt": updated.value if updated else None,
            "demo": (demo_flag.value == "1") if demo_flag else False,
            "backend": backend,
            "store": "database",
        },
        "equipmentFields": [field_to_ui(f) for f in fields],
        "equipment": [equipment_to_ui(e) for e in equipment],
        "templates": [template_to_ui(t) for t in templates],
        "records": [record_to_ui(r) for r in records],
        "paramPresets": presets,
        "logs": logs_to_ui(db),
    }


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    now = utcnow_iso()
    if row:
        row.value = value
        row.updated_at = now
    else:
        db.add(Setting(key=key, value=value, updated_at=now))


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
        # unique tag: if conflict with another row, suffix
        other = db.query(Equipment).filter(Equipment.tag == tag, Equipment.id != eq.id).one_or_none()
        if other:
            tag = tag + "-" + eid[-4:]
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
            row.template_name = rec.get("templateName", row.template_name)
            row.kind = rec.get("kind", row.kind)
            row.equipment_id = rec.get("equipmentId", row.equipment_id) or row.equipment_id
            row.notes = rec.get("notes", row.notes)
            row.updated_at = now
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


def sync_catalog(db: Session, state: dict[str, Any], *, actor: str = "ui") -> dict[str, Any]:
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
    db.commit()
    return {"ok": True, "updatedAt": utcnow_iso()}


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
) -> dict[str, Any]:
    """Insert many readings. Each item: tag or equipment_id, parameter, value, optional ts/date/time, remarks, entered_by, entry_id, unit, label."""
    ensure_default_fields(db)
    created = []
    skipped = []
    oor_events = []
    fields = db.query(FieldDefinition).all()
    tag_field = next((f for f in fields if f.key == "tag"), None)
    now = utcnow_iso()
    for raw in items:
        tag = (raw.get("tag") or raw.get("equipment") or "").strip()
        eid = raw.get("equipment_id") or raw.get("equipmentId")
        eq = get_equipment(db, equipment_id=eid, tag=tag or None)
        if not eq:
            if create_missing_equipment and tag:
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
            db.query(Parameter).filter_by(equipment_id=eq.id, key=key_raw).one_or_none()
            or db.query(Parameter).filter(Parameter.equipment_id == eq.id, Parameter.label == key_raw).one_or_none()
        )
        key = param.key if param else key_raw
        if not param:
            param = ensure_parameter(db, eq.id, key, label=raw.get("label") or key_raw, unit=raw.get("unit"))
        value = as_float(raw.get("value") if "value" in raw else raw.get("reading"))
        if value is None:
            skipped.append({"reason": "invalid_value", "item": raw})
            continue
        ts = parse_ts(raw.get("date"), raw.get("time"), raw.get("ts") or raw.get("timestamp"))
        entry_id = raw.get("entry_id") or raw.get("entryId") or uid("log")
        reading = Reading(
            id=raw.get("id") or uid("rd"),
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
    set_setting(db, "updated_at", now)
    db.commit()
    hook_results = []
    if fire_webhooks and oor_events:
        for reading, tag, label in oor_events:
            hook_results.extend(events.deliver(db, "out_of_range", events.oor_payload(reading, tag, label)))
    return {
        "added": len(created),
        "skipped": len(skipped),
        "skipped_items": skipped,
        "out_of_range": len(oor_events),
        "ids": [r.id for r in created],
        "entry_ids": list({r.entry_id for r in created}),
        "webhooks": hook_results,
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
) -> list[dict[str, Any]]:
    q = db.query(Reading).options(selectinload(Reading.annotation), selectinload(Reading.equipment))
    if equipment_id:
        q = q.filter(Reading.equipment_id == equipment_id)
    if tag:
        eq = get_equipment(db, tag=tag)
        if not eq:
            return []
        q = q.filter(Reading.equipment_id == eq.id)
    if parameter:
        q = q.filter(or_(Reading.parameter_key == parameter, Reading.parameter_key == parameter.lower()))
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
) -> list[dict[str, Any]]:
    rows = query_readings(
        db, equipment_id=equipment_id, tag=tag, ts_from=ts_from, ts_to=ts_to, limit=limit,
    )
    out = [r for r in rows if r["out_of_range"]]
    if unacknowledged is True:
        out = [r for r in out if r["unacknowledged"]]
    elif unacknowledged is False:
        out = [r for r in out if not r["unacknowledged"]]
    return out


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
    n = db.query(Reading).filter_by(entry_id=entry_id).delete()
    db.commit()
    return n


def clear_all(db: Session, *, actor: str = "ui") -> None:
    # order respects FKs; cascade covers most
    db.query(WebhookDelivery).delete()
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
    db.commit()
    ensure_default_fields(db)
    db.commit()
