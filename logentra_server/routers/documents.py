"""Document approval REST API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from logentra_server import authz
from logentra_server import documents as docs, service
from logentra_server.auth import Principal
from logentra_server.http_security import read_upload
from logentra_server.models import ApprovalRoute
from logentra_server.upload_safety import PREVIEW_TYPES, detect_upload_mime, extension, preview_mime


def build_router(get_db, get_principal, settings) -> APIRouter:
    router = APIRouter()

    def current(principal: Principal = Depends(get_principal)) -> Principal:
        return principal

    def _doc(db: Session, did: str, principal: Principal):
        return authz.require_object(db, principal, "document", docs.get_document(db, did), "access")


    def _err(exc: Exception):
        if isinstance(exc, PermissionError):
            raise HTTPException(403, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc

    # ---------- dashboard ----------
    @router.get("/api/dashboard", tags=["catalog"], summary="KPI counts, alerts, and my approvals queue")
    def dashboard(db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("equipment.read", "readings.read", "workorders.read", "documents.read")
        return docs.dashboard_summary(db, principal)

    # ---------- routes ----------
    @router.get("/api/approval-routes", tags=["documents"])
    def list_routes(db: Session = Depends(get_db), principal: Principal = Depends(current), enabled: bool = False):
        principal.require("documents.read")
        rows = docs.list_routes(db, enabled_only=enabled)
        if principal.scoped:
            return [{**route, "steps": []} for route in rows
                    if route["site_id"] is None or route["site_id"] == principal.site_id]
        return rows

    @router.post("/api/approval-routes", tags=["documents"], status_code=201)
    def create_route(body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require_global()
        principal.require("routes.manage")
        try:
            row = docs.create_route(db, body, actor=principal.actor, user_id=principal.user_id)
        except ValueError as exc:
            _err(exc)
        return docs.route_to_dict(row)

    @router.get("/api/approval-routes/{rid}", tags=["documents"])
    def get_route(rid: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require_global()
        principal.require("documents.read")
        row = docs.get_route(db, rid)
        if not row:
            raise HTTPException(404, "Approval route not found")
        return docs.route_to_dict(row)

    @router.patch("/api/approval-routes/{rid}", tags=["documents"])
    def patch_route(rid: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require_global()
        principal.require("routes.manage")
        row = docs.get_route(db, rid)
        if not row:
            raise HTTPException(404, "Approval route not found")
        try:
            row = docs.update_route(db, row, body, actor=principal.actor, user_id=principal.user_id)
        except ValueError as exc:
            _err(exc)
        return docs.route_to_dict(row)

    @router.delete("/api/approval-routes/{rid}", tags=["documents"])
    def delete_route(rid: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require_global()
        principal.require("routes.manage")
        row = docs.get_route(db, rid)
        if not row:
            raise HTTPException(404, "Approval route not found")
        try:
            docs.delete_route(db, row, actor=principal.actor, user_id=principal.user_id)
        except ValueError as exc:
            _err(exc)
        return {"ok": True}

    # ---------- documents ----------
    @router.get("/api/documents", tags=["documents"])
    def list_documents(
        db: Session = Depends(get_db),
        principal: Principal = Depends(current),
        status: str | None = None,
        equipment_id: str | None = None,
        record_id: str | None = None,
        work_order_id: str | None = None,
        document_type: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ):
        principal.require("documents.read")
        return docs.list_documents(
            db, status=status, equipment_id=equipment_id, record_id=record_id,
            work_order_id=work_order_id, document_type=document_type, q=q,
            page=page, page_size=page_size, principal=principal,
        )

    @router.get("/api/documents/queue", tags=["documents"], summary="Documents waiting on the current user")
    def my_queue(
        db: Session = Depends(get_db),
        principal: Principal = Depends(current),
        page: int = 1,
        page_size: int = 50,
    ):
        principal.require("documents.read")
        return docs.list_my_queue(db, principal, page=page, page_size=page_size)

    @router.post("/api/documents", tags=["documents"], status_code=201)
    async def create_document(
        db: Session = Depends(get_db),
        principal: Principal = Depends(current),
        file: UploadFile = File(...),
        title: str | None = Form(None),
        document_type: str | None = Form(None),
        equipment_id: str | None = Form(None),
        record_id: str | None = Form(None),
        work_order_id: str | None = Form(None),
        site_id: str | None = Form(None),
        area_id: str | None = Form(None),
        route_id: str | None = Form(None),
        submit: bool = Form(False),
    ):
        principal.require("documents.upload")
        site_id, area_id = docs.inherit_location(
            db, site_id=site_id, area_id=area_id, equipment_id=equipment_id,
        )
        site_id, area_id = site_id or principal.site_id, area_id or principal.area_id
        authz.require_location(principal, site_id, area_id, detail="Document location is outside your scope")
        service.validate_location(db, site_id, area_id)
        authz.check_document_links(db, principal, {"equipment_id": equipment_id, "record_id": record_id, "work_order_id": work_order_id})
        data = await read_upload(file, settings.upload_limit_bytes)
        mime = detect_upload_mime(file.filename, data, settings.attachment_extensions)
        try:
            if route_id:
                docs.match_route(db, route_id=route_id, site_id=site_id)
            row = docs.create_document(
                db,
                title=title or file.filename or "Untitled",
                filename=file.filename or "upload.bin",
                data=data,
                mime=mime,
                document_type=document_type,
                equipment_id=equipment_id,
                record_id=record_id,
                work_order_id=work_order_id,
                site_id=site_id,
                area_id=area_id,
                route_id=route_id,
                actor=principal.actor,
                user_id=principal.user_id,
                commit=not submit,
            )
            if submit:
                row = docs.submit_document(db, row, route_id=route_id, actor=principal.actor, user_id=principal.user_id)
        except ValueError as exc:
            _err(exc)
        return docs.to_dict(docs.get_document(db, row.id) or row, db, detail=True)

    @router.get("/api/documents/{did}", tags=["documents"])
    def get_document(did: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("documents.read")
        return docs.to_dict(_doc(db, did, principal), db, detail=True)

    @router.patch("/api/documents/{did}", tags=["documents"])
    def patch_document(did: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("documents.upload")
        row = _doc(db, did, principal)
        if principal.scoped:
            if set(body) - {"title", "document_type", "documentType"}:
                raise HTTPException(403, "Changing document links or location requires unscoped access")
        authz.check_document_links(db, principal, body, existing=row)
        try:
            row = docs.update_document(db, row, body, actor=principal.actor, user_id=principal.user_id)
        except ValueError as exc:
            _err(exc)
        return docs.to_dict(row, db, detail=True)

    @router.post("/api/documents/{did}/versions", tags=["documents"], status_code=201)
    async def add_version(
        did: str,
        db: Session = Depends(get_db),
        principal: Principal = Depends(current),
        file: UploadFile = File(...),
    ):
        principal.require("documents.upload")
        row = _doc(db, did, principal)
        data = await read_upload(file, settings.upload_limit_bytes)
        mime = detect_upload_mime(file.filename, data, settings.attachment_extensions)
        try:
            row = docs.add_version(
                db, row, filename=file.filename or "upload.bin", data=data,
                mime=mime, actor=principal.actor, user_id=principal.user_id,
            )
        except ValueError as exc:
            _err(exc)
        return docs.to_dict(row, db, detail=True)

    @router.get("/api/documents/{did}/file", tags=["documents"], summary="Download or inline-preview the current (or given) version")
    def get_file(
        did: str,
        db: Session = Depends(get_db),
        principal: Principal = Depends(current),
        version_id: str | None = None,
    ):
        principal.require("documents.read")
        row = _doc(db, did, principal)
        ver = None
        if version_id:
            ver = next((v for v in (row.versions or []) if v.id == version_id), None)
        else:
            ver = docs.current_version(row)
        if not ver:
            raise HTTPException(404, "File not found")
        try:
            path = docs.verify_version(ver)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        mime = preview_mime(path, PREVIEW_TYPES.get(extension(ver.filename)), PREVIEW_TYPES.values())
        return FileResponse(
            path, media_type=mime or "application/octet-stream", filename=ver.filename,
            content_disposition_type="inline" if mime else "attachment",
            headers={"Content-Security-Policy": "sandbox", "X-Frame-Options": "SAMEORIGIN"},
        )

    @router.post("/api/documents/{did}/submit", tags=["documents"])
    def submit(did: str, body: dict[str, Any] | None = None, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("documents.upload")
        row = _doc(db, did, principal)
        body = body or {}
        try:
            row = docs.submit_document(
                db, row, route_id=body.get("route_id") or body.get("routeId"),
                actor=principal.actor, user_id=principal.user_id,
            )
        except ValueError as exc:
            _err(exc)
        return docs.to_dict(row, db, detail=True)

    @router.post("/api/documents/{did}/approve", tags=["documents"])
    def approve(did: str, body: dict[str, Any] | None = None, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("documents.approve")
        row = _doc(db, did, principal)
        body = body or {}
        try:
            row = docs.approve_document(db, row, principal, comment=body.get("comment") or body.get("body"))
        except (ValueError, PermissionError) as exc:
            _err(exc)
        return docs.to_dict(row, db, detail=True)

    @router.post("/api/documents/{did}/reject", tags=["documents"])
    def reject(did: str, body: dict[str, Any] | None = None, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("documents.approve")
        row = _doc(db, did, principal)
        body = body or {}
        try:
            row = docs.reject_document(db, row, principal, comment=body.get("comment") or body.get("body") or "")
        except (ValueError, PermissionError) as exc:
            _err(exc)
        return docs.to_dict(row, db, detail=True)

    @router.post("/api/documents/{did}/return", tags=["documents"])
    def return_for_changes(did: str, body: dict[str, Any] | None = None, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("documents.approve")
        row = _doc(db, did, principal)
        body = body or {}
        try:
            row = docs.return_document(db, row, principal, comment=body.get("comment") or body.get("body") or "")
        except (ValueError, PermissionError) as exc:
            _err(exc)
        return docs.to_dict(row, db, detail=True)

    @router.post("/api/documents/{did}/comments", tags=["documents"], status_code=201)
    def add_comment(did: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("documents.read")
        row = _doc(db, did, principal)
        text = (body.get("body") or body.get("comment") or "").strip()
        if not text:
            raise HTTPException(400, "body is required")
        docs.add_comment(db, row, body=text, kind="comment", actor=principal.actor, user_id=principal.user_id)
        db.commit()
        return docs.to_dict(docs.get_document(db, row.id) or row, db, detail=True)

    # silence unused import warning if any
    _ = ApprovalRoute
    _ = Query
    return router
