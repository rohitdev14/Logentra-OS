"""Handover state transitions, immutable packages and notifications."""
import json
import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Event, Thread

from fastapi import HTTPException
from sqlalchemy import update

from logentra_server import events, service, shifts
from logentra_server.models import Annotation, AuditLog, Equipment, Parameter, Punch, Reading, Record, WorkOrder
from logentra_server.routers.shift_log import latest_entries
from logentra_server.routers.shifts import settings_for
from logentra_server.shift_models import Handover, HandoverComment, HandoverItem, Shift, ShiftDefinition, ShiftLogCategory, ShiftLogEntry, ShiftPattern
from logentra_server import sitetime
from logentra_server.utils import parse_ts, uid
from logentra_server.workorders import OPENISH

log = logging.getLogger("logentra.handover")
EDITABLE = {"draft"}


def lock_shift(db, shift_id):
    # A no-op write serializes state transitions on both supported databases.
    db.flush()
    db.execute(update(Shift).where(Shift.id == shift_id).values(status=Shift.status),
               execution_options={"synchronize_session": False})
    db.expire_all()


def require_lead(principal, shift, *, allow_manager=True):
    if principal.mode == "open" or (allow_manager and principal.can("shifts.manage")):
        return
    principal.require("handover.act")
    if not shift or not principal.user_id or principal.user_id != shift.lead_user_id:
        raise HTTPException(403, "This action requires the rostered shift lead")


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
    timestamp = shifts.utc_text(now or datetime.now(timezone.utc))
    return db.query(Shift.id).filter(
        Shift.site_id == handover.site_id, Shift.area_id == handover.area_id,
        Shift.lead_user_id == principal.user_id, Shift.id != handover.outgoing_shift_id,
        Shift.starts_at <= timestamp, Shift.ends_at > timestamp,
    ).first() is not None


def require_incoming(db, principal, handover, *, allow_manager=True):
    if not can_act_incoming(db, principal, handover, allow_manager=allow_manager):
        raise HTTPException(403, "This action requires the rostered incoming lead")


def allowed(db, principal, row, *, incoming=False, allow_manager=True):
    shifts.scoped(principal, row.site_id, row.area_id)
    if incoming:
        require_incoming(db, principal, row, allow_manager=allow_manager)
    else:
        require_lead(principal, db.get(Shift, row.outgoing_shift_id), allow_manager=allow_manager)


def require_state(handover, states):
    if handover.status not in states:
        raise HTTPException(409, f"Handover is {handover.status.replace('_', ' ')}")


def audit(db, principal, name, action, handover, detail=None):
    service.audit(db, f"handover.{action}", actor=name, user_id=principal.user_id if principal else None,
                  entity_type="handover", entity_id=handover.id, detail=detail)


def audit_opened(db, principal, name, handover, *, locked=False):
    query = db.query(AuditLog.id).filter_by(
        action="handover.opened", entity_type="handover", entity_id=handover.id, user_id=principal.user_id,
    )
    if principal.user_id is None:
        query = query.filter_by(actor=name)
    if query.first():
        return
    if not locked:
        lock_shift(db, handover.outgoing_shift_id)
        if query.first():
            return
    audit(db, principal, name, "opened", handover)


def notify(db, handover, event, **detail):
    events.enqueue(db, f"handover.{event}", {
        "handover_id": handover.id, "version": handover.version, "site_id": handover.site_id,
        "area_id": handover.area_id, "outgoing_shift_id": handover.outgoing_shift_id,
        "incoming_shift_id": handover.incoming_shift_id, **detail,
    })


def active_version(db, shift_id):
    return db.query(Handover).filter_by(outgoing_shift_id=shift_id).order_by(Handover.version.desc()).first()


def draft(db, shift, now, principal=None):
    current = active_version(db, shift.id)
    if current:
        return current
    incoming = shifts.next_shift(db, shift, now)
    row = Handover(id=uid("handover"), outgoing_shift_id=shift.id, site_id=shift.site_id, area_id=shift.area_id,
                   incoming_shift_id=incoming.id if incoming else None, version=1, summary="", status="draft")
    db.add(row)
    if shift.status == "active":
        shift.status = "handover_draft"
    db.flush()
    return row


