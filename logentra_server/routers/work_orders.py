"""Work-order REST API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from logentra_server import authz
from logentra_server import service, workorders as wo
from logentra_server.auth import Principal
from logentra_server.models import AutoWorkOrderRule, Document, WorkOrder
from logentra_server.utils import uid
from logentra_server.documents import safe_filename
from logentra_server.http_security import read_request_body, read_upload
from logentra_server.upload_safety import detect_upload_mime, require_extension

ROOT = Path(__file__).resolve().parent.parent.parent
ATTACH_DIR = ROOT / "data" / "attachments"


def build_router(get_db, get_principal, settings) -> APIRouter:
    router = APIRouter()

    def current(principal: Principal = Depends(get_principal)) -> Principal:
        return principal

    def _get(db: Session, wid: str, principal: Principal, action="access") -> WorkOrder:
        return authz.require_object(db, principal, "work_order", wo.get_work_order(db, wid), action)

    @router.get("/api/work-orders", tags=["work-orders"])
    def list_work_orders(
        db: Session = Depends(get_db),
        principal: Principal = Depends(current),
        status: str | None = None,
        priority: str | None = None,
        assignee_id: str | None = None,
        equipment_id: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ):
        principal.require("workorders.read")
        try:
            return wo.list_work_orders(
                db, status=status, priority=priority, assignee_id=assignee_id,
                equipment_id=equipment_id, q=q, page=page, page_size=page_size, principal=principal,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/api/work-orders", tags=["work-orders"], status_code=201)
    def create_work_order(body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("workorders.create")
        authz.check_work_order_assignee(db, principal, body.get("assignee_id") or body.get("assigneeId"))
        if principal.scoped:
            allowed = {"id", "title", "description", "priority", "equipment_id", "equipmentId", "due_at", "dueAt",
                       "due_date", "site_id", "siteId", "area_id", "areaId", "assignee_id", "assigneeId"}
            if set(body) - allowed:
                raise HTTPException(403, "This work order contains restricted fields")
            site = body.get("site_id") or body.get("siteId") or principal.site_id
            area = body.get("area_id") or body.get("areaId") or principal.area_id
            equipment = body.get("equipment_id") or body.get("equipmentId")
            if equipment:
                row = authz.require_object(db, principal, "equipment", equipment, "access")
                site, area = site or row.site_id, area or row.area_id
                if (site, area) != (row.site_id, row.area_id):
                    raise HTTPException(403, "Work order location must match its equipment")
            authz.require_location(principal, site, area, detail="Work order location is outside your scope")
            service.validate_location(db, site, area)
            body = {**body, "site_id": site, "area_id": area}
        if body.get("id") and db.get(WorkOrder, body["id"]) is not None:
            raise HTTPException(409, "A work order with this ID already exists")
        try:
            row = wo.create_work_order(db, body, actor=principal.actor, user_id=principal.user_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return wo.to_dict(wo.get_work_order(db, row.id) or row, db, detail=True)

    @router.get("/api/work-orders/{wid}", tags=["work-orders"])
    def get_work_order(wid: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("workorders.read")
        return wo.to_dict(_get(db, wid, principal), db, detail=True)

    @router.patch("/api/work-orders/{wid}", tags=["work-orders"])
    def patch_work_order(wid: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        row = _get(db, wid, principal)
        if principal.scoped and (body.get("equipment_id") or body.get("equipmentId")) == row.equipment_id:
            body = {key: value for key, value in body.items() if key not in {"equipment_id", "equipmentId"}}
        if principal.scoped and set(body) - {
            "title", "description", "priority", "due_at", "dueAt", "due_date", "status",
            "assignee_id", "assigneeId", "labour_minutes", "labour_notes",
        }:
            raise HTTPException(403, "Changing work order links or location requires unscoped access")
        if "assignee_id" in body or "assigneeId" in body:
            principal.require("workorders.assign")
            authz.check_work_order_assignee(db, principal, body.get("assignee_id") or body.get("assigneeId"))
        details = set(body) - {"status", "assignee_id", "assigneeId"}
        if details or not body:
            authz.require_work_order_edit(principal)
            if not principal.can("workorders.assign") and (
                row.created_by_id != principal.user_id or row.status != "open"
            ):
                raise HTTPException(403, "You are not allowed to edit this work order")
        if "status" in body:
            authz.require_work_order_transition(principal, body["status"])
        try:
            row = wo.update_work_order(
                db, row, body, actor=principal.actor, user_id=principal.user_id,
                force_status=principal.can("users.manage"),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return wo.to_dict(row, db, detail=True)

    @router.post("/api/work-orders/{wid}/transition", tags=["work-orders"])
    def transition(wid: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        row = _get(db, wid, principal)
        status = body.get("status")
        if not status:
            raise HTTPException(400, "status is required")
        authz.require_work_order_transition(principal, status)
        try:
            row = wo.update_work_order(
                db, row, {"status": status}, actor=principal.actor, user_id=principal.user_id,
                force_status=principal.can("users.manage"),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return wo.to_dict(row, db, detail=True)

    @router.post("/api/work-orders/{wid}/assign", tags=["work-orders"])
    def assign(wid: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("workorders.assign")
        row = _get(db, wid, principal)
        assignee = body.get("assignee_id") or body.get("assigneeId")
        authz.check_work_order_assignee(db, principal, assignee)
        row = wo.update_work_order(
            db, row, {"assignee_id": assignee}, actor=principal.actor, user_id=principal.user_id,
        )
        return wo.to_dict(row, db, detail=True)

    @router.post("/api/work-orders/{wid}/comments", tags=["work-orders"], status_code=201)
    def add_comment(wid: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        authz.require_any(principal, "workorders.create", "workorders.assign", "workorders.close")
        row = _get(db, wid, principal)
        try:
            c = wo.add_comment(db, row, body.get("body") or "", author=principal.username, user_id=principal.user_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": c.id, "user_id": c.user_id, "author": c.author, "body": c.body, "created_at": c.created_at}

    @router.post("/api/work-orders/{wid}/labour", tags=["work-orders"], status_code=201)
    def add_labour(wid: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        authz.require_any(principal, "workorders.create", "workorders.assign")
        row = _get(db, wid, principal)
        try:
            e = wo.add_labour(
                db, row, int(body.get("minutes") or 0),
                note=body.get("note") or "", author=principal.username,
                user_id=principal.user_id, worked_on=body.get("worked_on") or body.get("workedOn"),
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": e.id, "minutes": e.minutes, "note": e.note, "worked_on": e.worked_on, "author": e.author, "created_at": e.created_at}

    @router.post("/api/work-orders/{wid}/attachments", tags=["work-orders"], status_code=201)
    async def add_attachment(
        wid: str,
        request: Request,
        db: Session = Depends(get_db),
        principal: Principal = Depends(current),
        file: UploadFile | None = File(default=None),
    ):
        authz.require_work_order_edit(principal)
        row = _get(db, wid, principal)
        storage_path = None
        filename = None
        content_type = None
        size = None
        if file is not None:
            data = await read_upload(file, settings.upload_limit_bytes)
            content_type = detect_upload_mime(file.filename, data, settings.attachment_extensions)
            filename = safe_filename(file.filename)
            size = len(data)
            dest_dir = (ATTACH_DIR / safe_filename(row.id)).resolve()
            if not dest_dir.is_relative_to(ATTACH_DIR.resolve()):
                raise HTTPException(400, "Invalid attachment path")
            dest_dir.mkdir(parents=True, exist_ok=True)
            stored = dest_dir / f"{uid('att')}_{filename}"
            stored.write_bytes(data)
            storage_path = str(stored.relative_to(ROOT))
        else:
            try:
                body = json.loads(await read_request_body(request, settings.upload_limit_bytes))
            except (ValueError, RecursionError, RuntimeError):
                body = {}
            if not isinstance(body, dict):
                body = {}
            filename = body.get("filename")
            size = body.get("size")
            if body.get("storage_path"):
                raise HTTPException(400, "Upload the attachment contents")
            if filename:
                if not isinstance(filename, str):
                    raise HTTPException(400, "filename must be text")
                require_extension(filename, settings.attachment_extensions)
            if size is not None:
                if type(size) is not int or size < 0:
                    raise HTTPException(400, "size must be a non-negative integer")
                if size > settings.upload_limit_bytes:
                    raise HTTPException(413, "Upload too large")
        try:
            att = wo.add_attachment(
                db, row, filename=filename or "", content_type=content_type,
                size=size, storage_path=storage_path, uploaded_by=principal.username,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "id": att.id, "filename": att.filename, "content_type": att.content_type,
            "size": att.size, "storage_path": att.storage_path, "uploaded_by": att.uploaded_by,
            "created_at": att.created_at,
        }

    @router.delete("/api/work-orders/{wid}", tags=["work-orders"])
    def delete_work_order(wid: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        row = _get(db, wid, principal, "delete")
        service.audit(db, "delete", actor=principal.actor, user_id=principal.user_id,
                      entity_type="work_order", entity_id=wid,
                      detail={"title": row.title, "status": row.status, "deleted_by": principal.actor})
        service.protect_document_links(db, Document.work_order_id, wid)
        db.delete(row)
        db.commit()
        return {"ok": True}

    # ---------- auto-create rules ----------
    @router.get("/api/work-order-rules", tags=["work-orders"])
    def list_rules(db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require_global()
        principal.require("workorders.read")
        rows = db.query(AutoWorkOrderRule).order_by(AutoWorkOrderRule.created_at.desc()).all()
        return [wo.rule_to_dict(r) for r in rows]

    @router.post("/api/work-order-rules", tags=["work-orders"], status_code=201)
    def create_rule(body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require_global()
        principal.require("workorders.assign")
        if body.get("id") and db.get(AutoWorkOrderRule, body["id"]) is not None:
            raise HTTPException(409, "A work-order rule with this ID already exists")
        row = wo.upsert_rule(db, body)
        return wo.rule_to_dict(row)

    @router.patch("/api/work-order-rules/{rid}", tags=["work-orders"])
    def patch_rule(rid: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require_global()
        principal.require("workorders.assign")
        row = db.get(AutoWorkOrderRule, rid)
        if not row:
            raise HTTPException(404, "Rule not found")
        row = wo.upsert_rule(db, body, rule=row)
        return wo.rule_to_dict(row)

    @router.delete("/api/work-order-rules/{rid}", tags=["work-orders"])
    def delete_rule(rid: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require_global()
        principal.require("workorders.assign")
        row = db.get(AutoWorkOrderRule, rid)
        if not row:
            raise HTTPException(404, "Rule not found")
        db.delete(row)
        db.commit()
        return {"ok": True}

    return router
