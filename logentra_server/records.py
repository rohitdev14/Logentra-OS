"""Record approval and audit trail."""

from collections import Counter
from datetime import date, datetime, timedelta

from fastapi import HTTPException
from pydantic import BaseModel, Field

from logentra_server import events, service
from logentra_server.models import AuditLog, Record, Signoff, User
from logentra_server.sitetime import utc_iso_z
from logentra_server.utils import loads, uid, utcnow_iso

LOCK_MESSAGES = {"submitted": "Record is waiting for approval", "approved": "Approved records are locked"}
REVISION_CONFLICT = "Record changed. Reload before saving."
OPEN_PUNCH_STATUSES = {"Open", "In progress"}
LABELS = {
    "create": "Created record",
    "revise": "Updated record",
    "delete": "Deleted record",
    "record.created": "Created record",
    "record.item_changed": "Changed checklist results",
    "record.notes_changed": "Edited notes",
    "record.punch_raised": "Raised punch",
    "record.punch_updated": "Updated punch",
    "record.punch_closed": "Closed punch",
    "record.punch_removed": "Removed punch",
    "record.submitted": "Submitted for approval",
    "record.returned": "Returned",
    "record.approved": "Approved",
    "record.signed": "Signed",
}


class ActionIn(BaseModel):
    revision: int = Field(ge=1)
    comment: str | None = Field(default=None, max_length=10000)


def display_name(user, fallback=""):
    return (user.display_name or user.username) if user else (fallback or "")


def audit(db, record, action, detail=None, *, actor, user_id):
    service.audit(db, f"record.{action}", actor=actor, user_id=user_id,
                  entity_type="record", entity_id=record.id, detail=detail)


def _transition(db, rid, principal, revision, action, comment):
    catalog = service.advance_catalog(db)
    record = get_scoped(db, rid, principal)
    if revision != record.revision:
        raise HTTPException(409, REVISION_CONFLICT)
    comment = (comment or "").strip()
    if action == "submit":
        if record.status not in ("draft", "returned"):
            raise HTTPException(409, "Only draft or returned records can be submitted")
        result_column = next((col for col in record.columns if col.type == "passfail"), None)
        if not record.items or any(
            not any(value.value and (result_column is None or value.column_id == result_column.id) for value in item.values)
            for item in record.items
        ):
            raise HTTPException(422, "Enter a result for every checklist item before submitting")
        status, step = "submitted", "submitted"
    else:
        if record.status != "submitted":
            raise HTTPException(409, "Only submitted records can be reviewed")
        if action == "approve" and principal.mode != "open" and principal.user_id == record.submitted_by_id:
            raise HTTPException(403, "You submitted this record; another approver must approve it")
        if action == "return" and not comment:
            raise HTTPException(422, "A comment is required")
        status, step = ("approved", "approved") if action == "approve" else ("returned", "returned")

    name = display_name(db.get(User, principal.user_id) if principal.user_id else None, principal.actor)
    now = utcnow_iso()
    record.status = status
    setattr(record, f"{step}_at", now)
    setattr(record, f"{step}_by", name)
    setattr(record, f"{step}_by_id", principal.user_id)
    record.updated_at = now
    record.revision += 1
    if action == "return":
        record.return_comment = comment
    if action == "approve":
        record.approval_comment = comment or None
    actor = {"actor": principal.actor, "user_id": principal.user_id}
    detail = {"comment": comment} if comment else {}
    audit(db, record, status, detail, **actor)
    if action == "approve":
        sign_record(db, record, name, now, **actor)
    events.enqueue(db, f"record.{status}", {
        "event": f"record.{status}", "at": now, "record_id": record.id,
        "equipment_id": record.equipment_id, "status": record.status, "revision": record.revision,
        "actor": name, **detail,
    })
    db.commit()
    return {**service.record_to_ui(record), "catalogRevision": catalog}


def submit_record(db, rid, principal, revision, comment=None):
    principal.require("records.write")
    return _transition(db, rid, principal, revision, "submit", comment)


def approve_record(db, rid, principal, revision, comment=None):
    principal.require("records.approve")
    return _transition(db, rid, principal, revision, "approve", comment)


def return_record(db, rid, principal, revision, comment):
    principal.require("records.approve")
    return _transition(db, rid, principal, revision, "return", comment)


def get_scoped(db, rid, principal):
    record = db.get(Record, rid)
    if not record:
        raise HTTPException(404, "Record not found")
    service.require_record_scope(db, principal, rid)
    return record