def live_candidates(db, shift, now=None):
    now = now or datetime.now(timezone.utc)
    config = settings_for(db, shift.site_id)
    cutoff = parse_ts(ts=shifts.utc_text(now - timedelta(days=config.reading_lookback_days)))
    area = (shift.site_id, shift.area_id)
    definition = db.get(ShiftDefinition, shift.shift_definition_id)
    zone = shifts.timezone_for(db.get(ShiftPattern, definition.pattern_id).timezone)
    oor = db.query(Reading, Equipment.tag, Parameter.label, Parameter.unit).select_from(Reading).join(Equipment, Equipment.id == Reading.equipment_id).outerjoin(
        Parameter, (Parameter.equipment_id == Reading.equipment_id) & (Parameter.key == Reading.parameter_key)
    ).outerjoin(Annotation, Annotation.reading_id == Reading.id).filter(
        Equipment.site_id == area[0], Equipment.area_id == area[1], Reading.out_of_range == 1,
        Annotation.id.is_(None), Reading.ts >= cutoff, Reading.ts <= parse_ts(ts=shifts.utc_text(now)),
    ).order_by(Reading.ts.desc(), Reading.id)
    for reading, tag, label, unit in oor:
        _, clock = sitetime.utc_text_to_local(reading.ts, zone)
        value = f"{reading.value:g} {unit}" if unit else f"{reading.value:g}"
        detail = f"{value} at {clock}"
        if reading.limit_min is not None and reading.value < reading.limit_min:
            detail += f" (limit min {reading.limit_min:g})"
        elif reading.limit_max is not None and reading.value > reading.limit_max:
            detail += f" (limit max {reading.limit_max:g})"
        if reading.remarks:
            detail += f". {reading.remarks}"
        yield ("oor_reading", reading.id, f"{tag} · {label or reading.parameter_key}",
               detail, "high", True, sitetime.utc_iso_z(reading.ts), None)
    orders = db.query(WorkOrder).filter(
        WorkOrder.site_id == area[0], WorkOrder.area_id == area[1],
        WorkOrder.priority.in_(("urgent", "high")), WorkOrder.status.in_(OPENISH),
    ).order_by(WorkOrder.created_at.desc(), WorkOrder.id)
    for row in orders:
        yield ("work_order", row.id, row.title, row.description or "", row.priority, True, row.created_at, None)
    punches = db.query(Punch, Equipment.tag).select_from(Punch).join(Record, Record.id == Punch.record_id).join(
        Equipment, Equipment.id == Record.equipment_id
    ).filter(Equipment.site_id == area[0], Equipment.area_id == area[1],
             Punch.status.in_(("Open", "In progress"))).order_by(Punch.created_at.desc(), Punch.id)
    for row, tag in punches:
        yield ("punch", row.id, f"{tag} · Open punch", row.description, row.priority or "medium", True, row.raised_at or row.created_at, None)
    logged = latest_entries(db.query(ShiftLogEntry).filter(
        ShiftLogEntry.shift_id == shift.id, ShiftLogEntry.in_handover.is_(True), ShiftLogEntry.voided_at.is_(None)
    )).order_by(ShiftLogEntry.event_at.desc(), ShiftLogEntry.id)
    for row in logged:
        category = db.get(ShiftLogCategory, row.category_id)
        yield ("log_entry", row.id, row.title, row.body, row.severity, False, row.event_at, category.name if category else None)


def add_item(db, handover, kind, ref_id, title, detail, severity, auto, order, principal=None, name="system", *, event_at=None, category=None):
    item = HandoverItem(id=uid("handover_item"), handover_id=handover.id, kind=kind, ref_id=ref_id,
                        title=title, detail=detail, severity=severity, auto=auto, sort_order=order,
                        event_at=event_at, category=category)
    db.add(item)
    audit(db, principal, name, "item_added", handover, {"item_id": item.id, "kind": kind, "ref_id": ref_id})
    return item


def sync_items(db, handover, shift, now=None):
    require_state(handover, EDITABLE)
    existing = {(row.kind, row.ref_id): row for row in db.query(HandoverItem).filter_by(handover_id=handover.id)
                if row.kind != "note"}
    retained = set()
    for index, (kind, ref_id, title, detail, severity, auto, event_at, category) in enumerate(live_candidates(db, shift, now)):
        key = kind, ref_id
        retained.add(key)
        item = existing.get(key)
        if item is None:
            add_item(db, handover, kind, ref_id, title, detail, severity, auto, index, event_at=event_at, category=category)
        elif not item.removed_manually:
            item.title, item.detail, item.severity = title, detail, severity
            item.event_at, item.category = event_at, category
            item.removed_at = None
    now = shifts.utc_text(datetime.now(timezone.utc))
    for key, item in existing.items():
        if key not in retained and not item.removed_at and not item.pinned:
            item.removed_at = now
            audit(db, None, "system", "item_removed", handover,
                  {"item_id": item.id, "reason": "source no longer open"})
    db.flush()


