from __future__ import annotations

import csv
import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from logentra_server.auth import Principal, make_auth_dependency
from logentra_server.branding import PRODUCT_NAME, RELEASE_LABEL
from logentra_server.schemas import ReplayIn, SyncBody, WebhookIn
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from logentra_server.http_security import HTTPSecurity, read_upload

from logentra_server import __version__, demo, events, handovers as handover_service, migrate, mqtt_worker, seed, service
from logentra_server.routers import auth_users, documents, handovers, qr, readings, shift_log, shifts, work_orders
from logentra_server.config import Settings, load_settings
from logentra_server.database import init_db, make_engine, make_session_factory
from logentra_server.models import (
    AuditLog,
    Document,
    Equipment,
    Setting,
    Signoff,
    FieldDefinition,
    OutboundWebhook,
    Record,
    Template,
    WebhookDelivery,
)
from logentra_server.utils import dumps, loads, uid, utcnow_iso

log = logging.getLogger("logentra")
ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
UI_HTML = next((p for p in (UI_DIR / "Logentra.html", UI_DIR / "index.html", UI_DIR / "Logbook.html") if p.is_file()), UI_DIR / "Logentra.html")


def _backend_name(url: str) -> str:
    if url.startswith("postgres"):
        return "postgresql"
    if url.startswith("sqlite"):
        return "sqlite"
    return url.split(":", 1)[0]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = make_engine(
        settings.sqlalchemy_url,
        pool_size=None if settings.sqlalchemy_url.startswith("sqlite") else settings.db_pool_size,
        max_overflow=None if settings.sqlalchemy_url.startswith("sqlite") else settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
    )
    SessionLocal = make_session_factory(engine)
    init_db(engine)
    # seed roles, optional first admin, default fields, startup webhooks
    with SessionLocal() as db:
        service.ensure_default_fields(db)
        if not db.get(Setting, "catalog_revision"):
            service.set_setting(db, "catalog_revision", uid("catalog"))
        seed.ensure_builtin_roles(db)
        seed.ensure_default_routes(db)
        seed.maybe_bootstrap_admin(db, settings)
        for wh in settings.outbound_webhooks:
            events.validate_destination(wh.url)
        if settings.outbound_webhooks and db.query(OutboundWebhook).count() == 0:
            for wh in settings.outbound_webhooks:
                db.add(OutboundWebhook(
                    id=uid("wh"), url=wh.url, secret=wh.secret or "",
                    events=dumps(wh.events or ["out_of_range"]),
                    enabled=1, created_at=utcnow_iso(),
                ))
        db.commit()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        delivery = events.DeliveryWorker(SessionLocal, settings)
        mqtt_client, mqtt_connected = mqtt_worker.start_mqtt(settings, SessionLocal)
        app.state.delivery_worker = delivery
        app.state.mqtt_connected = mqtt_connected
        delivery.start()
        escalation = handover_service.EscalationWorker(SessionLocal)
        escalation.start()
        try:
            import anyio
            anyio.to_thread.current_default_thread_limiter().total_tokens = settings.threadpool_size
        except (ImportError, AttributeError):
            pass
        try:
            yield
        finally:
            if mqtt_client:
                mqtt_client.disconnect()
                mqtt_client.loop_stop()
            escalation.stop()
            delivery.stop()
            engine.dispose()

    app = FastAPI(
        title=f"{PRODUCT_NAME} API",
        version=__version__,
        description=(
            f"On-prem open-source backend for {PRODUCT_NAME} {RELEASE_LABEL} — equipment, "
            "record templates, time-series readings, users/roles, work orders, and document approvals. "
            "SQLite by default; set LOGBOOK_DATABASE_URL to a PostgreSQL connection string to switch. "
            "Set LOGBOOK_AUTH_MODE=users to require login."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request, error):
        details = [{name: item[name] for name in ("loc", "msg", "type")} for item in error.errors()]
        return JSONResponse({"detail": details}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def duplicate_write(_request, error):
        # Safety net for any create path without its own pre-check. The session is
        # closed (and rolled back) by get_db; the constraint detail stays in the log.
        log.warning("Rejected conflicting write: %s", getattr(error, "orig", error))
        return JSONResponse(
            {"detail": "This item already exists or conflicts with another record."}, status_code=409
        )

    @app.exception_handler(StaleDataError)
    async def stale_write(_request, _error):
        return JSONResponse({"detail": "The record changed. Reload before saving."}, status_code=409)

    app.state.settings = settings
    app.state.engine = engine
    app.state.SessionLocal = SessionLocal

    app.add_middleware(HTTPSecurity, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_db() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    get_principal = make_auth_dependency(settings, SessionLocal)

    def perm(*names: str, scoped: bool = False):
        def _dep(principal: Principal = Depends(get_principal)) -> Principal:
            if not scoped:
                principal.require_global()
            principal.require(*names)
            return principal
        return [Depends(_dep)]

    def actor_name(request: Request) -> str:
        p = getattr(request.state, "principal", None)
        if p is not None:
            return p.actor
        return request.headers.get("X-Logentra-Actor") or request.headers.get("X-Logbook-Actor") or "api"

    def actor_user_id(request: Request) -> str | None:
        p = getattr(request.state, "principal", None)
        return p.user_id if p is not None else None

    # -------------- public / UI --------------
    @app.get("/api/health", tags=["system"], summary="Liveness and backend info")
    def health():
        auth_label = (settings.auth_mode or "open").lower()
        if auth_label == "open" and settings.api_key:
            auth_label = "api_key"
        return {
            "ok": True,
            "version": __version__,
            "product": PRODUCT_NAME,
            "backend": _backend_name(settings.database_url),
            "mqtt": bool(settings.mqtt.enabled),
            "auth": auth_label,
            "auth_mode": (settings.auth_mode or "open").lower(),
        }

    @app.get("/api/ready", tags=["system"])
    def readiness():
        from sqlalchemy.exc import SQLAlchemyError
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError:
            raise HTTPException(503, "Database unavailable") from None
        return {"ok": True}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        icon = UI_DIR / "static" / "favicon.ico"
        if icon.is_file():
            return FileResponse(icon, media_type="image/x-icon")
        return PlainTextResponse("", status_code=204)

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def apple_touch_icon():
        icon = UI_DIR / "static" / "apple-touch-icon.png"
        if icon.is_file():
            return FileResponse(icon, media_type="image/png")
        return PlainTextResponse("", status_code=404)

    @app.get("/", include_in_schema=False)
    def index():
        if UI_HTML.is_file():
            return FileResponse(UI_HTML, media_type="text/html")
        return PlainTextResponse("Logentra OS UI is not built yet. Run ui/build.py or see docs/README.md.", status_code=503)

    if (UI_DIR / "static").is_dir():
        app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")

    # Authenticated API routes (open mode still grants every permission)
    read_eq = perm("equipment.read")
    write_tpl = perm("templates.write")
    read_rd = perm("readings.read")
    manage_settings = perm("settings.manage")
    manage_hooks = perm("webhooks.manage")
    read_audit = perm("audit.read")

    app.include_router(auth_users.build_router(get_db, get_principal, settings))
    app.include_router(qr.build_router(get_db, get_principal, settings))
    app.include_router(shifts.build_router(get_db, get_principal))
    app.include_router(shift_log.build_router(get_db, get_principal, settings))
    app.include_router(handovers.build_router(get_db, get_principal))
    app.include_router(work_orders.build_router(get_db, get_principal, settings))
    app.include_router(readings.build_router(get_db, perm, settings, actor_name))
    app.include_router(documents.build_router(get_db, get_principal, settings))

    # -------------- bootstrap / catalog --------------
    @app.get("/api/bootstrap", tags=["catalog"], summary="Full app state (UI-compatible)", dependencies=perm("equipment.read", scoped=True))
    def get_bootstrap(request: Request, db: Session = Depends(get_db)):
        principal = request.state.principal
        state = service.bootstrap(
            db,
            backend=_backend_name(settings.database_url),
            equipment_cap=settings.bootstrap_equipment_cap,
            principal=getattr(request.state, "principal", None),
        )
        for permission, keys, total in (
            ("templates.read", ("templates",), None),
            ("records.read", ("records",), "recordTotal"),
            ("readings.read", ("logs",), "readingsTotal"),
        ):
            if not principal.can(permission):
                for key in keys:
                    state[key] = []
                if total:
                    state["meta"][total] = 0
        state["me"] = principal.to_public()
        state["auth_mode"] = (settings.auth_mode or "open").lower()
        return state



    @app.put("/api/sync", tags=["catalog"], summary="Upsert catalog (fields, equipment, templates, records). Does not delete readings.", dependencies=perm(scoped=True))
    def put_sync(body: SyncBody, request: Request, db: Session = Depends(get_db),
                 catalog_revision: str | None = Header(None, alias="If-Match")):
        principal = request.state.principal
        state = body.model_dump(exclude_none=True)
        for key, permission in {
            "equipment": "equipment.write", "templates": "templates.write", "records": "records.write",
            "equipmentFields": "settings.manage", "paramPresets": "settings.manage",
        }.items():
            if key in state:
                principal.require(permission)
        if settings.profile == "production" and catalog_revision is None:
            raise HTTPException(428, "If-Match catalog revision is required")
        return service.sync_catalog(db, state, actor=actor_name(request), expected=catalog_revision, principal=principal)

    @app.post("/api/demo", tags=["catalog"], summary="Replace catalog+readings with the fictional generic-plant demo", dependencies=manage_settings)
    def post_demo(request: Request, db: Session = Depends(get_db)):
        return demo.load_demo(db, actor=actor_name(request))

    @app.delete("/api/data", tags=["catalog"], summary="Clear all user data (keeps empty default columns, users, and roles)", dependencies=manage_settings)
    def delete_data(request: Request, db: Session = Depends(get_db)):
        service.clear_all(db, actor=actor_name(request))
        return {"ok": True}

    @app.post("/api/migrate/v2", tags=["catalog"], summary="Import a v0.2 JSON backup (JSON body) into the database", dependencies=manage_settings)
    async def post_migrate_v2(
        request: Request,
        db: Session = Depends(get_db),
        replace: bool = Query(True),
    ):
        try:
            data = await request.json()
            return migrate.migrate_v2(db, data, replace=replace, actor=actor_name(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/migrate/v2/file", tags=["catalog"], summary="Import a v0.2 JSON backup from an uploaded file", dependencies=manage_settings)
    async def post_migrate_v2_file(
        request: Request,
        db: Session = Depends(get_db),
        replace: bool = Query(True),
        file: UploadFile = File(...),
    ):
        import json as _json
        try:
            data = _json.loads((await read_upload(file, settings.upload_limit_bytes)).decode("utf-8-sig"))
            return migrate.migrate_v2(db, data, replace=replace, actor=actor_name(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # -------------- equipment & fields --------------
    @app.get("/api/fields", tags=["equipment"], dependencies=perm("equipment.read", scoped=True))
    def list_fields(db: Session = Depends(get_db)):
        return [service.field_to_ui(f) for f in db.query(FieldDefinition).order_by(FieldDefinition.sort_order).all()]

    @app.get("/api/equipment", tags=["equipment"], dependencies=perm("equipment.read", scoped=True))
    def list_equipment(
        request: Request,
        db: Session = Depends(get_db),
        q: str | None = None,
        site_id: str | None = None,
        area_id: str | None = None,
        category: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ):
        return service.list_equipment(
            db, q=q, site_id=site_id, area_id=area_id, category=category,
            page=page, page_size=page_size,
            principal=getattr(request.state, "principal", None),
            paginate=page is not None,
        )

    @app.post("/api/equipment/bulk", tags=["equipment"], dependencies=perm("equipment.write", scoped=True), status_code=201)
    def bulk_equipment(body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        service.advance_catalog(db)
        items = body.get("equipment") if isinstance(body, dict) and "equipment" in body else body.get("items") if isinstance(body, dict) else body
        if items is None:
            items = body if isinstance(body, list) else []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise HTTPException(422, "Equipment must contain objects")
        rows = service.upsert_equipment_list(db, [service.scoped_equipment_input(db, request.state.principal, item)
                                                  for item in items])
        service.audit(db, "create", actor=actor_name(request), user_id=actor_user_id(request), entity_type="equipment", detail={"count": len(rows)})
        db.commit()
        return {"added": len(rows), "ids": [r.id for r in rows], "items": [service.equipment_to_ui(r) for r in rows]}

    @app.get("/api/equipment/{eid}", tags=["equipment"], dependencies=perm("equipment.read", scoped=True))
    def get_equipment(eid: str, request: Request, db: Session = Depends(get_db)):
        eq = service.get_equipment(db, equipment_id=eid, tag=eid)
        if not eq or not request.state.principal.allows_scope(eq.site_id, eq.area_id):
            raise HTTPException(404, "Equipment not found")
        return service.equipment_to_ui(eq)

    @app.post("/api/equipment", tags=["equipment"], dependencies=perm("equipment.write", scoped=True), status_code=201)
    def create_equipment(body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        service.advance_catalog(db)
        rows = service.upsert_equipment_list(db, [service.scoped_equipment_input(db, request.state.principal, body)])
        service.audit(db, "create", actor=actor_name(request), entity_type="equipment", entity_id=rows[0].id)
        db.commit()
        return service.equipment_to_ui(rows[0])

    @app.patch("/api/equipment/{eid}", tags=["equipment"], dependencies=perm("equipment.write", scoped=True))
    def patch_equipment(eid: str, body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        service.advance_catalog(db)
        eq = db.get(Equipment, eid)
        if not eq:
            raise HTTPException(404, "Equipment not found")
        body = dict(body)
        body["id"] = eid
        service.upsert_equipment_list(db, [service.scoped_equipment_input(db, request.state.principal, body)])
        service.audit(db, "update", actor=actor_name(request), entity_type="equipment", entity_id=eid)
        db.commit()
        return service.equipment_to_ui(db.get(Equipment, eid))

    @app.delete("/api/equipment/{eid}", tags=["equipment"], dependencies=perm("equipment.write", scoped=True))
    def delete_equipment(eid: str, request: Request, db: Session = Depends(get_db),
                         catalog_revision: str | None = Header(None, alias="If-Match")):
        service.advance_catalog(db, catalog_revision)
        eq = db.get(Equipment, eid) or service.get_equipment(db, tag=eid)
        if not eq or not request.state.principal.allows_scope(eq.site_id, eq.area_id):
            raise HTTPException(404, "Equipment not found")
        if db.query(Signoff).join(Record).filter(Record.equipment_id == eq.id).first():
            raise HTTPException(409, "Equipment is linked to signed evidence")
        service.protect_document_links(db, Document.equipment_id, eq.id)
        db.delete(eq)
        service.audit(db, "delete", actor=actor_name(request), entity_type="equipment", entity_id=eq.id)
        db.commit()
        return {"ok": True}

    # -------------- templates & records --------------
    @app.get("/api/templates", tags=["records"], dependencies=perm("templates.read", scoped=True))
    def list_templates(
        db: Session = Depends(get_db),
        q: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ):
        return service.list_templates(db, q=q, page=page, page_size=page_size, paginate=page is not None)

    @app.post("/api/templates", tags=["records"], dependencies=write_tpl, status_code=201)
    def create_template(body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        service.advance_catalog(db)
        service.upsert_templates(db, [body])
        tid = body.get("id")
        t = db.get(Template, tid) if tid else db.query(Template).order_by(Template.created_at.desc()).first()
        service.audit(db, "create", actor=actor_name(request), entity_type="template", entity_id=t.id if t else None)
        db.commit()
        return service.template_to_ui(t)

    @app.delete("/api/templates/{tid}", tags=["records"], dependencies=write_tpl)
    def delete_template(tid: str, request: Request, db: Session = Depends(get_db),
                         catalog_revision: str | None = Header(None, alias="If-Match")):
        service.advance_catalog(db, catalog_revision)
        t = db.get(Template, tid)
        if not t:
            raise HTTPException(404, "Template not found")
        if db.query(Signoff).join(Record).filter(Record.template_id == t.id).first():
            raise HTTPException(409, "Template is linked to signed evidence")
        db.delete(t)
        service.audit(db, "delete", actor=actor_name(request), entity_type="template", entity_id=tid)
        db.commit()
        return {"ok": True}

    @app.get("/api/records", tags=["records"], dependencies=perm("records.read", scoped=True))
    def list_records(
        request: Request,
        db: Session = Depends(get_db),
        q: str | None = None,
        equipment_id: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ):
        return service.list_records(
            db, q=q, equipment_id=equipment_id, page=page, page_size=page_size,
            paginate=page is not None, principal=getattr(request.state, "principal", None),
        )

    @app.post("/api/records", tags=["records"], dependencies=perm("records.write", scoped=True), status_code=201)
    def create_record(body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        service.advance_catalog(db)
        service.require_record_scope(db, request.state.principal, body.get("id"), body.get("equipmentId"))
        service.require_signoff_permission(db, request.state.principal, body)
        service.upsert_records(db, [body])
        rid = body.get("id")
        r = db.get(Record, rid) if rid else db.query(Record).order_by(Record.created_at.desc()).first()
        service.audit(db, "create", actor=actor_name(request), entity_type="record", entity_id=r.id if r else None)
        db.commit()
        return service.record_to_ui(r)

    @app.delete("/api/records/{rid}", tags=["records"], dependencies=perm("records.write", scoped=True))
    def delete_record(rid: str, request: Request, db: Session = Depends(get_db),
                         catalog_revision: str | None = Header(None, alias="If-Match")):
        service.advance_catalog(db, catalog_revision)
        r = db.get(Record, rid)
        if not r:
            raise HTTPException(404, "Record not found")
        service.require_record_scope(db, request.state.principal, rid)
        if r.signoff:
            raise HTTPException(409, "Signed records cannot be deleted")
        service.protect_document_links(db, Document.record_id, r.id)
        db.delete(r)
        service.audit(db, "delete", actor=actor_name(request), entity_type="record", entity_id=rid, detail=service.record_to_ui(r))
        db.commit()
        return {"ok": True}

    # -------------- export --------------
    @app.get("/api/export/json", tags=["export"], summary="Full JSON backup (v0.2-compatible shape)", dependencies=read_rd)
    def export_json(request: Request, db: Session = Depends(get_db)):
        request.state.principal.require("equipment.read", "records.read", "templates.read")
        state = service.bootstrap(db, backend=_backend_name(settings.database_url), equipment_cap=10**9)
        return JSONResponse(
            state,
            headers={"Content-Disposition": "attachment; filename=logbook-backup.json"},
        )

    @app.get("/api/export/readings.csv", tags=["export"], dependencies=perm("readings.read", scoped=True))
    def export_readings_csv(
        request: Request,
        db: Session = Depends(get_db),
        tag: str | None = None,
        parameter: str | None = None,
        ts_from: str | None = Query(default=None, alias="from"),
        ts_to: str | None = Query(default=None, alias="to"),
    ):
        rows = service.query_readings(db, tag=tag, parameter=parameter, ts_from=ts_from, ts_to=ts_to, limit=100000, principal=request.state.principal)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["tag", "equipment_id", "date", "time", "ts", "parameter", "value", "min", "max",
                    "out_of_range", "unacknowledged", "remarks", "entered_by", "source",
                    "annot_cause", "annot_action", "annot_by", "annot_at", "reading_id", "entry_id"])
        for r in rows:
            an = r.get("annotation") or {}
            w.writerow([
                r.get("tag"), r["equipment_id"], r["date"], r["time"], r["ts"], r["parameter"], r["value"],
                r.get("min"), r.get("max"), int(r["out_of_range"]), int(r["unacknowledged"]),
                r.get("remarks"), r.get("entered_by"), r.get("source"),
                an.get("cause"), an.get("action"), an.get("by"), an.get("at"),
                r["id"], r["entry_id"],
            ])
        data = buf.getvalue().encode("utf-8-sig")
        return StreamingResponse(
            io.BytesIO(data), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=logbook-readings.csv"},
        )

    @app.get("/api/export/equipment.csv", tags=["export"], dependencies=perm("equipment.read", scoped=True))
    def export_equipment_csv(request: Request, db: Session = Depends(get_db)):
        fields = [service.field_to_ui(f) for f in db.query(FieldDefinition).order_by(FieldDefinition.sort_order)]
        equipment = service.list_equipment(db, principal=request.state.principal)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([f["label"] for f in fields])
        for e in equipment:
            w.writerow([e.get("values", {}).get(f["id"], "") for f in fields])
        data = buf.getvalue().encode("utf-8-sig")
        return StreamingResponse(
            io.BytesIO(data), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=logbook-equipment.csv"},
        )

    # -------------- outbound webhooks & audit --------------


    @app.get("/api/webhooks", tags=["integrations"], dependencies=manage_hooks)
    def list_webhooks(db: Session = Depends(get_db)):
        return [
            {"id": w.id, "url": w.url, "events": loads(w.events, []), "enabled": bool(w.enabled), "created_at": w.created_at}
            for w in db.query(OutboundWebhook).all()
        ]

    @app.post("/api/webhooks", tags=["integrations"], dependencies=manage_hooks, status_code=201)
    def create_webhook(body: WebhookIn, request: Request, db: Session = Depends(get_db)):
        try:
            destination = events.validate_destination(body.url)
            events.resolve_destination(destination, settings.webhook_private_destinations)
        except (ValueError, OSError):
            raise HTTPException(422, "Invalid or restricted webhook destination") from None
        w = OutboundWebhook(
            id=uid("wh"), url=body.url, secret=body.secret or "",
            events=dumps(body.events), enabled=1 if body.enabled else 0, created_at=utcnow_iso(),
        )
        db.add(w)
        service.audit(db, "create", actor=actor_name(request), entity_type="webhook", entity_id=w.id)
        db.commit()
        return {"id": w.id, "url": w.url, "events": body.events, "enabled": body.enabled}

    @app.delete("/api/webhooks/{wid}", tags=["integrations"], dependencies=manage_hooks)
    def delete_webhook(wid: str, request: Request, db: Session = Depends(get_db)):
        w = db.get(OutboundWebhook, wid)
        if not w:
            raise HTTPException(404, "Webhook not found")
        db.delete(w)
        service.audit(db, "delete", actor=actor_name(request), entity_type="webhook", entity_id=wid)
        db.commit()
        return {"ok": True}

    @app.get("/api/webhooks/deliveries", tags=["integrations"], dependencies=manage_hooks)
    def list_deliveries(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=500)):
        rows = db.query(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(limit).all()
        return [
            {"id": d.id, "webhook_id": d.webhook_id, "event": d.event, "ok": bool(d.ok),
             "status_code": d.status_code, "error": d.error, "created_at": d.created_at,
             "state": d.state, "attempts": d.attempts, "replays": d.replays}
            for d in rows
        ]

    @app.post("/api/webhooks/deliveries/{delivery_id}/replay", tags=["integrations"],
              dependencies=manage_hooks, status_code=202)
    def replay_delivery(delivery_id: str, body: ReplayIn, request: Request, db: Session = Depends(get_db)):
        if not db.get(WebhookDelivery, delivery_id):
            raise HTTPException(404, "Delivery not found")
        if not events.replay_dead_delivery(db, delivery_id):
            raise HTTPException(409, "Delivery is not eligible for replay")
        service.audit(db, "replay", actor=actor_name(request), user_id=actor_user_id(request),
                      entity_type="webhook_delivery", entity_id=delivery_id, detail={"reason": body.reason.strip()})
        db.commit()
        return {"id": delivery_id, "state": "pending"}

    @app.get("/api/integrations/health", tags=["integrations"], dependencies=manage_hooks)
    def integration_health(db: Session = Depends(get_db)):
        from sqlalchemy import func
        counts = dict(db.query(WebhookDelivery.state, func.count()).group_by(WebhookDelivery.state).all())
        mqtt_connected = getattr(app.state, "mqtt_connected", None)
        worker = getattr(app.state, "delivery_worker", None)
        return {"mqtt_enabled": settings.mqtt.enabled, "mqtt_connected": bool(mqtt_connected and mqtt_connected.is_set()),
                "delivery_worker": bool(worker and worker.thread.is_alive()), "deliveries": counts}

    @app.get("/api/audit", tags=["system"], dependencies=read_audit)
    def list_audit(
        db: Session = Depends(get_db),
        limit: int = 100,
        entity_type: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ):
        q = db.query(AuditLog).order_by(AuditLog.ts.desc())
        if entity_type:
            q = q.filter(AuditLog.entity_type == entity_type)
        if page is not None:
            from logentra_server.pagination import clamp_page, page_result
            page, page_size = clamp_page(page, page_size)
            total = q.count()
            rows = q.offset((page - 1) * page_size).limit(page_size).all()
            items = [
                {"id": a.id, "ts": a.ts, "actor": a.actor, "user_id": a.user_id, "action": a.action,
                 "entity_type": a.entity_type, "entity_id": a.entity_id, "detail": loads(a.detail)}
                for a in rows
            ]
            return page_result(items, total, page, page_size)
        rows = q.limit(limit).all()
        return [
            {"id": a.id, "ts": a.ts, "actor": a.actor, "user_id": a.user_id, "action": a.action,
             "entity_type": a.entity_type, "entity_id": a.entity_id, "detail": loads(a.detail)}
            for a in rows
        ]

    return app


# Use the factory: uvicorn logentra_server.app:create_app --factory
