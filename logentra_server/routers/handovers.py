"""Outgoing and incoming handover workflow, history and printable records."""
import csv
import io
import json
from datetime import date, datetime, timezone
from html import escape
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from logentra_server import handovers, service, shifts
from logentra_server.export_safety import safe_cell
from logentra_server.pagination import count_rows, page_result
from logentra_server.routers.shift_log import MessageIn, Severity, actor, nonblank, shift_for
from logentra_server.shift_models import Handover, HandoverComment, HandoverItem, Shift, ShiftLogCategory, ShiftLogEntry
from logentra_server.utils import uid


class ActorIn(BaseModel):
    author_name: str | None = Field(None, max_length=250)


class SummaryIn(ActorIn):
    summary: str = Field(max_length=20000)


class SignOffIn(ActorIn):
    summary: str | None = Field(None, max_length=20000)


class ItemIn(ActorIn):
    kind: Literal["note", "log_entry", "work_order", "oor_reading", "punch"] = "note"
    ref_id: str | None = None
    title: str = Field("", max_length=250)
    detail: str = Field("", max_length=10000)
    severity: Severity = "info"


class AcknowledgeIn(ActorIn):
    comment: str = Field("", max_length=10000)


class ReturnIn(ActorIn):
    reason: str = Field(min_length=1, max_length=10000)


