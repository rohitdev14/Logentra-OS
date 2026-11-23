from __future__ import annotations

import csv
import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from logbook_server.auth import Principal, make_auth_dependency
from logbook_server.branding import PRODUCT_NAME, RELEASE_LABEL
from logbook_server.schemas import AnnotationIn, BulkReadingsIn, ReadingIn, SyncBody, WebhookIn
from sqlalchemy.orm import Session

from logbook_server import __version__, demo, events, importers, migrate, mqtt_worker, seed, service
from logbook_server.routers import auth_users, work_orders
from logbook_server.config import Settings, load_settings
from logbook_server.database import init_db, make_engine, make_session_factory
from logbook_server.models import (
    Annotation,
    AuditLog,
    Equipment,
    FieldDefinition,
    OutboundWebhook,
    Reading,
    Record,
    Template,
    WebhookDelivery,
)
from logbook_server.utils import dumps, loads, uid, utcnow_iso

log = logging.getLogger("logbook")
ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
UI_HTML = UI_DIR / "Logbook.html"


def _backend_name(url: str) -> str:
    if url.startswith("postgres"):
        return "postgresql"
    if url.startswith("sqlite"):
        return "sqlite"
    return url.split(":", 1)[0]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = make_engine(settings.sqlalchemy_url)
    SessionLocal = make_session_factory(engine)
    init_db(engine)
    # seed roles, optional first admin, default fields, startup webhooks
    with SessionLocal() as db:
        service.ensure_default_fields(db)
        seed.ensure_builtin_roles(db)
        seed.maybe_bootstrap_admin(db, settings)
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
        mqtt_worker.start_mqtt(settings, SessionLocal)
        yield

    app = FastAPI(
        title=f"{PRODUCT_NAME} API",
        version=__version__,
        description=(
            f"On-prem open-source backend for {PRODUCT_NAME} {RELEASE_LABEL} — equipment, "
            "record templates, time-series readings, users/roles, and work orders. "
            "SQLite by default; set LOGBOOK_DATABASE_URL to a PostgreSQL connection string to switch. "
            "Set LOGBOOK_AUTH_MODE=users to require login."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.SessionLocal = SessionLocal

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
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

    def perm(*names: str):
        def _dep(principal: Principal = Depends(get_principal)) -> Principal:
            principal.require(*names)
            return principal
        return [Depends(_dep)]

    def actor_name(request: Request) -> str:
        p = getattr(request.state, "principal", None)
        if p is not None:
            return p.actor
        return request.headers.get("X-Logbook-Actor") or "api"

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

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return PlainTextResponse("", status_code=204)

    @app.get("/", include_in_schema=False)
    def index():
        if UI_HTML.is_file():
            return FileResponse(UI_HTML, media_type="text/html")
        return PlainTextResponse("Logbook UI is not built yet. Run ui/build.py or see docs/README.md.", status_code=503)

    if (UI_DIR / "static").is_dir():
        app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")

    # Authenticated API routes (open mode still grants every permission)
    api_dep = perm()  # auth only
    read_eq = perm("equipment.read")
    write_eq = perm("equipment.write")
    read_rec = perm("records.read")
    write_rec = perm("records.write")
    write_tpl = perm("templates.write")
    read_tpl = perm("templates.read")
    read_rd = perm("readings.read")
    write_rd = perm("readings.write")
    write_ann = perm("annotations.write")
    manage_settings = perm("settings.manage")
    manage_hooks = perm("webhooks.manage")
    read_audit = perm("audit.read")

    app.include_router(auth_users.build_router(get_db, get_principal, settings))
    app.include_router(work_orders.build_router(get_db, get_principal, settings))

    # -------------- bootstrap / catalog --------------
    @app.get("/api/bootstrap", tags=["catalog"], summary="Full app state (UI-compatible)", dependencies=read_eq)
    def get_bootstrap(request: Request, db: Session = Depends(get_db)):
        state = service.bootstrap(
            db,
            backend=_backend_name(settings.database_url),
            equipment_cap=settings.bootstrap_equipment_cap,
            principal=getattr(request.state, "principal", None),
        )
        p = getattr(request.state, "principal", None)
        state["me"] = p.to_public() if p is not None else None
        state["auth_mode"] = (settings.auth_mode or "open").lower()
        return state



    @app.put("/api/sync", tags=["catalog"], summary="Upsert catalog (fields, equipment, templates, records). Does not delete readings.", dependencies=write_eq)
    def put_sync(body: SyncBody, request: Request, db: Session = Depends(get_db)):
        return service.sync_catalog(db, body.model_dump(exclude_none=True), actor=actor_name(request))

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
        data = await request.json()
        try:
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
        data = _json.loads((await file.read()).decode("utf-8-sig"))
        try:
            return migrate.migrate_v2(db, data, replace=replace, actor=actor_name(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # -------------- equipment & fields --------------
    @app.get("/api/fields", tags=["equipment"], dependencies=read_eq)
    def list_fields(db: Session = Depends(get_db)):
        return [service.field_to_ui(f) for f in db.query(FieldDefinition).order_by(FieldDefinition.sort_order).all()]

    @app.get("/api/equipment", tags=["equipment"], dependencies=read_eq)
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

    @app.post("/api/equipment/bulk", tags=["equipment"], dependencies=write_eq, status_code=201)
    def bulk_equipment(body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        items = body.get("equipment") if isinstance(body, dict) and "equipment" in body else body.get("items") if isinstance(body, dict) else body
        if items is None:
            items = body if isinstance(body, list) else []
        if isinstance(items, dict):
            items = [items]
        rows = service.upsert_equipment_list(db, items)
        service.audit(db, "create", actor=actor_name(request), user_id=actor_user_id(request), entity_type="equipment", detail={"count": len(rows)})
        db.commit()
        return {"added": len(rows), "ids": [r.id for r in rows], "items": [service.equipment_to_ui(r) for r in rows]}

    @app.get("/api/equipment/{eid}", tags=["equipment"], dependencies=read_eq)
    def get_equipment(eid: str, db: Session = Depends(get_db)):
        eq = service.get_equipment(db, equipment_id=eid, tag=eid)
        if not eq:
            raise HTTPException(404, "Equipment not found")
        return service.equipment_to_ui(eq)

    @app.post("/api/equipment", tags=["equipment"], dependencies=write_eq, status_code=201)
    def create_equipment(body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        rows = service.upsert_equipment_list(db, [body])
        service.audit(db, "create", actor=actor_name(request), entity_type="equipment", entity_id=rows[0].id)
        db.commit()
        return service.equipment_to_ui(rows[0])

    @app.patch("/api/equipment/{eid}", tags=["equipment"], dependencies=write_eq)
    def patch_equipment(eid: str, body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        eq = db.get(Equipment, eid)
        if not eq:
            raise HTTPException(404, "Equipment not found")
        body = dict(body)
        body["id"] = eid
        service.upsert_equipment_list(db, [body])
        service.audit(db, "update", actor=actor_name(request), entity_type="equipment", entity_id=eid)
        db.commit()
        return service.equipment_to_ui(db.get(Equipment, eid))

    @app.delete("/api/equipment/{eid}", tags=["equipment"], dependencies=write_eq)
    def delete_equipment(eid: str, request: Request, db: Session = Depends(get_db)):
        eq = db.get(Equipment, eid) or service.get_equipment(db, tag=eid)
        if not eq:
            raise HTTPException(404, "Equipment not found")
        db.delete(eq)
        service.audit(db, "delete", actor=actor_name(request), entity_type="equipment", entity_id=eq.id)
        db.commit()
        return {"ok": True}

    # -------------- templates & records --------------
    @app.get("/api/templates", tags=["records"], dependencies=read_tpl)
    def list_templates(
        db: Session = Depends(get_db),
        q: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ):
        return service.list_templates(db, q=q, page=page, page_size=page_size, paginate=page is not None)

    @app.post("/api/templates", tags=["records"], dependencies=write_tpl, status_code=201)
    def create_template(body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        service.upsert_templates(db, [body])
        tid = body.get("id")
        t = db.get(Template, tid) if tid else db.query(Template).order_by(Template.created_at.desc()).first()
        service.audit(db, "create", actor=actor_name(request), entity_type="template", entity_id=t.id if t else None)
        db.commit()
        return service.template_to_ui(t)

    @app.delete("/api/templates/{tid}", tags=["records"], dependencies=write_tpl)
    def delete_template(tid: str, request: Request, db: Session = Depends(get_db)):
        t = db.get(Template, tid)
        if not t:
            raise HTTPException(404, "Template not found")
        db.delete(t)
        service.audit(db, "delete", actor=actor_name(request), entity_type="template", entity_id=tid)
        db.commit()
        return {"ok": True}

    @app.get("/api/records", tags=["records"], dependencies=read_rec)
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

    @app.post("/api/records", tags=["records"], dependencies=write_rec, status_code=201)
    def create_record(body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        service.upsert_records(db, [body])
        rid = body.get("id")
        r = db.get(Record, rid) if rid else db.query(Record).order_by(Record.created_at.desc()).first()
        service.audit(db, "create", actor=actor_name(request), entity_type="record", entity_id=r.id if r else None)
        db.commit()
        return service.record_to_ui(r)

    @app.delete("/api/records/{rid}", tags=["records"], dependencies=write_rec)
    def delete_record(rid: str, request: Request, db: Session = Depends(get_db)):
        r = db.get(Record, rid)
        if not r:
            raise HTTPException(404, "Record not found")
        db.delete(r)
        service.audit(db, "delete", actor=actor_name(request), entity_type="record", entity_id=rid)
        db.commit()
        return {"ok": True}

    # -------------- readings --------------




    @app.get("/api/readings", tags=["readings"], summary="Query time-series readings", dependencies=read_rd)
    def get_readings(
        db: Session = Depends(get_db),
        equipment_id: str | None = None,
        tag: str | None = None,
        parameter: str | None = None,
        ts_from: str | None = Query(default=None, alias="from"),
        ts_to: str | None = Query(default=None, alias="to"),
        source: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ):
        rows = service.query_readings(
            db, equipment_id=equipment_id, tag=tag, parameter=parameter,
            ts_from=ts_from, ts_to=ts_to, source=source, limit=limit, offset=offset,
        )
        return {"count": len(rows), "readings": rows}

    @app.post("/api/readings", tags=["readings"], summary="Insert one reading", dependencies=write_rd, status_code=201)
    def post_reading(body: ReadingIn, request: Request, db: Session = Depends(get_db)):
        result = service.ingest_readings(
            db, [body.model_dump(exclude_none=True)], source=body.source or "api",
            actor=actor_name(request), create_missing_equipment=False, fire_webhooks=True,
        )
        if result["added"] == 0:
            raise HTTPException(400, result)
        return result

    @app.post("/api/readings/bulk", tags=["readings"], summary="Insert many readings in one transaction", dependencies=write_rd)
    def post_readings_bulk(body: BulkReadingsIn, request: Request, db: Session = Depends(get_db)):
        items = [r.model_dump(exclude_none=True) for r in body.readings]
        return service.ingest_readings(
            db, items, source="api", actor=actor_name(request),
            create_missing_equipment=body.create_missing_equipment, fire_webhooks=True,
        )

    @app.delete("/api/readings/{rid}", tags=["readings"], dependencies=write_rd)
    def delete_reading(rid: str, request: Request, db: Session = Depends(get_db)):
        r = db.get(Reading, rid)
        if not r:
            # treat as entry_id so the UI can delete a whole log line
            n = service.delete_entry(db, rid)
            if not n:
                raise HTTPException(404, "Reading not found")
            service.audit(db, "delete", actor=actor_name(request), entity_type="entry", entity_id=rid)
            return {"ok": True, "deleted": n}
        db.delete(r)
        service.audit(db, "delete", actor=actor_name(request), entity_type="reading", entity_id=rid)
        db.commit()
        return {"ok": True, "deleted": 1}



    @app.put("/api/readings/{rid}/annotation", tags=["readings"], summary="Add or edit an annotation", dependencies=write_ann)
    def put_annotation(rid: str, body: AnnotationIn, request: Request, db: Session = Depends(get_db)):
        try:
            an = service.upsert_annotation(db, rid, body.model_dump(), actor=actor_name(request))
        except KeyError:
            raise HTTPException(404, "Reading not found") from None
        return {"id": an.id, "cause": an.cause, "action": an.action, "by": an.annotated_by, "at": an.annotated_at}

    @app.put("/api/logs/{entry_id}/annotations/{parameter}", tags=["readings"],
             summary="Add or edit an annotation on a grouped log entry + parameter", dependencies=write_ann)
    def put_log_annotation(entry_id: str, parameter: str, body: AnnotationIn, request: Request, db: Session = Depends(get_db)):
        r = db.query(Reading).filter_by(entry_id=entry_id, parameter_key=parameter).order_by(Reading.ts.desc()).first()
        if not r:
            raise HTTPException(404, "Reading not found for that log entry and parameter")
        an = service.upsert_annotation(db, r.id, body.model_dump(), actor=actor_name(request))
        return {"id": an.id, "reading_id": r.id, "cause": an.cause, "action": an.action, "by": an.annotated_by, "at": an.annotated_at}

    @app.delete("/api/logs/{entry_id}/annotations/{parameter}", tags=["readings"], dependencies=write_ann)
    def delete_log_annotation(entry_id: str, parameter: str, request: Request, db: Session = Depends(get_db)):
        r = db.query(Reading).filter_by(entry_id=entry_id, parameter_key=parameter).first()
        if not r or not r.annotation:
            raise HTTPException(404, "Annotation not found")
        aid = r.annotation.id
        db.delete(r.annotation)
        service.audit(db, "delete", actor=actor_name(request), entity_type="annotation", entity_id=aid)
        db.commit()
        return {"ok": True}

    @app.delete("/api/annotations/{aid}", tags=["readings"], dependencies=write_ann)
    def delete_annotation(aid: str, request: Request, db: Session = Depends(get_db)):
        an = db.get(Annotation, aid)
        if not an:
            raise HTTPException(404, "Annotation not found")
        db.delete(an)
        service.audit(db, "delete", actor=actor_name(request), entity_type="annotation", entity_id=aid)
        db.commit()
        return {"ok": True}


    @app.get("/api/logs", tags=["readings"], summary="Paginated grouped log-book lines", dependencies=read_rd)
    def list_logs(
        request: Request,
        db: Session = Depends(get_db),
        equipment_id: str | None = None,
        q: str | None = None,
        ts_from: str | None = Query(default=None, alias="from"),
        ts_to: str | None = Query(default=None, alias="to"),
        page: int = 1,
        page_size: int = 50,
    ):
        return service.list_logs(
            db, equipment_id=equipment_id, q=q, ts_from=ts_from, ts_to=ts_to,
            page=page, page_size=page_size, principal=getattr(request.state, "principal", None),
        )

    @app.get(
        "/api/events/out-of-range",
        tags=["readings"],
        summary="Out-of-range readings, optionally only unacknowledged (no annotation yet)",
        dependencies=read_rd,
    )
    def get_oor_events(
        db: Session = Depends(get_db),
        unacknowledged: bool | None = None,
        equipment_id: str | None = None,
        tag: str | None = None,
        ts_from: str | None = Query(default=None, alias="from"),
        ts_to: str | None = Query(default=None, alias="to"),
        limit: int = 500,
    ):
        rows = service.query_events(
            db, unacknowledged=unacknowledged, equipment_id=equipment_id,
            tag=tag, ts_from=ts_from, ts_to=ts_to, limit=limit,
        )
        return {"count": len(rows), "events": rows}

    # -------------- IoT ingest (webhook) --------------
    @app.post(
        "/api/ingest/readings",
        tags=["ingest"],
        summary="IoT / gateway webhook. Accepts one reading, a list, or {readings: [...]}.",
        dependencies=write_rd,
    )
    async def ingest_webhook(
        request: Request,
        db: Session = Depends(get_db),
        create_missing_equipment: bool = Query(False),
    ):
        payload = await request.json()
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict) and "readings" in payload:
            items = payload["readings"]
            create_missing_equipment = bool(payload.get("create_missing_equipment", create_missing_equipment))
        elif isinstance(payload, dict):
            items = [payload]
        else:
            raise HTTPException(400, "Expected a JSON object or array")
        return service.ingest_readings(
            db, items, source="webhook", actor=actor_name(request) or "webhook",
            create_missing_equipment=create_missing_equipment, fire_webhooks=True,
        )

    @app.post("/api/ingest/file", tags=["ingest"], summary="Upload a CSV or Excel table of equipment or readings", dependencies=write_rd)
    async def ingest_file(
        request: Request,
        db: Session = Depends(get_db),
        file: UploadFile = File(...),
        kind: str = Query("logs", description="equipment | logs"),
        log_mode: str = Query("long", description="long (tag,date,parameter,value) or wide"),
        create_missing_equipment: bool = Query(True),
        mapping: str | None = Query(default=None, description="Optional JSON object header->field"),
    ):
        raw = await file.read()
        name = file.filename or "upload"
        if name.lower().endswith(".xlsx"):
            _sheet, rows = importers.parse_xlsx_bytes(raw)
        else:
            rows = importers.parse_csv_bytes(raw)
        map_obj = loads(mapping, None) if mapping else None
        if kind == "equipment":
            # default map headers to themselves; tag is required
            dicts = importers.rows_to_dicts(rows, map_obj)
            fields = {f.key: f for f in db.query(FieldDefinition).all()}
            # ensure columns exist
            items = []
            for rec in dicts:
                values = {}
                # try common names
                norm = {k.lower().replace(" ", "_"): v for k, v in rec.items()}
                for key, field in fields.items():
                    if key in rec:
                        values[field.id] = rec[key]
                    elif key in norm:
                        values[field.id] = norm[key]
                    elif field.label.lower() in {k.lower() for k in rec}:
                        for k, v in rec.items():
                            if k.lower() == field.label.lower():
                                values[field.id] = v
                # leftover headers become new text columns? keep simple: only known + Tag
                if "tag" in norm and fields.get("tag"):
                    values[fields["tag"].id] = values.get(fields["tag"].id) or norm["tag"]
                items.append({"values": values, "params": []})
            service.upsert_equipment_list(db, items)
            service.audit(db, "ingest", actor=actor_name(request), entity_type="equipment", detail={"n": len(items), "file": name})
            db.commit()
            return {"ok": True, "kind": "equipment", "added": len(items)}
        # logs — long mode uses header names tag, date, time, parameter, value
        dicts = importers.rows_to_dicts(rows, map_obj)
        if log_mode == "wide":
            items = []
            reserved = {"tag", "date", "time", "remarks", "entered_by", "enteredby", "equipment"}
            for rec in dicts:
                lower = {k.lower(): v for k, v in rec.items()}
                shared = {
                    "tag": lower.get("tag") or lower.get("equipment"),
                    "date": lower.get("date"),
                    "time": lower.get("time"),
                    "remarks": lower.get("remarks"),
                    "entered_by": lower.get("entered_by") or lower.get("enteredby"),
                }
                entry_id = uid("log")
                for k, v in rec.items():
                    if k.lower() in reserved:
                        continue
                    if v == "" or v is None:
                        continue
                    item = dict(shared)
                    item.update({"parameter": k, "value": v, "entry_id": entry_id})
                    items.append(item)
        else:
            items = []
            for rec in dicts:
                lower = {k.lower(): v for k, v in rec.items()}
                items.append({
                    "tag": lower.get("tag") or lower.get("equipment"),
                    "date": lower.get("date"),
                    "time": lower.get("time"),
                    "parameter": lower.get("parameter") or lower.get("param") or lower.get("key"),
                    "value": lower.get("value") or lower.get("reading"),
                    "remarks": lower.get("remarks"),
                    "entered_by": lower.get("entered_by") or lower.get("enteredby") or lower.get("by"),
                    "unit": lower.get("unit"),
                    "annot_cause": lower.get("annot_cause") or lower.get("cause"),
                    "annot_action": lower.get("annot_action") or lower.get("action"),
                    "annot_by": lower.get("annot_by"),
                    "annot_at": lower.get("annot_at"),
                })
        return service.ingest_readings(
            db, items, source="import", actor=actor_name(request),
            create_missing_equipment=create_missing_equipment, fire_webhooks=True,
        )

    # -------------- export --------------
    @app.get("/api/export/json", tags=["export"], summary="Full JSON backup (v0.2-compatible shape)", dependencies=read_rd)
    def export_json(db: Session = Depends(get_db)):
        state = service.bootstrap(db, backend=_backend_name(settings.database_url), equipment_cap=10**9)
        return JSONResponse(
            state,
            headers={"Content-Disposition": "attachment; filename=logbook-backup.json"},
        )

    @app.get("/api/export/readings.csv", tags=["export"], dependencies=read_rd)
    def export_readings_csv(
        db: Session = Depends(get_db),
        tag: str | None = None,
        parameter: str | None = None,
        ts_from: str | None = Query(default=None, alias="from"),
        ts_to: str | None = Query(default=None, alias="to"),
    ):
        rows = service.query_readings(db, tag=tag, parameter=parameter, ts_from=ts_from, ts_to=ts_to, limit=100000)
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

    @app.get("/api/export/equipment.csv", tags=["export"], dependencies=read_eq)
    def export_equipment_csv(db: Session = Depends(get_db)):
        state = service.bootstrap(db, equipment_cap=10**9)
        fields = state["equipmentFields"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([f["label"] for f in fields])
        for e in state["equipment"]:
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
    def list_deliveries(db: Session = Depends(get_db), limit: int = 50):
        rows = db.query(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(limit).all()
        return [
            {"id": d.id, "webhook_id": d.webhook_id, "event": d.event, "ok": bool(d.ok),
             "status_code": d.status_code, "error": d.error, "created_at": d.created_at}
            for d in rows
        ]

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
            from logbook_server.pagination import clamp_page, page_result
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


# Use the factory: uvicorn logbook_server.app:create_app --factory
