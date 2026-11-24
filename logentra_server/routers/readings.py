"""Reading queries, annotations, and ingestion endpoints."""
from __future__ import annotations


from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from logentra_server import importers, service
from logentra_server.http_security import read_upload
from logentra_server.models import Annotation, FieldDefinition, Reading
from logentra_server.schemas import AnnotationIn, BulkReadingsIn, ReadingIn
from logentra_server.utils import loads, uid


def build_router(get_db, perm, settings, actor_name) -> APIRouter:
    router = APIRouter()
    read_rd = perm("readings.read")
    write_rd = perm("readings.write")
    write_ann = perm("annotations.write")
    # -------------- readings --------------




    @router.get("/api/readings", tags=["readings"], summary="Query time-series readings", dependencies=perm("readings.read", scoped=True))
    def get_readings(
        request: Request,
        db: Session = Depends(get_db),
        equipment_id: str | None = None,
        tag: str | None = None,
        parameter: str | None = None,
        ts_from: str | None = Query(default=None, alias="from"),
        ts_to: str | None = Query(default=None, alias="to"),
        source: str | None = None,
        limit: int = Query(1000, ge=1, le=10000),
        offset: int = Query(0, ge=0),
    ):
        rows = service.query_readings(
            db, equipment_id=equipment_id, tag=tag, parameter=parameter,
            ts_from=ts_from, ts_to=ts_to, source=source, limit=limit, offset=offset, principal=request.state.principal,
        )
        return {"count": len(rows), "readings": rows}

    @router.post("/api/readings", tags=["readings"], summary="Insert one reading", dependencies=write_rd, status_code=201)
    def post_reading(body: ReadingIn, request: Request, db: Session = Depends(get_db)):
        result = service.ingest_readings(
            db, [body.model_dump(exclude_none=True)], source=body.source or "api",
            actor=actor_name(request), create_missing_equipment=False, fire_webhooks=True,
        )
        if result["added"] == 0 and not any(row["reason"] == "duplicate" for row in result["skipped_items"]):
            raise HTTPException(400, result)
        return result

    @router.post("/api/readings/bulk", tags=["readings"], summary="Insert many readings in one transaction", dependencies=write_rd)
    def post_readings_bulk(body: BulkReadingsIn, request: Request, db: Session = Depends(get_db)):
        if body.create_missing_equipment:
            request.state.principal.require("equipment.write")
        items = [r.model_dump(exclude_none=True) for r in body.readings]
        return service.ingest_readings(
            db, items, source="api", actor=actor_name(request),
            create_missing_equipment=body.create_missing_equipment, fire_webhooks=True,
        )

    @router.delete("/api/readings/{rid}", tags=["readings"], dependencies=write_rd)
    def delete_reading(rid: str, request: Request, db: Session = Depends(get_db)):
        r = db.get(Reading, rid)
        if not r:
            # treat as entry_id so the UI can delete a whole log line
            n = service.delete_entry(db, rid)
            if not n:
                raise HTTPException(404, "Reading not found")
            service.audit(db, "delete", actor=actor_name(request), entity_type="entry", entity_id=rid)
            db.commit()
            return {"ok": True, "deleted": n}
        db.delete(r)
        service.audit(db, "delete", actor=actor_name(request), entity_type="reading", entity_id=rid)
        db.commit()
        return {"ok": True, "deleted": 1}



    @router.put("/api/readings/{rid}/annotation", tags=["readings"], summary="Add or edit an annotation", dependencies=write_ann)
    def put_annotation(rid: str, body: AnnotationIn, request: Request, db: Session = Depends(get_db)):
        try:
            an = service.upsert_annotation(db, rid, body.model_dump(), actor=actor_name(request))
        except KeyError:
            raise HTTPException(404, "Reading not found") from None
        return {"id": an.id, "cause": an.cause, "action": an.action, "by": an.annotated_by, "at": an.annotated_at}

    @router.put("/api/logs/{entry_id}/annotations/{parameter}", tags=["readings"],
             summary="Add or edit an annotation on a grouped log entry + parameter", dependencies=write_ann)
    def put_log_annotation(entry_id: str, parameter: str, body: AnnotationIn, request: Request, db: Session = Depends(get_db)):
        r = db.query(Reading).filter_by(entry_id=entry_id, parameter_key=parameter).order_by(Reading.ts.desc()).first()
        if not r:
            raise HTTPException(404, "Reading not found for that log entry and parameter")
        an = service.upsert_annotation(db, r.id, body.model_dump(), actor=actor_name(request))
        return {"id": an.id, "reading_id": r.id, "cause": an.cause, "action": an.action, "by": an.annotated_by, "at": an.annotated_at}

    @router.delete("/api/logs/{entry_id}/annotations/{parameter}", tags=["readings"], dependencies=write_ann)
    def delete_log_annotation(entry_id: str, parameter: str, request: Request, db: Session = Depends(get_db)):
        r = db.query(Reading).filter_by(entry_id=entry_id, parameter_key=parameter).first()
        if not r or not r.annotation:
            raise HTTPException(404, "Annotation not found")
        aid = r.annotation.id
        db.delete(r.annotation)
        service.audit(db, "delete", actor=actor_name(request), entity_type="annotation", entity_id=aid)
        db.commit()
        return {"ok": True}

    @router.delete("/api/annotations/{aid}", tags=["readings"], dependencies=write_ann)
    def delete_annotation(aid: str, request: Request, db: Session = Depends(get_db)):
        an = db.get(Annotation, aid)
        if not an:
            raise HTTPException(404, "Annotation not found")
        db.delete(an)
        service.audit(db, "delete", actor=actor_name(request), entity_type="annotation", entity_id=aid)
        db.commit()
        return {"ok": True}


    @router.get("/api/logs", tags=["readings"], summary="Paginated grouped log-book lines", dependencies=perm("readings.read", scoped=True))
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

    @router.get(
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
    @router.post(
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
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "Invalid JSON") from None
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict) and "readings" in payload:
            items = payload["readings"]
            create_missing_equipment = bool(payload.get("create_missing_equipment", create_missing_equipment))
        elif isinstance(payload, dict):
            items = [payload]
        else:
            raise HTTPException(400, "Expected a JSON object or array")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise HTTPException(422, "readings must contain objects")
        if create_missing_equipment:
            request.state.principal.require("equipment.write")
        return service.ingest_readings(
            db, items, source="webhook", actor=actor_name(request) or "webhook",
            create_missing_equipment=create_missing_equipment, fire_webhooks=True,
        )

    @router.post("/api/ingest/file", tags=["ingest"], summary="Upload a CSV or Excel table of equipment or readings", dependencies=write_rd)
    async def ingest_file(
        request: Request,
        db: Session = Depends(get_db),
        file: UploadFile = File(...),
        kind: str = Query("logs", description="equipment | logs"),
        log_mode: str = Query("long", description="long (tag,date,parameter,value) or wide"),
        create_missing_equipment: bool = Query(True),
        mapping: str | None = Query(default=None, description="Optional JSON object header->field"),
    ):
        if create_missing_equipment or kind == "equipment":
            request.state.principal.require("equipment.write")
        raw = await read_upload(file, settings.upload_limit_bytes)
        name = file.filename or "upload"
        try:
            if name.lower().endswith(".xlsx"):
                _sheet, rows = importers.parse_xlsx_bytes(raw, max_expanded_bytes=settings.upload_limit_bytes * 4)
            elif name.lower().endswith(".csv"):
                rows = importers.parse_csv_bytes(raw)
            else:
                raise HTTPException(415, "Expected a CSV or XLSX file")
        except (ValueError, OSError) as exc:
            raise HTTPException(400, "Invalid import file") from exc
        map_obj = loads(mapping, None) if mapping else None
        if kind == "equipment":
            service.advance_catalog(db)
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

    return router