def report_html(record):
    frozen = json.loads(record["snapshot_json"]) if record["snapshot_json"] else record
    current = {item["id"]: item for item in record["items"]}
    comments = record["comments"]
    h = lambda value: escape(str(value if value is not None else ""))
    items = []
    for item in frozen["items"]:
        live = current.get(item["id"], {})
        status = f"Seen {h(live['seen_at'])}" if live.get("seen_at") else "Unseen"
        messages = "".join(
            f"<p><strong>{h(msg['kind'].title())} · {h(msg['author_name'])} · {h(msg['created_at'])}</strong><br>{h(msg['body'])}</p>"
            for msg in comments if msg["item_id"] == item["id"]
        )
        items.append(f"<article><h3>{h(item['title'])}</h3><p>{h(item['detail'])}</p>"
                     f"<small>{h(item['kind'])} · {h(item['severity'])} · {status}</small>{messages}</article>")
    versions = "".join(
        f"<li>Version {version['version']} · {h(version['status'])}"
        + (f" · Return reason: {h(version['return_reason'])}" if version["return_reason"] else "") + "</li>"
        for version in record["versions"]
    )
    outgoing = frozen["outgoing"]
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Shift handover</title>
    <style>@page{{size:A4;margin:15mm}}body{{font:15px system-ui,sans-serif;max-width:180mm;margin:20px auto;padding:0 12px;color:#182a3b}}
    p{{white-space:pre-wrap}}article{{border-bottom:1px solid #ccd5df;break-inside:avoid;padding-bottom:12px}}
    code{{overflow-wrap:anywhere}}@media print{{button{{display:none}}body{{margin:0;padding:0}}}}</style>
    <button onclick="window.print()">Print / Save as PDF</button>
    <h1>Shift handover · Version {record['version']}</h1>
    <p>{h(outgoing['date'])} · {h(outgoing['name'])} · {h(outgoing['area_name'])} · {h(outgoing['crew_name'])}</p>
    <p>Status: {h(record['status'].replace('_', ' '))}</p>
    <h2>Outgoing summary</h2><p>{h(frozen['summary'])}</p><h2>Handover items</h2>{''.join(items)}
    <h2>Sign-off and acknowledgement</h2>
    <p>Signed off: {h(record['signed_off_name'])} {h(record['signed_off_at'])}</p>
    <p>Acknowledged: {h(record['acknowledged_name'])} {h(record['acknowledged_at'])}{' · Late' if record['late'] else ''}
    {' · After incoming shift start' if record['acknowledged_after_start'] else ''}</p>
    <p>{h(record['ack_comment'])}</p><p>Unseen at acknowledgement: {record['unseen_count']}</p>
    <p>Returned: {h(record['returned_name'])} {h(record['returned_at'])} {h(record['return_reason'])}</p>
    <p>Snapshot SHA-256 <code>{h(record['snapshot_sha256'])}</code></p>
    <h2>Version history</h2><ul>{versions}</ul></html>"""


def build_router(get_db, get_principal, clock=lambda: datetime.now(timezone.utc)):
    router = APIRouter(prefix="/api", tags=["handovers"])

    def reader(principal=Depends(get_principal)):
        principal.require("shiftlog.read")
        return principal

    def get_handover(db, identifier, principal, *, lock=False):
        row = shifts.load(db, Handover, identifier, "Handover")
        shifts.scoped(principal, row.site_id, row.area_id)
        if lock:
            handovers.lock_shift(db, row.outgoing_shift_id)
        return row

    def finish(db, row, principal):
        db.flush()
        result = handovers.describe(db, row, principal)
        db.commit()
        return result

    def edit(db, identifier, principal):
        row = get_handover(db, identifier, principal, lock=True)
        handovers.allowed(db, principal, row)
        handovers.require_state(row, {"draft"})
        return row

    def incoming(db, identifier, principal, *, allow_manager=True):
        row = get_handover(db, identifier, principal, lock=True)
        handovers.allowed(db, principal, row, incoming=True, allow_manager=allow_manager)
        handovers.require_state(row, {"signed_off"})
        return row

    @router.get("/shifts/{shift_id}/handover")
    def for_shift(shift_id: str, db: Session = Depends(get_db), principal=Depends(reader)):
        shift = shift_for(db, shift_id, principal)
        handovers.lock_shift(db, shift_id)
        if shift.starts_at > shifts.utc_text(clock()):
            raise HTTPException(409, "This shift has not started")
        row = handovers.draft(db, shift, clock(), principal)
        if row.status == "draft":
            handovers.sync_items(db, row, shift, clock())
        handovers.audit_opened(db, principal, actor(principal), row, locked=True)
        return finish(db, row, principal)

    @router.get("/handovers/notifications")
    def notifications(db: Session = Depends(get_db), principal=Depends(reader)):
        query = service.apply_scope(db.query(Handover), Handover, principal)
        waiting = []
        for row in query.filter(or_(Handover.status.in_(("signed_off", "returned")),
                                    (Handover.status == "draft") & Handover.reminder_sent_at.is_not(None))):
            if row.status == "returned" and handovers.active_version(db, row.outgoing_shift_id).version > row.version + 1:
                continue
            outgoing = db.get(Shift, row.outgoing_shift_id)
            incoming_shift = db.get(Shift, row.incoming_shift_id) if row.incoming_shift_id else None
            if row.status == "signed_off":
                direct = incoming_shift and principal.user_id and incoming_shift.lead_user_id == principal.user_id
                allowed = direct or (row.escalated_to and principal.role_name == row.escalated_to)
                if not (allowed or principal.mode == "open" or not incoming_shift and principal.can("shifts.manage")):
                    continue
                kind = "overdue" if row.ack_due_at < shifts.utc_text(clock()) else "waiting"
            else:
                if not (principal.mode == "open" or principal.user_id and outgoing.lead_user_id == principal.user_id):
                    continue
                if row.status == "returned":
                    if handovers.active_version(db, outgoing.id).status != "draft":
                        continue
                    kind = "returned"
                else:
                    kind = "reminder"
            waiting.append({"id": row.id, "kind": kind, "area_id": row.area_id,
                            "shift": shifts.describe_shift(db, outgoing), "ack_due_at": row.ack_due_at})
        return {"items": waiting, "waiting": sum(item["kind"] == "waiting" for item in waiting),
                "overdue": sum(item["kind"] == "overdue" for item in waiting)}

    def history_query(db, principal, date_from, date_to, area, crew, status, q):
        query = service.apply_scope(db.query(Handover), Handover, principal).join(
            Shift, Shift.id == Handover.outgoing_shift_id
        )
        for column, value in ((Handover.area_id, area), (Shift.crew_id, crew), (Handover.status, status)):
            if value:
                query = query.filter(column == value)
        if date_from:
            query = query.filter(Shift.date >= date_from.isoformat())
        if date_to:
            query = query.filter(Shift.date <= date_to.isoformat())
        if q and q.strip():
            match = service.substring_match(q.strip(), HandoverItem.title, HandoverItem.detail)
            exists = db.query(HandoverItem.id).filter(HandoverItem.handover_id == Handover.id,
                                                      HandoverItem.removed_at.is_(None), match).exists()
            query = query.filter(service.substring_match(q.strip(), Handover.summary, extra=exists))
        return query

    @router.get("/handovers")
    def history(date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
                area: str | None = None, crew: str | None = None, status: str | None = None, q: str | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db), principal=Depends(reader)):
        query = history_query(db, principal, date_from, date_to, area, crew, status, q)
        total = count_rows(query, Handover.id)
        rows = query.order_by(Shift.starts_at.desc(), Handover.version.desc()).offset(
            (page - 1) * page_size).limit(page_size).all()
        return page_result([{**shifts.columns(row), "outgoing": shifts.describe_shift(db, db.get(Shift, row.outgoing_shift_id))}
                            for row in rows], total, page, page_size)

    @router.get("/handovers/export.csv")
    def export_csv(date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
                   area: str | None = None, crew: str | None = None, status: str | None = None, q: str | None = None,
                   db: Session = Depends(get_db), principal=Depends(reader)):
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["Shift", "Area", "Version", "Status", "Summary", "Signed off", "Acknowledged", "Unseen", "SHA-256"])
        for row in history_query(db, principal, date_from, date_to, area, crew, status, q).order_by(Shift.starts_at.desc()):
            writer.writerow(map(safe_cell, (
                row.outgoing_shift_id, row.area_id, row.version, row.status, row.summary,
                row.signed_off_at, row.acknowledged_at, row.unseen_count, row.snapshot_sha256,
            )))
        return Response(stream.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="handovers.csv"'})

    @router.get("/handovers/{identifier}")
    def detail(identifier: str, db: Session = Depends(get_db), principal=Depends(reader)):
        row = get_handover(db, identifier, principal)
        handovers.audit_opened(db, principal, actor(principal), row)
        return finish(db, row, principal)

    @router.patch("/handovers/{identifier}")
    def summary(identifier: str, body: SummaryIn, db: Session = Depends(get_db), principal=Depends(reader)):
        row = edit(db, identifier, principal)
        before = row.summary
        row.summary = body.summary.strip()
        if before != row.summary:
            handovers.audit(db, principal, actor(principal, body.author_name), "summary_changed", row,
                            {"previous": before, "current": row.summary})
        return finish(db, row, principal)

    @router.post("/handovers/{identifier}/items", status_code=201)
    def add_item(identifier: str, body: ItemIn, db: Session = Depends(get_db), principal=Depends(reader)):
        row = edit(db, identifier, principal)
        shift = db.get(Shift, row.outgoing_shift_id)
        event_at, category = shifts.utc_text(clock()), None
        if body.kind == "note":
            title, text, severity = nonblank(body.title, "Title"), body.detail, body.severity
        elif body.kind == "log_entry":
            entry = shifts.load(db, ShiftLogEntry, body.ref_id, "Shift log entry")
            if entry.shift_id != shift.id or entry.voided_at:
                raise HTTPException(404, "Shift log entry not found")
            title, text, severity = entry.title, entry.body, entry.severity
            event_at = entry.event_at
            log_category = db.get(ShiftLogCategory, entry.category_id)
            category = log_category.name if log_category else None
        else:
            match = next((item for item in handovers.live_candidates(db, shift, clock()) if item[:2] == (body.kind, body.ref_id)), None)
            if not match:
                raise HTTPException(404, "Handover source item not found")
            _, _, title, text, severity, _, event_at, category = match
        existing = (db.query(HandoverItem).filter_by(handover_id=row.id, kind=body.kind, ref_id=body.ref_id).first()
                    if body.kind != "note" else None)
        name = actor(principal, body.author_name)
        if existing:
            existing.removed_at = existing.removed_by = None
            existing.removed_manually = False
            existing.pinned = True
            handovers.audit(db, principal, name, "item_added", row, {"item_id": existing.id, "restored": True})
        else:
            item = handovers.add_item(db, row, body.kind, body.ref_id, title, text, severity, False,
                                      len(handovers.items_for(db, row, removed=True)), principal, name,
                                      event_at=event_at, category=category)
            item.pinned = True
        return finish(db, row, principal)

    @router.delete("/handovers/{identifier}/items/{item_id}")
    def remove(identifier: str, item_id: str, body: ActorIn | None = None,
               db: Session = Depends(get_db), principal=Depends(reader)):
        row = edit(db, identifier, principal)
        item = handovers.item_for(db, row, item_id)
        item.removed_at = shifts.utc_text(clock())
        item.removed_by = principal.user_id
        item.removed_manually = True
        handovers.audit(db, principal, actor(principal, body.author_name if body else None), "item_removed", row,
                        {"item_id": item.id, "kind": item.kind})
        return finish(db, row, principal)

    @router.post("/handovers/{identifier}/sign-off")
    def sign(identifier: str, body: SignOffIn | None = None, db: Session = Depends(get_db), principal=Depends(reader)):
        row = edit(db, identifier, principal)
        name = actor(principal, body.author_name if body else None)
        if body and body.summary is not None and row.summary != body.summary.strip():
            row.summary = body.summary.strip()
            handovers.audit(db, principal, name, "summary_changed", row, {"current": row.summary})
        handovers.sign_off(db, row, principal, clock(), name)
        return finish(db, row, principal)

    @router.post("/handovers/{identifier}/items/{item_id}/seen")
    def seen(identifier: str, item_id: str, body: ActorIn | None = None,
             db: Session = Depends(get_db), principal=Depends(reader)):
        row = incoming(db, identifier, principal)
        item = handovers.item_for(db, row, item_id)
        if not item.seen_at:
            item.seen_at = shifts.utc_text(clock())
            item.seen_by = principal.user_id
            name = actor(principal, body.author_name if body else None)
            handovers.audit(db, principal, name, "item_seen", row, {"item_id": item.id})
        return finish(db, row, principal)

    def add_comment(db, row, item_id, kind, body, principal, reply_to=None):
        name = actor(principal, body.author_name)
        message = HandoverComment(id=uid("handover_comment"), handover_id=row.id, item_id=item_id,
                                  kind=kind, body=nonblank(body.body, kind.title()),
                                  author_id=principal.user_id, author_name=name,
                                  reply_to_id=reply_to, created_at=shifts.utc_text(clock()))
        db.add(message)
        handovers.audit(db, principal, name, kind, row, {"item_id": item_id, "comment_id": message.id})
        return finish(db, row, principal)

    @router.post("/handovers/{identifier}/items/{item_id}/questions", status_code=201)
    def question(identifier: str, item_id: str, body: MessageIn, db: Session = Depends(get_db), principal=Depends(reader)):
        row = incoming(db, identifier, principal)
        handovers.item_for(db, row, item_id)
        return add_comment(db, row, item_id, "question", body, principal)

    @router.post("/handover-comments/{comment_id}/answer", status_code=201)
    def answer(comment_id: str, body: MessageIn, db: Session = Depends(get_db), principal=Depends(reader)):
        question = shifts.load(db, HandoverComment, comment_id, "Question")
        row = get_handover(db, question.handover_id, principal, lock=True)
        handovers.allowed(db, principal, row)
        if question.kind != "question":
            raise HTTPException(422, "Only questions can be answered")
        return add_comment(db, row, question.item_id, "answer", body, principal, comment_id)

    @router.post("/handovers/{identifier}/acknowledge")
    def acknowledge(identifier: str, body: AcknowledgeIn | None = None,
                    db: Session = Depends(get_db), principal=Depends(reader)):
        row = incoming(db, identifier, principal)
        handovers.acknowledge(db, row, principal, clock(), actor(principal, body.author_name if body else None),
                              body.comment.strip() if body else "")
        return finish(db, row, principal)

    @router.post("/handovers/{identifier}/return")
    def return_to_lead(identifier: str, body: ReturnIn, db: Session = Depends(get_db), principal=Depends(reader)):
        row = incoming(db, identifier, principal, allow_manager=False)
        new = handovers.return_handover(db, row, principal, clock(), actor(principal, body.author_name),
                                        nonblank(body.reason, "Return reason"))
        return finish(db, new, principal)

    @router.get("/handovers/{identifier}/report", response_class=HTMLResponse)
    def report(identifier: str, db: Session = Depends(get_db), principal=Depends(reader)):
        return HTMLResponse(report_html(handovers.describe(db, get_handover(db, identifier, principal))))

    return router
