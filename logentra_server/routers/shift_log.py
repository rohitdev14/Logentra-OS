"""Append-only shift log, comments, linked evidence and attachments."""
import re
from datetime import date, datetime
from hashlib import sha256
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, aliased

from logentra_server import qr_entries, service, shifts
from logentra_server.documents import safe_filename
from logentra_server.http_security import read_upload
from logentra_server.models import Equipment, Punch, Reading, WorkOrder
from logentra_server.pagination import count_rows, page_result
from logentra_server.shift_models import (
    Handover, HandoverItem, Shift, ShiftLogAttachment, ShiftLogCategory, ShiftLogComment, ShiftLogEntry,
)
from logentra_server.upload_safety import detect_upload_mime
from logentra_server.utils import uid, utcnow_iso

Severity = Literal["info", "low", "medium", "high", "critical"]
TAG_REFERENCE = re.compile(r"(?:^|\s)#([A-Za-z0-9][\w.-]*)")
WORK_REFERENCE = re.compile(r"\bWO-[\w-]+\b", re.IGNORECASE)


class EntryIn(BaseModel):
    category_id: str = "shift_cat_7"
    severity: Severity = "info"
    title: str = Field(min_length=1, max_length=250)
    body: str = Field("", max_length=20000)
    event_at: datetime | None = None
    equipment_id: str | None = None
    work_order_id: str | None = None
    reading_id: str | None = None
    punch_id: str | None = None
    in_handover: bool = False
    author_name: str | None = Field(None, max_length=250)


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    author_name: str | None = Field(None, max_length=250)


class VoidIn(BaseModel):
    reason: str = Field(min_length=1, max_length=10000)
    author_name: str | None = Field(None, max_length=250)


def nonblank(value, label):
    if not value.strip():
        raise HTTPException(422, f"{label} is required")
    return value.strip()


def actor(principal, author_name=None):
    if principal.mode == "open":
        return (author_name or principal.username).strip() or "Operator"
    return principal.username


def shift_for(db, shift_id, principal):
    row = shifts.load(db, Shift, shift_id, "Shift")
    shifts.scoped(principal, row.site_id, row.area_id)
    return row


def entry_for(db, entry_id, principal):
    row = shifts.load(db, ShiftLogEntry, entry_id, "Shift log entry")
    return row, shift_for(db, row.shift_id, principal)


def add_audit(db, principal, name, action, entry, detail=None):
    service.audit(db, action, actor=name, user_id=principal.user_id, entity_type="shift_log_entry",
                  entity_id=entry.id, detail=detail)


def latest_entries(query):
    newer = aliased(ShiftLogEntry)
    return query.filter(~select(newer.id).where(newer.supersedes_id == ShiftLogEntry.id).exists())


def link_evidence(db, shift, fields):
    if not fields["equipment_id"]:
        match = TAG_REFERENCE.search(fields["title"] + " " + fields["body"])
        if match:
            row = db.query(Equipment).filter_by(tag=match[1], site_id=shift.site_id, area_id=shift.area_id).first()
            if row:
                fields["equipment_id"] = row.id
    if not fields["work_order_id"]:
        match = WORK_REFERENCE.search(fields["title"] + " " + fields["body"])
        if match:
            row = db.query(WorkOrder).filter(WorkOrder.id.ilike(match[0]), WorkOrder.site_id == shift.site_id,
                                             WorkOrder.area_id == shift.area_id).first()
            if row:
                fields["work_order_id"] = row.id
    areas = []
    if fields["equipment_id"]:
        equipment = shifts.load(db, Equipment, fields["equipment_id"], "Equipment")
        areas.append((equipment.site_id, equipment.area_id))
    if fields["work_order_id"]:
        order = shifts.load(db, WorkOrder, fields["work_order_id"], "Work order")
        areas.append((order.site_id, order.area_id))
    if fields["reading_id"]:
        reading = shifts.load(db, Reading, fields["reading_id"], "Reading")
        areas.append((reading.equipment.site_id, reading.equipment.area_id))
    if fields["punch_id"]:
        punch = shifts.load(db, Punch, fields["punch_id"], "Punch item")
        equipment = db.get(Equipment, punch.record.equipment_id) if punch.record.equipment_id else None
        areas.append((equipment.site_id, equipment.area_id) if equipment else (None, None))
    if any(area != (shift.site_id, shift.area_id) for area in areas):
        raise HTTPException(403, "Linked evidence must belong to this shift's area")


