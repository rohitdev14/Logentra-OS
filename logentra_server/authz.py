"""Permission, ownership and scope rules shared by HTTP and service callers."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import or_

from logentra_server.models import (
    Annotation, Document, DocumentApprovalStep, Equipment, Punch, QREntry, Reading, Record, Role, Site, User, WorkOrder,
)
from logentra_server.route_policies import ROUTE_POLICIES
from logentra_server.shift_models import Handover, Shift, ShiftLogEntry

GLOBAL_PERMISSIONS = frozenset({"users.manage", "settings.manage", "audit.read", "webhooks.manage", "templates.write"})


def require(principal, *permissions, scoped=False):
    if not scoped or GLOBAL_PERMISSIONS.intersection(permissions):
        require_global(principal)
    missing = [name for name in permissions if not principal.can(name)]
    if missing:
        raise HTTPException(403, f"Missing permission: {', '.join(missing)}")
    return principal


def require_route(principal, method, path, *permissions, scoped=False):
    """Apply declared requirements alongside the existing dependency arguments."""
    policy = ROUTE_POLICIES.get((method, path))
    if policy is None:
        return require(principal, *permissions, scoped=scoped)
    requirements = tuple(dict.fromkeys((*permissions, *policy.permissions)))
    return require(principal, *requirements, scoped=scoped and policy.scoped)


def require_global(principal):
    if principal.scoped:
        raise HTTPException(403, "This operation requires unscoped access")


def allows_location(principal, site_id, area_id=None):
    return (
        (not principal.site_id or principal.site_id == site_id)
        and (not principal.area_id or principal.area_id == area_id)
    )


def require_location(principal, site_id, area_id=None, *, detail="You are not allowed to access this area"):
    if not allows_location(principal, site_id, area_id):
        raise HTTPException(403, detail)


def require_any(principal, first, *others):
    if not any(principal.can(permission) for permission in (first, *others)):
        require(principal, first, scoped=True)


def require_work_order_edit(principal):
    require_any(principal, "workorders.create", "workorders.assign")


def require_work_order_transition(principal, status):
    if str(status).lower() in ("closed", "close"):
        require(principal, "workorders.close", scoped=True)
    else:
        require_any(principal, "workorders.create", "workorders.assign", "workorders.close")


def check_work_order_assignee(db, principal, assignee):
    if assignee is not None:
        require(principal, "workorders.assign", scoped=True)
        if principal.scoped:
            require_object(db, principal, "user", assignee, "assign")


def scope_query(query, model, principal):
    """Limit a query to the caller's site/area when those are set on the user."""
    if principal is None:
        return query
    site_id = getattr(principal, "site_id", None)
    area_id = getattr(principal, "area_id", None)
    if site_id and (model is Site or hasattr(model, "site_id")):
        site_column = model.id if model is Site else model.site_id
        query = query.filter(site_column == site_id)
    if area_id and hasattr(model, "area_id"):
        query = query.filter(model.area_id == area_id)
    return query


def scope_evidence(db, query, model, principal):
    """Fail closed on historical links whose current scope disagrees with the parent."""
    query = scope_query(query, model, principal)
    if not principal or not principal.scoped:
        return query
    assets = scope_query(db.query(Equipment.id), Equipment, principal).statement
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
    orders = scoped_work(scope_query(db.query(WorkOrder.id), WorkOrder, principal)).statement
    return query.filter(permits(Document.equipment_id, assets), permits(Document.record_id, records),
                        permits(Document.work_order_id, orders))


def scoped_equipment_input(db, principal, payload):
    if not principal.scoped:
        return payload
    existing = db.get(Equipment, payload["id"]) if payload.get("id") else None
    if existing:
        require_object(db, principal, "equipment", existing, "access")
    old_site, old_area = (existing.site_id, existing.area_id) if existing else (principal.site_id, principal.area_id)
    site = payload.get("site_id", old_site)
    area = payload.get("area_id", old_area)
    require_location(principal, site, area, detail="Equipment location is outside your scope")
    if existing and (site, area) != (old_site, old_area):
        raise HTTPException(403, "Changing equipment location requires unscoped access")
    return {**payload, "site_id": site, "area_id": area}

def require_record_scope(db, principal, record_id, equipment_id=None):
    if not principal.scoped:
        return
    existing = db.get(Record, record_id) if record_id else None
    if existing:
        require_object(db, principal, "record", existing, "access")
    if existing and existing.status != "approved" and equipment_id and equipment_id != existing.equipment_id:
        raise HTTPException(403, "Moving records requires unscoped access")
    if not existing:
        equipment = db.get(Equipment, equipment_id) if equipment_id else None
        require_object(db, principal, "equipment", equipment, "access")


def require_lead(principal, shift, *, allow_manager=True):
    if principal.mode == "open" or (allow_manager and principal.can("shifts.manage")):
        return
    principal.require("handover.act")
    if not shift or not principal.user_id or principal.user_id != shift.lead_user_id:
        raise HTTPException(403, "This action requires the rostered shift lead")


def can_see_handover_notification(principal, handover, outgoing, incoming):
    if principal.mode == "open":
        return True
    if handover.status != "signed_off":
        return bool(principal.user_id and outgoing.lead_user_id == principal.user_id)
    return bool(
        incoming and principal.user_id and incoming.lead_user_id == principal.user_id
        or handover.escalated_to and principal.role_name == handover.escalated_to
        or not incoming and principal.can("shifts.manage")
    )


def can_act(principal, shift, *, allow_manager=True):
    return (
        principal.mode == "open"
        or (allow_manager and principal.can("shifts.manage"))
        or bool(shift and principal.can("handover.act") and principal.user_id
                and principal.user_id == shift.lead_user_id)
    )