def items_for(db, handover, *, removed=False):
    query = db.query(HandoverItem).filter_by(handover_id=handover.id)
    if not removed:
        query = query.filter(HandoverItem.removed_at.is_(None))
    return query.order_by(HandoverItem.sort_order, HandoverItem.id).all()


def item_for(db, handover, item_id):
    row = shifts.load(db, HandoverItem, item_id, "Handover item")
    if row.handover_id != handover.id or row.removed_at:
        raise HTTPException(404, "Handover item not found")
    return row


def describe(db, handover, principal=None):
    outgoing = db.get(Shift, handover.outgoing_shift_id)
    incoming = db.get(Shift, handover.incoming_shift_id) if handover.incoming_shift_id else None
    comments = db.query(HandoverComment).filter_by(handover_id=handover.id).order_by(
        HandoverComment.created_at, HandoverComment.id
    )
    versions = db.query(Handover).filter_by(outgoing_shift_id=outgoing.id).order_by(Handover.version)
    items = [shifts.columns(row) for row in items_for(db, handover)]
    audit_events = db.query(AuditLog).filter_by(entity_type="handover", entity_id=handover.id).order_by(
        AuditLog.ts.desc(), AuditLog.id.desc()
    ).limit(50)
    config = settings_for(db, handover.site_id)
    return {
        **shifts.columns(handover), "outgoing": shifts.describe_shift(db, outgoing),
        "incoming": shifts.describe_shift(db, incoming) if incoming else None, "items": items,
        "comments": [shifts.columns(comment) for comment in comments],
        "ack_window_minutes": config.ack_within_minutes, "escalation_role": config.escalate_to_role,
        "audit_events": [{"action": entry.action, "actor": entry.actor, "at": entry.ts}
                         for entry in audit_events],
        "can_edit": bool(principal and can_act(principal, outgoing)),
        "can_acknowledge": bool(principal and can_act_incoming(db, principal, handover)),
        "can_return": bool(principal and can_act_incoming(db, principal, handover, allow_manager=False)),
        "acknowledged_after_start": bool(incoming and handover.acknowledged_at
                                        and handover.acknowledged_at > incoming.starts_at),
        "versions": [{key: getattr(row, key) for key in
                      ("id", "version", "status", "return_reason", "snapshot_sha256")} for row in versions],
        "late": bool(handover.acknowledged_at and handover.ack_due_at
                     and handover.acknowledged_at > handover.ack_due_at),
    }


def sign_off(db, handover, principal, now, name):
    require_state(handover, EDITABLE)
    outgoing = db.get(Shift, handover.outgoing_shift_id)
    sync_items(db, handover, outgoing, now)
    incoming = shifts.next_shift(db, outgoing, now)
    config = settings_for(db, handover.site_id)
    start = datetime.fromisoformat(incoming.starts_at) if incoming else now
    handover.incoming_shift_id = incoming.id if incoming else None
    handover.ack_due_at = shifts.utc_text(start + timedelta(minutes=config.ack_within_minutes))
    handover.signed_off_at = shifts.utc_text(now)
    handover.signed_off_by = principal.user_id
    handover.signed_off_name = name
    frozen = {
        "version": handover.version, "summary": handover.summary, "signed_off_by": name,
        "signed_off_at": handover.signed_off_at, "outgoing": shifts.describe_shift(db, outgoing),
        "incoming": shifts.describe_shift(db, incoming) if incoming else None,
        "items": [shifts.columns(row) for row in items_for(db, handover)],
    }
    handover.snapshot_json = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    handover.snapshot_sha256 = sha256(handover.snapshot_json.encode()).hexdigest()
    handover.status = outgoing.status = "signed_off"
    outgoing.closed_at = handover.signed_off_at
    audit(db, principal, name, "signed_off", handover, {"snapshot_sha256": handover.snapshot_sha256})
    shifts.audited(db, principal, "shift.closed", "shift", outgoing.id)
    notify(db, handover, "signed_off", snapshot_sha256=handover.snapshot_sha256)
    if not incoming:
        escalate(db, handover, now, config, reason="no_incoming_roster")
    db.flush()