def describe_entries(db, rows):
    if not rows:
        return []
    ids = [row.id for row in rows]
    categories = {row.id: row.name for row in db.query(ShiftLogCategory).filter(
        ShiftLogCategory.id.in_({item.category_id for item in rows})
    )}
    attachments = {}
    for item in db.query(ShiftLogAttachment).filter(ShiftLogAttachment.entry_id.in_(ids)):
        attachments.setdefault(item.entry_id, []).append({"id": item.id, "filename": item.filename,
                                                         "size": item.size})
    comments = {}
    for item in db.query(ShiftLogComment).filter(ShiftLogComment.entry_id.in_(ids)).order_by(
        ShiftLogComment.created_at, ShiftLogComment.id
    ):
        comments.setdefault(item.entry_id, []).append(shifts.columns(item))
    return [{**shifts.columns(row), "category": categories.get(row.category_id),
             "attachments": attachments.get(row.id, []), "comments": comments.get(row.id, [])} for row in rows]


def entry_data(db, shift, body, principal):
    data = body.model_dump(exclude={"event_at", "author_name"})
    data["title"] = nonblank(body.title, "Title")
    category = shifts.load(db, ShiftLogCategory, body.category_id, "Log category")
    if not category.active or (category.site_id and category.site_id != shift.site_id):
        raise HTTPException(422, "Choose an active category for this site")
    link_evidence(db, shift, data)
    if body.event_at and not body.event_at.tzinfo:
        raise HTTPException(422, "Event time must include its timezone")
    event_at = shifts.utc_text(body.event_at) if body.event_at else utcnow_iso()
    after_handover = db.query(Handover.id).filter(
        Handover.outgoing_shift_id == shift.id, Handover.signed_off_at.is_not(None)
    ).first() is not None
    return {**data, "shift_id": shift.id, "event_at": event_at, "recorded_at": utcnow_iso(),
            "author_id": principal.user_id, "author_name": actor(principal, body.author_name),
            "after_handover": after_handover}