def can_act_incoming(db, principal, handover, *, allow_manager=True, now=None):
    incoming = db.get(Shift, handover.incoming_shift_id) if handover.incoming_shift_id else None
    if can_act(principal, incoming, allow_manager=allow_manager):
        return True
    if incoming or not principal.can("handover.act") or not principal.user_id:
        return False
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return db.query(Shift.id).filter(
        Shift.site_id == handover.site_id, Shift.area_id == handover.area_id,
        Shift.lead_user_id == principal.user_id, Shift.id != handover.outgoing_shift_id,
        Shift.starts_at <= timestamp, Shift.ends_at > timestamp,
    ).first() is not None


def require_incoming(db, principal, handover, *, allow_manager=True):
    if not can_act_incoming(db, principal, handover, allow_manager=allow_manager):
        raise HTTPException(403, "This action requires the rostered incoming lead")


def can_act_on_step(principal, step: DocumentApprovalStep) -> bool:
    if principal.mode in ("open", "legacy_key"):
        return True
    if not principal.can("documents.approve"):
        return False
    if principal.role_name == "admin" or principal.can("users.manage"):
        return True
    if step.assignee_type == "user":
        return bool(principal.user_id and principal.user_id == step.user_id)
    if step.assignee_type == "role":
        return (principal.role_name or "").lower() == (step.role_name or "").lower()
    return False


def is_waiting_on(principal, step: DocumentApprovalStep) -> bool:
    """Whether this step is in this principal's approvals queue (not admin-wildcard)."""
    if principal.mode in ("open", "legacy_key"):
        return True
    if step.assignee_type == "user":
        return bool(principal.user_id and principal.user_id == step.user_id)
    if step.assignee_type == "role":
        return (principal.role_name or "").lower() == (step.role_name or "").lower()
    return False


OBJECTS = {
    "equipment": (Equipment, "Equipment"),
    "record": (Record, "Record"),
    "reading": (Reading, "Reading"),
    "work_order": (WorkOrder, "Work order"),
    "document": (Document, "Document"),
    "shift": (Shift, "Shift"),
    "shift_log_entry": (ShiftLogEntry, "Shift log entry"),
    "handover": (Handover, "Handover"),
    "qr_entry": (QREntry, "Quick entry"),
    "user": (User, "User"),
    "role": (Role, "Role"),
    "site": (Site, "Site"),
}

DOCUMENT_LINKS = {
    "equipment": ("equipment.read", "equipmentId"),
    "record": ("records.read", "recordId"),
    "work_order": ("workorders.read", "workOrderId"),
}


def require_object(db, principal, kind, obj_or_id, action):
    """Resolve an object and enforce its access rules after the route's permission check."""
    model, label = OBJECTS[kind]
    if action == "link_document":
        require(principal, DOCUMENT_LINKS[kind][0], scoped=True)
    elif kind == "role" or kind == "site" and action == "manage":
        require(principal, "users.manage")
    elif kind == "user" and action != "assign":
        identifier = obj_or_id.id if isinstance(obj_or_id, model) else obj_or_id
        if action != "self_or_manage" or principal.user_id != identifier:
            require(principal, "users.manage")

    identifier = obj_or_id if isinstance(obj_or_id, str) else None
    obj = db.get(model, identifier) if identifier is not None else obj_or_id
    hidden = {
        "link_document": "Linked object not found",
        "assign": "Assignee not found",
    }.get(action, f"{label} not found")
    if obj is None:
        raise HTTPException(404, hidden)

    if kind == "site":
        if principal.site_id and principal.site_id != obj.id:
            if action == "manage":
                raise HTTPException(404, hidden)
            raise HTTPException(403, "You are not allowed to access this site")
    elif kind == "user":
        if action == "assign" and (not obj.is_active or not allows_location(principal, obj.site_id, obj.area_id)):
            raise HTTPException(404, "Assignee not found")
    elif kind != "role":
        target = obj
        if kind in {"record", "reading"}:
            target = obj.equipment
        elif kind == "qr_entry":
            target = db.get(Equipment, obj.equipment_id)
        elif kind == "shift_log_entry":
            require_object(db, principal, "shift", obj.shift_id, "access")
            return obj

        if kind in {"shift", "handover"} or action == "qr_access":
            require_location(principal, target.site_id, target.area_id)
        elif principal.scoped and (target is None or not allows_location(principal, target.site_id, target.area_id)):
            raise HTTPException(404, hidden)
        if principal.scoped and kind in {"work_order", "document"}:
            if scope_evidence(db, db.query(model.id), model, principal).filter(model.id == obj.id).first() is None:
                raise HTTPException(404, hidden)

    if kind == "handover":
        if action == "outgoing":
            require_lead(principal, db.get(Shift, obj.outgoing_shift_id))
        elif action in {"incoming", "return"}:
            require_incoming(db, principal, obj, allow_manager=action != "return")
    elif kind == "work_order" and action == "delete":
        own_open = principal.can("workorders.create") and obj.status == "open" and obj.created_by_id == principal.user_id
        if not principal.can("users.manage") and not own_open:
            require(principal, "users.manage")
    return obj


def check_document_links(db, principal, links, *, existing=None):
    for kind, (_, alias) in DOCUMENT_LINKS.items():
        field = f"{kind}_id"
        for key in (field, alias):
            identifier = links.get(key)
            if identifier and (existing is None or identifier != getattr(existing, field)):
                require_object(db, principal, kind, identifier, "link_document")
