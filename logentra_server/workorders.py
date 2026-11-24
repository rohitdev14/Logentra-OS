"""Work-order lifecycle, comments, labour, attachments, and auto-create rules."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from logentra_server import events, service
from logentra_server.models import (
    AutoWorkOrderRule,
    Equipment,
    User,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderComment,
    WorkOrderLabour,
)
from logentra_server.pagination import clamp_page, page_result
from logentra_server.utils import uid, utcnow_iso

STATUSES = ("open", "in_progress", "on_hold", "done", "closed")
PRIORITIES = ("low", "medium", "high", "urgent")
OPENISH = ("open", "in_progress", "on_hold")

TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "on_hold", "done"},
    "in_progress": {"on_hold", "done", "open"},
    "on_hold": {"in_progress", "open"},
    "done": {"closed", "in_progress"},
    "closed": set(),
}


def _norm_status(value: str | None, default: str = "open") -> str:
    s = (value or default).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"inprogress": "in_progress", "hold": "on_hold", "complete": "done", "completed": "done"}
    s = aliases.get(s, s)
    if s not in STATUSES:
        raise ValueError(f"Invalid status {value!r}; expected one of {', '.join(STATUSES)}")
    return s


def _norm_priority(value: str | None, default: str = "medium") -> str:
    s = (value or default).strip().lower()
    if s not in PRIORITIES:
        raise ValueError(f"Invalid priority {value!r}; expected one of {', '.join(PRIORITIES)}")
    return s


def _user_name(db: Session, user_id: str | None, users_by_id=None) -> str | None:
    if not user_id:
        return None
    u = users_by_id.get(user_id) if users_by_id is not None else db.get(User, user_id)
    return (u.display_name or u.username) if u else None


def to_dict(wo: WorkOrder, db: Session | None = None, *, detail: bool = False,
            equipment_by_id=None, users_by_id=None) -> dict[str, Any]:
    tag = None
    if wo.equipment_id:
        # equipment may be gone
        from logentra_server.models import Equipment as Eq
        if db:
            eq = equipment_by_id.get(wo.equipment_id) if equipment_by_id is not None else db.get(Eq, wo.equipment_id)
            tag = eq.tag if eq else None
    out: dict[str, Any] = {
        "id": wo.id,
        "title": wo.title,
        "description": wo.description or "",
        "status": wo.status,
        "priority": wo.priority,
        "equipment_id": wo.equipment_id,
        "tag": tag,
        "site_id": wo.site_id,
        "area_id": wo.area_id,
        "assignee_id": wo.assignee_id,
        "assignee": _user_name(db, wo.assignee_id, users_by_id) if db else None,
        "created_by_id": wo.created_by_id,
        "created_by": _user_name(db, wo.created_by_id, users_by_id) if db else None,
        "due_at": wo.due_at,
        "started_at": wo.started_at,
        "completed_at": wo.completed_at,
        "closed_at": wo.closed_at,
        "linked_reading_id": wo.linked_reading_id,
        "linked_annotation_id": wo.linked_annotation_id,
        "linked_punch_id": wo.linked_punch_id,
        "linked_record_id": wo.linked_record_id,
        "parameter_key": wo.parameter_key,
        "labour_minutes": wo.labour_minutes or 0,
        "labour_notes": wo.labour_notes or "",
        "source": wo.source,
        "created_at": wo.created_at,
        "updated_at": wo.updated_at,
    }
    if detail:
        out["comments"] = [
            {
                "id": c.id,
                "user_id": c.user_id,
                "author": c.author or "",
                "body": c.body,
                "created_at": c.created_at,
            }
            for c in (wo.comments or [])
        ]
        out["attachments"] = [
            {
                "id": a.id,
                "filename": a.filename,
                "content_type": a.content_type,
                "size": a.size,
                "storage_path": a.storage_path,
                "uploaded_by": a.uploaded_by,
                "created_at": a.created_at,
            }
            for a in (wo.attachments or [])
        ]
        out["labour"] = [
            {
                "id": e.id,
                "user_id": e.user_id,
                "author": e.author or "",
                "minutes": e.minutes,
                "note": e.note or "",
                "worked_on": e.worked_on,
                "created_at": e.created_at,
            }
            for e in (wo.labour_entries or [])
        ]
    return out


def _scope(q, principal):
    if principal and getattr(principal, "site_id", None):
        q = q.filter(WorkOrder.site_id == principal.site_id)
    if principal and getattr(principal, "area_id", None):
        q = q.filter(WorkOrder.area_id == principal.area_id)
    return q


def list_work_orders(
    db: Session,
    *,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: str | None = None,
    equipment_id: str | None = None,
    q: str | None = None,
    page: int | None = 1,
    page_size: int | None = 50,
    principal=None,
) -> dict[str, Any]:
    page, page_size = clamp_page(page, page_size)
    query = db.query(WorkOrder)
    query = _scope(query, principal)
    if status:
        query = query.filter(WorkOrder.status == _norm_status(status, status))
    if priority:
        query = query.filter(WorkOrder.priority == _norm_priority(priority, priority))
    if assignee_id:
        query = query.filter(WorkOrder.assignee_id == assignee_id)
    if equipment_id:
        query = query.filter(WorkOrder.equipment_id == equipment_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(WorkOrder.title.ilike(like), WorkOrder.description.ilike(like), WorkOrder.id.ilike(like)))
    total = query.count()
    rows = (
        query.order_by(WorkOrder.created_at.desc(), WorkOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    equipment_ids = {row.equipment_id for row in rows if row.equipment_id}
    user_ids = {identifier for row in rows for identifier in (row.assignee_id, row.created_by_id) if identifier}
    # Keep the eager results alive while to_dict resolves objects through the identity map.
    equipment = db.query(Equipment).filter(Equipment.id.in_(equipment_ids)).all() if equipment_ids else []
    users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    equipment_by_id = {e.id: e for e in equipment}
    users_by_id = {u.id: u for u in users}
    items = [to_dict(row, db, equipment_by_id=equipment_by_id, users_by_id=users_by_id) for row in rows]
    return page_result(items, total, page, page_size)


def get_work_order(db: Session, wid: str) -> WorkOrder | None:
    return (
        db.query(WorkOrder)
        .options(
            selectinload(WorkOrder.comments),
            selectinload(WorkOrder.attachments),
            selectinload(WorkOrder.labour_entries),
        )
        .filter(WorkOrder.id == wid)
        .one_or_none()
    )


def create_work_order(
    db: Session,
    data: dict[str, Any],
    *,
    actor: str = "api",
    user_id: str | None = None,
    fire_webhooks: bool = True,
    source: str = "manual",
    commit: bool = True,
) -> WorkOrder:
    now = utcnow_iso()
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    status = _norm_status(data.get("status"), "open")
    wo = WorkOrder(
        id=data.get("id") or uid("wo"),
        title=title,
        description=data.get("description") or "",
        status=status,
        priority=_norm_priority(data.get("priority")),
        equipment_id=data.get("equipment_id") or data.get("equipmentId"),
        site_id=data.get("site_id") or data.get("siteId"),
        area_id=data.get("area_id") or data.get("areaId"),
        assignee_id=data.get("assignee_id") or data.get("assigneeId"),
        created_by_id=user_id or data.get("created_by_id"),
        due_at=data.get("due_at") or data.get("dueAt") or data.get("due_date"),
        linked_reading_id=data.get("linked_reading_id") or data.get("reading_id"),
        linked_annotation_id=data.get("linked_annotation_id") or data.get("annotation_id"),
        linked_punch_id=data.get("linked_punch_id") or data.get("punch_id"),
        linked_record_id=data.get("linked_record_id") or data.get("record_id"),
        parameter_key=data.get("parameter_key") or data.get("parameter"),
        labour_minutes=int(data["labour_minutes"]) if data.get("labour_minutes") not in (None, "") else 0,
        labour_notes=data.get("labour_notes") or "",
        source=data.get("source") or source,
        created_at=now,
        updated_at=now,
    )
    if wo.equipment_id and not wo.site_id:
        eq = db.get(Equipment, wo.equipment_id)
        if eq:
            wo.site_id = wo.site_id or eq.site_id
            wo.area_id = wo.area_id or eq.area_id
    if status == "in_progress":
        wo.started_at = now
    if status == "done":
        wo.completed_at = now
    if status == "closed":
        wo.closed_at = now
        wo.completed_at = wo.completed_at or now
    db.add(wo)
    db.flush()
    service.audit(db, "create", actor=actor, user_id=user_id, entity_type="work_order", entity_id=wo.id, detail={"title": wo.title})
    if fire_webhooks:
        events.enqueue(db, "workorder.created", {"event": "workorder.created", "at": now, "work_order": to_dict(wo, db, detail=True)})
    if commit:
        db.commit()
        db.refresh(wo)
    return wo


def apply_status(wo: WorkOrder, new_status: str, *, force: bool = False) -> None:
    new_status = _norm_status(new_status, new_status)
    if new_status == wo.status:
        return
    allowed = set(TRANSITIONS.get(wo.status, set()))
    if force:
        allowed = set(STATUSES)
    if new_status not in allowed:
        raise ValueError(f"Cannot transition from {wo.status} to {new_status}")
    now = utcnow_iso()
    wo.status = new_status
    wo.updated_at = now
    if new_status == "in_progress" and not wo.started_at:
        wo.started_at = now
    if new_status == "done":
        wo.completed_at = now
    if new_status == "closed":
        wo.closed_at = now
        wo.completed_at = wo.completed_at or now


def update_work_order(
    db: Session,
    wo: WorkOrder,
    data: dict[str, Any],
    *,
    actor: str = "api",
    user_id: str | None = None,
    force_status: bool = False,
    fire_webhooks: bool = True,
) -> WorkOrder:
    now = utcnow_iso()
    if "title" in data and data["title"] is not None:
        title = str(data["title"]).strip()
        if not title:
            raise ValueError("title is required")
        wo.title = title
    if "description" in data:
        wo.description = data.get("description") or ""
    if "priority" in data and data["priority"] is not None:
        wo.priority = _norm_priority(data.get("priority"))
    for src, attr in (
        ("equipment_id", "equipment_id"), ("equipmentId", "equipment_id"),
        ("site_id", "site_id"), ("area_id", "area_id"),
        ("due_at", "due_at"), ("dueAt", "due_at"), ("due_date", "due_at"),
        ("linked_reading_id", "linked_reading_id"),
        ("linked_annotation_id", "linked_annotation_id"),
        ("linked_punch_id", "linked_punch_id"),
        ("linked_record_id", "linked_record_id"),
        ("parameter_key", "parameter_key"), ("parameter", "parameter_key"),
        ("labour_notes", "labour_notes"),
    ):
        if src in data:
            setattr(wo, attr, data.get(src) or None if attr != "labour_notes" else (data.get(src) or ""))
    if "labour_minutes" in data and data["labour_minutes"] not in (None, ""):
        wo.labour_minutes = int(data["labour_minutes"])
    prev = wo.status
    if "status" in data and data["status"] is not None:
        apply_status(wo, data["status"], force=force_status)
    if "assignee_id" in data or "assigneeId" in data:
        wo.assignee_id = data.get("assignee_id") or data.get("assigneeId") or None
    wo.updated_at = now
    service.audit(db, "update", actor=actor, user_id=user_id, entity_type="work_order", entity_id=wo.id)
    if fire_webhooks:
        ev = "workorder.closed" if wo.status == "closed" and prev != "closed" else "workorder.updated"
        events.enqueue(db, ev, {"event": ev, "at": now, "work_order": to_dict(wo, db, detail=True)})
    db.commit()
    return get_work_order(db, wo.id) or wo


def add_comment(db: Session, wo: WorkOrder, body: str, *, author: str = "", user_id: str | None = None) -> WorkOrderComment:
    if not (body or "").strip():
        raise ValueError("comment body is required")
    row = WorkOrderComment(
        id=uid("woc"),
        work_order_id=wo.id,
        user_id=user_id,
        author=author or "",
        body=body.strip(),
        created_at=utcnow_iso(),
    )
    db.add(row)
    wo.updated_at = utcnow_iso()
    db.commit()
    db.refresh(row)
    return row


def add_labour(
    db: Session, wo: WorkOrder, minutes: int, *, note: str = "", author: str = "",
    user_id: str | None = None, worked_on: str | None = None,
) -> WorkOrderLabour:
    minutes = int(minutes)
    if minutes < 0:
        raise ValueError("minutes must be >= 0")
    row = WorkOrderLabour(
        id=uid("wol"),
        work_order_id=wo.id,
        user_id=user_id,
        author=author or "",
        minutes=minutes,
        note=note or "",
        worked_on=worked_on or utcnow_iso()[:10],
        created_at=utcnow_iso(),
    )
    db.add(row)
    wo.labour_minutes = (wo.labour_minutes or 0) + minutes
    if note:
        prev = wo.labour_notes or ""
        wo.labour_notes = (prev + "\n" if prev else "") + note
    wo.updated_at = utcnow_iso()
    db.commit()
    db.refresh(row)
    return row


def add_attachment(
    db: Session, wo: WorkOrder, *, filename: str, content_type: str | None = None,
    size: int | None = None, storage_path: str | None = None, uploaded_by: str | None = None,
) -> WorkOrderAttachment:
    if not filename:
        raise ValueError("filename is required")
    row = WorkOrderAttachment(
        id=uid("woa"),
        work_order_id=wo.id,
        filename=filename,
        content_type=content_type,
        size=size,
        storage_path=storage_path,
        uploaded_by=uploaded_by,
        created_at=utcnow_iso(),
    )
    db.add(row)
    wo.updated_at = utcnow_iso()
    db.commit()
    db.refresh(row)
    return row


def rule_to_dict(rule: AutoWorkOrderRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "equipment_id": rule.equipment_id,
        "parameter_key": rule.parameter_key,
        "enabled": bool(rule.enabled),
        "priority": rule.priority,
        "title_template": rule.title_template or "",
        "description_template": rule.description_template or "",
        "assignee_id": rule.assignee_id,
        "cooldown_minutes": rule.cooldown_minutes,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def upsert_rule(db: Session, data: dict[str, Any], *, rule: AutoWorkOrderRule | None = None) -> AutoWorkOrderRule:
    now = utcnow_iso()
    if rule is None:
        rule = AutoWorkOrderRule(
            id=data.get("id") or uid("wor"),
            created_at=now,
            updated_at=now,
            enabled=0,
            priority="medium",
            cooldown_minutes=1440,
        )
        db.add(rule)
    if "equipment_id" in data or rule.id:
        if "equipment_id" in data or not rule.equipment_id:
            rule.equipment_id = data.get("equipment_id") or rule.equipment_id
    if "parameter_key" in data or "parameter" in data:
        rule.parameter_key = data.get("parameter_key") or data.get("parameter") or rule.parameter_key
    if "enabled" in data:
        rule.enabled = 1 if data.get("enabled") else 0
    if data.get("priority"):
        rule.priority = _norm_priority(data.get("priority"))
    if "title_template" in data:
        rule.title_template = data.get("title_template") or ""
    if "description_template" in data:
        rule.description_template = data.get("description_template") or ""
    if "assignee_id" in data:
        rule.assignee_id = data.get("assignee_id") or None
    if "cooldown_minutes" in data and data["cooldown_minutes"] not in (None, ""):
        rule.cooldown_minutes = int(data["cooldown_minutes"])
    rule.updated_at = now
    db.commit()
    db.refresh(rule)
    return rule


def _rule_matches(rule: AutoWorkOrderRule, equipment_id: str, parameter_key: str) -> bool:
    if not rule.enabled:
        return False
    if rule.equipment_id and rule.equipment_id != equipment_id:
        return False
    if rule.parameter_key and rule.parameter_key != parameter_key:
        return False
    return True


def maybe_auto_create(db: Session, reading, tag: str, parameter_label: str | None = None) -> WorkOrder | None:
    """Create a work order from an out-of-range reading if a matching enabled rule exists."""
    rules = db.query(AutoWorkOrderRule).filter(AutoWorkOrderRule.enabled == 1).all()
    matched = [r for r in rules if _rule_matches(r, reading.equipment_id, reading.parameter_key)]
    if not matched:
        return None
    # prefer the most specific rule (has equipment_id, then parameter)
    matched.sort(key=lambda r: (0 if r.equipment_id else 1, 0 if r.parameter_key else 1))
    rule = matched[0]
    cooldown = max(int(rule.cooldown_minutes or 0), 0)
    if cooldown:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=cooldown)
        cutoff = cutoff_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        existing = (
            db.query(WorkOrder)
            .filter(
                WorkOrder.equipment_id == reading.equipment_id,
                WorkOrder.parameter_key == reading.parameter_key,
                WorkOrder.status.in_(OPENISH),
                WorkOrder.created_at >= cutoff,
            )
            .first()
        )
        if existing:
            return None
    else:
        existing = (
            db.query(WorkOrder)
            .filter(
                WorkOrder.equipment_id == reading.equipment_id,
                WorkOrder.parameter_key == reading.parameter_key,
                WorkOrder.status.in_(OPENISH),
            )
            .first()
        )
        if existing:
            return None
    ctx = {
        "tag": tag or "",
        "parameter": reading.parameter_key,
        "parameter_label": parameter_label or reading.parameter_key,
        "value": reading.value,
        "min": reading.limit_min,
        "max": reading.limit_max,
        "ts": reading.ts,
    }
    title_t = rule.title_template or "Out of range: {tag} {parameter} = {value}"
    desc_t = rule.description_template or "Auto-created from an out-of-range reading at {ts}."
    try:
        title = title_t.format(**ctx)
        desc = desc_t.format(**ctx)
    except (KeyError, IndexError, ValueError):
        title = f"Out of range: {tag} {reading.parameter_key} = {reading.value}"
        desc = f"Auto-created from an out-of-range reading at {reading.ts}."
    return create_work_order(
        db,
        {
            "title": title,
            "description": desc,
            "priority": rule.priority,
            "equipment_id": reading.equipment_id,
            "assignee_id": rule.assignee_id,
            "linked_reading_id": reading.id,
            "parameter_key": reading.parameter_key,
        },
        actor="system",
        source="auto_oor",
        fire_webhooks=True,
        commit=False,
    )