def build_router(get_db, get_principal, settings):
    router = APIRouter(prefix="/api", tags=["shift log"])

    def reader(principal=Depends(get_principal)):
        principal.require("shiftlog.read")
        return principal

    def writer(principal=Depends(get_principal)):
        principal.require("shiftlog.write")
        return principal

    def entries_page(db, query, page, page_size):
        total = count_rows(query, ShiftLogEntry.id)
        rows = query.order_by(ShiftLogEntry.event_at.desc(), ShiftLogEntry.recorded_at.desc(),
                              ShiftLogEntry.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return page_result(describe_entries(db, rows), total, page, page_size)

    @router.get("/shifts/{shift_id}/entries")
    def entries(shift_id: str, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                revisions: bool = False, timeline_filter: str | None = None, db: Session = Depends(get_db), principal=Depends(reader)):
        shift = shift_for(db, shift_id, principal)
        query = db.query(ShiftLogEntry).filter_by(shift_id=shift_id)
        if not revisions:
            query = latest_entries(query)
            previous = db.query(Shift.id).filter(
                Shift.site_id == shift.site_id, Shift.area_id == shift.area_id, Shift.ends_at <= shift.starts_at
            ).order_by(Shift.ends_at.desc()).limit(1).scalar_subquery()
            query = latest_entries(db.query(ShiftLogEntry).filter(or_(
                ShiftLogEntry.shift_id == shift_id,
                (ShiftLogEntry.shift_id == previous) & ShiftLogEntry.after_handover.is_(True),
            )))
        if timeline_filter is None:
            return entries_page(db, query, page, page_size)
        handover_id = select(Handover.id).where(Handover.outgoing_shift_id == shift_id).order_by(
            Handover.version.desc()).limit(1).scalar_subquery()
        included = select(HandoverItem.id).where(
            HandoverItem.handover_id == handover_id, HandoverItem.kind == "log_entry",
            HandoverItem.ref_id == ShiftLogEntry.id, HandoverItem.removed_at.is_(None),
        ).exists()
        kind = case((ShiftLogEntry.reading_id.is_not(None), "readings"),
                    (ShiftLogEntry.work_order_id.is_not(None), "work_orders"), else_="notes")
        counts = {"all": 0, "included": 0, "readings": 0, "work_orders": 0, "notes": 0}
        totals = query.with_entities(ShiftLogEntry.category_id, kind, included, func.count()).group_by(
            ShiftLogEntry.category_id, kind, included)
        for category_id, entry_kind, pinned, count in totals:
            key = "category:" + category_id
            counts[key] = counts.get(key, 0) + count
            counts["all"] += count
            counts[entry_kind] += count
            if pinned:
                counts["included"] += count
        if timeline_filter == "included":
            query = query.filter(included)
        elif timeline_filter in {"readings", "work_orders", "notes"}:
            query = query.filter(kind == timeline_filter)
        elif timeline_filter.startswith("category:"):
            query = query.filter(ShiftLogEntry.category_id == timeline_filter.removeprefix("category:"))
        return {**entries_page(db, query, page, page_size), "counts": counts}

    @router.post("/shifts/{shift_id}/entries", status_code=201)
    def add_entry(shift_id: str, body: EntryIn, db: Session = Depends(get_db), principal=Depends(writer)):
        shift = shift_for(db, shift_id, principal)
        if shift.starts_at > utcnow_iso():
            raise HTTPException(409, "This shift has not started")
        row = ShiftLogEntry(id=uid("shift_entry"), version=1, **entry_data(db, shift, body, principal))
        db.add(row)
        add_audit(db, principal, row.author_name, "shiftlog.entry_added", row)
        db.flush()
        result = describe_entries(db, [row])[0]
        db.commit()
        return result

    def check_latest(db, row):
        if row.voided_at or db.query(ShiftLogEntry.id).filter_by(supersedes_id=row.id).first():
            raise HTTPException(409, "This entry already has a newer revision")

    @router.post("/shift-log-entries/{entry_id}/amend", status_code=201)
    def amend(entry_id: str, body: EntryIn, db: Session = Depends(get_db), principal=Depends(writer)):
        original, shift = entry_for(db, entry_id, principal)
        check_latest(db, original)
        values = entry_data(db, shift, body, principal)
        if not body.event_at:
            values["event_at"] = original.event_at
        row = ShiftLogEntry(id=uid("shift_entry"), version=original.version + 1,
                            supersedes_id=original.id, **values)
        db.add(row)
        add_audit(db, principal, row.author_name, "shiftlog.entry_amended", row, {"previous": original.id})
        db.flush()
        result = describe_entries(db, [row])[0]
        db.commit()
        return result

    @router.post("/shift-log-entries/{entry_id}/void", status_code=201)
    def void(entry_id: str, body: VoidIn, db: Session = Depends(get_db), principal=Depends(writer)):
        original, shift = entry_for(db, entry_id, principal)
        check_latest(db, original)
        values = shifts.columns(original)
        name = actor(principal, body.author_name)
        now = utcnow_iso()
        values.update(id=uid("shift_entry"), supersedes_id=entry_id, version=original.version + 1,
                      voided_at=now, void_reason=nonblank(body.reason, "Void reason"), recorded_at=now,
                      author_name=name, author_id=principal.user_id, in_handover=False,
                      after_handover=shift.status in {"signed_off", "acknowledged", "returned"})
        row = ShiftLogEntry(**values)
        db.add(row)
        add_audit(db, principal, name, "shiftlog.entry_voided", row, {"previous": entry_id, "reason": row.void_reason})
        db.flush()
        result = describe_entries(db, [row])[0]
        db.commit()
        return result

    @router.post("/shift-log-entries/{entry_id}/comments", status_code=201)
    def comment(entry_id: str, body: MessageIn, db: Session = Depends(get_db), principal=Depends(writer)):
        entry, _ = entry_for(db, entry_id, principal)
        row = ShiftLogComment(id=uid("shift_comment"), entry_id=entry_id,
                              author_id=principal.user_id, author_name=actor(principal, body.author_name),
                              body=nonblank(body.body, "Comment"), created_at=utcnow_iso())
        db.add(row)
        add_audit(db, principal, row.author_name, "shiftlog.comment_added", entry, {"comment_id": row.id})
        db.flush()
        result = shifts.columns(row)
        db.commit()
        return result

    @router.post("/shift-log-entries/{entry_id}/attachments", status_code=201)
    async def add_attachment(entry_id: str, file: UploadFile, db: Session = Depends(get_db),
                             principal=Depends(writer)):
        entry, _ = entry_for(db, entry_id, principal)
        data = await read_upload(file, settings.upload_limit_bytes)
        mime = detect_upload_mime(file.filename, data, settings.attachment_extensions)
        identifier = uid("shift_file")
        now = utcnow_iso()
        relative = f"shiftlog/{now[:4]}/{now[5:7]}/{identifier}"
        path = qr_entries.attachment_path(relative, "shiftlog")
        path.parent.mkdir(parents=True, exist_ok=True)
        name = actor(principal)
        row = ShiftLogAttachment(id=identifier, entry_id=entry_id, equipment_id=entry.equipment_id,
                                 size=len(data), filename=safe_filename(file.filename or "attachment"),
                                 content_type=mime, sha256=sha256(data).hexdigest(),
                                 storage_path=relative, uploaded_by=name, user_id=principal.user_id, created_at=now)
        with path.open("xb") as target:
            try:
                target.write(data)
                target.flush()
                db.add(row)
                add_audit(db, principal, name, "shiftlog.attachment_added", entry, {"attachment_id": identifier})
                db.flush()
                result = shifts.columns(row)
                db.commit()
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return result

    @router.get("/shift-log-attachments/{identifier}")
    def download(identifier: str, db: Session = Depends(get_db), principal=Depends(reader)):
        row = shifts.load(db, ShiftLogAttachment, identifier, "Attachment")
        entry_for(db, row.entry_id, principal)
        path = qr_entries.attachment_path(row.storage_path, "shiftlog")
        if not path.is_file():
            raise HTTPException(404, "Attachment not found")
        return FileResponse(path, media_type=row.content_type, filename=row.filename,
                            content_disposition_type="attachment")

    @router.get("/shift-log-entries")
    def history(date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
                area: str | None = None, crew: str | None = None, category: str | None = None,
                severity: Severity | None = None, equipment: str | None = None, q: str | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db), principal=Depends(reader)):
        query = db.query(ShiftLogEntry).join(Shift, Shift.id == ShiftLogEntry.shift_id)
        for column, value in ((Shift.area_id, area), (Shift.crew_id, crew), (ShiftLogEntry.category_id, category),
                              (ShiftLogEntry.severity, severity), (ShiftLogEntry.equipment_id, equipment),
                              (Shift.site_id, principal.site_id), (Shift.area_id, principal.area_id)):
            if value:
                query = query.filter(column == value)
        if date_from:
            query = query.filter(Shift.date >= date_from.isoformat())
        if date_to:
            query = query.filter(Shift.date <= date_to.isoformat())
        if q and q.strip():
            query = query.filter(service.substring_match(q.strip(), ShiftLogEntry.title, ShiftLogEntry.body))
        return entries_page(db, query, page, page_size)

    return router