def return_handover(db, handover, principal, now, name, reason):
    require_state(handover, {"signed_off"})
    handover.status = "returned"
    handover.returned_at = shifts.utc_text(now)
    handover.returned_by = principal.user_id
    handover.returned_name = name
    handover.return_reason = reason
    outgoing = db.get(Shift, handover.outgoing_shift_id)
    outgoing.status = "returned"
    replacement = Handover(id=uid("handover"), outgoing_shift_id=outgoing.id,
                           incoming_shift_id=handover.incoming_shift_id, site_id=handover.site_id,
                           area_id=handover.area_id, version=handover.version + 1, status="draft",
                           summary=handover.summary, reminder_sent_at=handover.reminder_sent_at)
    db.add(replacement)
    db.flush()
    for item in items_for(db, handover, removed=True):
        values = shifts.columns(item)
        values.update(id=uid("handover_item"), handover_id=replacement.id, seen_at=None, seen_by=None)
        db.add(HandoverItem(**values))
    audit(db, principal, name, "returned", handover, {"reason": reason, "draft_id": replacement.id})
    notify(db, handover, "returned", reason=reason)
    db.flush()
    return replacement


def acknowledge(db, handover, principal, now, name, comment):
    require_state(handover, {"signed_off"})
    unseen = [row.id for row in items_for(db, handover) if not row.seen_at]
    handover.unseen_count = len(unseen)
    handover.acknowledged_at = shifts.utc_text(now)
    handover.acknowledged_by = principal.user_id
    handover.acknowledged_name = name
    handover.ack_comment = comment
    handover.status = "acknowledged"
    db.get(Shift, handover.outgoing_shift_id).status = "acknowledged"
    audit(db, principal, name, "acknowledged", handover,
          {"unseen_count": len(unseen), "unseen_items": unseen})
    notify(db, handover, "acknowledged", unseen_count=len(unseen))
    db.flush()


def escalate(db, handover, now, config, *, reason):
    if handover.escalated_at:
        return
    handover.escalated_at = shifts.utc_text(now)
    handover.escalated_to = config.escalate_to_role
    detail = {"role": handover.escalated_to, "reason": reason}
    audit(db, None, "system", "escalated", handover, detail)
    notify(db, handover, "escalated", **detail)


def process_notifications(db, now):
    results = {"reminded": 0, "escalated": 0}
    current = shifts.current_shifts(db, now)
    for row in sorted(current, key=lambda shift: shift.id):
        lock_shift(db, row.id)
        config = settings_for(db, row.site_id)
        end = datetime.fromisoformat(row.ends_at)
        if now < end - timedelta(minutes=config.signoff_reminder_minutes):
            continue
        handover = draft(db, row, now)
        if handover.status != "draft" or handover.reminder_sent_at:
            continue
        handover.reminder_sent_at = shifts.utc_text(now)
        detail = {"lead_user_id": row.lead_user_id, "ends_at": row.ends_at}
        audit(db, None, "system", "reminder_sent", handover, detail)
        notify(db, handover, "reminder", **detail)
        results["reminded"] += 1
    overdue = db.query(Handover).filter(Handover.status == "signed_off", Handover.escalated_at.is_(None),
                                       Handover.ack_due_at < shifts.utc_text(now)).all()
    for row in overdue:
        lock_shift(db, row.outgoing_shift_id)
        if row.status != "signed_off" or row.escalated_at:
            continue
        escalate(db, row, now, settings_for(db, row.site_id), reason="acknowledgement_overdue")
        results["escalated"] += 1
    db.commit()
    return results


class EscalationWorker:
    def __init__(self, session_factory, clock=lambda: datetime.now(timezone.utc), interval=60):
        self.session_factory = session_factory
        self.clock = clock
        self.interval = interval
        self.stopped = Event()
        self.thread = Thread(target=self.run, name="handover-escalation", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=5)

    def run(self):
        while not self.stopped.is_set():
            try:
                with self.session_factory() as db:
                    process_notifications(db, self.clock())
            except Exception:
                log.exception("Handover notification cycle failed")
            self.stopped.wait(self.interval)