def sign_record(db, record, name, signed_at, *, actor, user_id):
    if record.signoff is None:
        record.signoff = Signoff(id=uid("sg"), name=name, signed_at=signed_at)
    else:
        record.signoff.name, record.signoff.signed_at = name, signed_at
    audit(db, record, "signed", {"name": name, "at": signed_at}, actor=actor, user_id=user_id)


def _is_closed_date(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except (TypeError, ValueError):
        return False


def close_approved_punches(db, record, payload, previous, old_fields, current, *, now, principal):
    locked = LOCK_MESSAGES["approved"]
    # The normal save path ignores these server-managed fields and the original raised date.
    server_fields = previous.keys() - current.keys() - {"id", "revision"}
    for key in server_fields | {"createdAt", "equipmentId"}:
        if key in payload and payload[key] != previous[key]:
            raise HTTPException(409, locked)
    if any(current[key] != old_fields[key] for key in current if key != "punches"):
        raise HTTPException(409, locked)

    original = {punch["id"]: punch for punch in old_fields["punches"]}
    updated = {punch["id"]: punch for punch in current["punches"]}
    if original.keys() != updated.keys() or len(updated) != len(current["punches"]):
        raise HTTPException(409, locked)
    for raw in payload.get("punches") or []:
        raised = original[raw["id"]]["raised"]
        if any(key in raw and (raw[key] or "") != raised for key in ("raised", "raised_at")):
            raise HTTPException(409, locked)

    closures = {}
    for identifier, new in updated.items():
        old = original[identifier]
        if old == new:
            continue
        if (old["status"] not in OPEN_PUNCH_STATUSES
                or new != {**old, "status": "Closed", "closed": new["closed"]}
                or not _is_closed_date(new["closed"])):
            raise HTTPException(409, locked)
        closures[new["id"]] = new["closed"]
    if not closures:
        return False
    if principal is None:
        raise HTTPException(409, locked)
    principal.require("records.write")
    if payload.get("revision") != record.revision:
        raise HTTPException(409, REVISION_CONFLICT)

    record.revision += 1
    record.updated_at = now
    for punch in record.punches:
        if punch.id in closures:
            punch.status = "Closed"
            punch.closed_at = closures[punch.id]
    audit_save(db, record, old_fields, current, actor=principal.user_id or principal.actor, user_id=principal.user_id)
    return True


def _result_cells(record):
    columns = {col["id"]: col["label"] for col in record.get("columns", [])}
    return {(item["id"], key): (item["label"], columns[key], value)
            for item in record.get("items", []) for key, value in item["values"].items()
            if key in columns and value != ""}


def audit_save(db, record, previous, current, *, actor, user_id):
    identity = {"actor": actor, "user_id": user_id}
    if previous is None:
        audit(db, record, "created", {"template": record.template_name or "",
                                      "equipment_tag": record.equipment.tag if record.equipment else ""}, **identity)
    elif previous["notes"] != current["notes"]:
        audit(db, record, "notes_changed", {
            "from_length": len(previous["notes"]), "to_length": len(current["notes"]),
        }, **identity)
    old_cells, new_cells = _result_cells(previous or {}), _result_cells(current)
    changes = []
    total = 0
    for key in dict.fromkeys((*new_cells, *old_cells)):
        old, new = old_cells.get(key), new_cells.get(key)
        old_value, new_value = old[2] if old else "", new[2] if new else ""
        if old_value != new_value:
            total += 1
            if len(changes) < 50:
                item, column, _ = new or old
                changes.append({"item": item, "column": column, "from": old_value, "to": new_value})
    if changes:
        detail = {"changes": changes}
        if total > len(changes):
            detail["truncated"] = total - len(changes)
        audit(db, record, "item_changed", detail, **identity)
    old_punches = {punch["id"]: punch for punch in (previous or {}).get("punches", [])}
    for punch in current["punches"]:
        identifier = punch["id"]
        old = old_punches.pop(identifier, None)
        if old is None:
            audit(db, record, "punch_raised", {"punch_id": identifier, "description": punch["description"],
                                               "priority": punch["priority"]}, **identity)
            continue
        fields = {key: {"from": old[key], "to": punch[key]} for key in ("status", "owner", "description", "priority")
                  if old[key] != punch[key]}
        if fields:
            action = "punch_closed" if "status" in fields and punch["status"] == "Closed" else "punch_updated"
            detail = {"punch_id": identifier, **fields}
            if action == "punch_closed":
                detail["punch_description"] = punch["description"]
                detail["closed"] = {"from": old["closed"], "to": punch["closed"]}
                if record.status == "approved":
                    name = display_name(db.get(User, user_id) if user_id else None, actor)
                    events.enqueue(db, "record.punch_closed", {
                        "event": "record.punch_closed", "at": record.updated_at, "record_id": record.id,
                        "equipment_id": record.equipment_id, "status": record.status, "revision": record.revision,
                        "actor": name, "punch_id": identifier, "description": punch["description"],
                        "closed": punch["closed"],
                    })
            audit(db, record, action, detail, **identity)
    for identifier, old in old_punches.items():
        audit(db, record, "punch_removed", {"punch_id": identifier, "description": old["description"]}, **identity)


def _event_label(action, detail):
    label = LABELS[action]
    if action == "record.item_changed":
        count = len(detail.get("changes", [])) + detail.get("truncated", 0)
        return f"Changed {count} item" + ("s" if count != 1 else "")
    if action in {"record.punch_raised", "record.punch_removed"} and detail.get("description"):
        return f"{label}: {detail['description']}"
    if action == "record.punch_closed" and detail.get("punch_description"):
        return f"{label}: {detail['punch_description']}"
    if action == "record.returned" and detail.get("comment"):
        return f"{label}: {detail['comment']}"
    return label


def _merge_item_changes(entries, record):
    """Combine nearby changes for display while preserving each stored audit row."""
    item_labels = Counter(item.label for item in record.items)
    column_labels = Counter(column.label for column in record.columns)
    trail = []
    newest = None
    identity = None
    cells = {}
    for timestamp, actor, entry in entries:
        if entry["action"] != "record.item_changed":
            newest = None
            trail.append(entry)
            continue

        changes = entry["detail"].get("changes", [])
        keys = [(change["item"], change["column"]) for change in changes]
        # Historical rows identify cells by label. Keep ambiguous rows intact.
        if len(keys) != len(set(keys)) or any(item_labels[item] > 1 or column_labels[column] > 1
                                              for item, column in keys):
            newest = None
            trail.append(entry)
            continue

        if newest is None or actor != identity or not actor[1] or newest - timestamp > timedelta(seconds=120):
            newest, identity = timestamp, actor
            cells = {}
            trail.append({**entry, "detail": {"changes": []}})

        detail = trail[-1]["detail"]
        for key, change in zip(keys, changes):
            if key in cells:
                cells[key]["from"] = change["from"]
            else:
                cells[key] = dict(change)
                detail["changes"].append(cells[key])
        if skipped := entry["detail"].get("truncated"):
            detail["truncated"] = detail.get("truncated", 0) + skipped

    for entry in trail:
        if entry["action"] == "record.item_changed":
            detail = entry["detail"]
            if len(detail["changes"]) > 50:
                detail["truncated"] = detail.get("truncated", 0) + len(detail["changes"]) - 50
                detail["changes"] = detail["changes"][:50]
            entry["label"] = _event_label(entry["action"], detail)
    return trail


def list_audit(db, record):
    rows = db.query(AuditLog, User).outerjoin(User, AuditLog.user_id == User.id).filter(
        AuditLog.entity_type == "record", AuditLog.entity_id == record.id,
        AuditLog.action.in_(LABELS),
    ).all()
    entries = []
    for row, user in rows:
        detail = loads(row.detail) or {}
        # Current revisions have a checklist audit event alongside the evidence snapshot.
        if row.action == "revise" and "status" in detail:
            continue
        identity = ("user", row.user_id) if row.user_id else ("actor", row.actor)
        entries.append((datetime.fromisoformat(row.ts), identity, {
            "id": row.id, "at": row.ts, "action": row.action, "label": _event_label(row.action, detail),
            "actor": row.actor, "actor_name": display_name(user, row.actor), "detail": detail,
        }))
    if record.signoff and not any(entry["action"] == "record.signed" for _, _, entry in entries):
        name, at = record.signoff.name, record.signoff.signed_at
        timestamp = utc_iso_z(at)
        entries.append((datetime.fromisoformat(timestamp), ("actor", name), {
            "id": record.signoff.id, "at": timestamp, "action": "record.signed",
            "label": "Signed (before 0.7.1)", "actor": name, "actor_name": name,
            "date_only": len(at) == 10, "detail": {"name": name, "at": at},
        }))
    entries.sort(key=lambda row: (row[0], row[2]["id"]), reverse=True)
    return _merge_item_changes(entries, record)
