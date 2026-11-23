"""Import a v0.2 Logbook JSON backup into the v0.3 database."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from logbook_server import service


def migrate_v2(db: Session, data: dict[str, Any], *, replace: bool = True, actor: str = "migrate") -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object (a v0.2 backup).")
    # Accept a raw state or a wrapper {state: ...}
    state = data.get("state") if isinstance(data.get("state"), dict) and "equipment" in data.get("state", {}) else data
    if replace:
        service.clear_all(db, actor=actor)

    catalog = {
        "equipmentFields": state.get("equipmentFields") or service.default_fields(),
        "equipment": state.get("equipment") or [],
        "templates": state.get("templates") or [],
        "records": state.get("records") or [],
        "paramPresets": state.get("paramPresets") or {},
    }
    service.sync_catalog(db, catalog, actor=actor)

    items = []
    for log in state.get("logs") or []:
        eid = log.get("equipmentId") or log.get("equipment_id")
        entry_id = log.get("id") or None
        readings = log.get("readings") or {}
        anns = log.get("annotations") or {}
        for key, val in readings.items():
            rec = {
                "id": None,
                "entry_id": entry_id,
                "equipment_id": eid,
                "tag": log.get("tag"),
                "parameter": key,
                "date": log.get("date"),
                "time": log.get("time") or "08:00",
                "value": val,
                "remarks": log.get("remarks") or "",
                "entered_by": log.get("enteredBy") or log.get("entered_by") or "",
                "source": "import",
            }
            an = anns.get(key) or {}
            if an:
                rec["annot_cause"] = an.get("cause")
                rec["annot_action"] = an.get("action")
                rec["annot_by"] = an.get("by")
                rec["annot_at"] = an.get("at")
            items.append(rec)
    result = {"added": 0, "skipped": 0}
    if items:
        result = service.ingest_readings(db, items, source="import", actor=actor, fire_webhooks=False)
    service.set_setting(db, "demo", "1" if (state.get("meta") or {}).get("demo") else "0")
    service.set_setting(db, "migrated_from", "v0.2")
    db.commit()
    return {
        "ok": True,
        "equipment": len(catalog["equipment"]),
        "templates": len(catalog["templates"]),
        "records": len(catalog["records"]),
        "readings": result.get("added", 0),
        "skipped": result.get("skipped", 0),
    }
