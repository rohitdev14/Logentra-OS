"""Fictional generic-plant demo data (same story as v0.2, written to the database)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from logentra_server import service, sitetime
from logentra_server.utils import utcnow_iso


def _add_days(iso: str, delta: int) -> str:
    dt = datetime.strptime(iso, "%Y-%m-%d") + timedelta(days=delta)
    return dt.strftime("%Y-%m-%d")


def _seeded(n: int) -> float:
    x = math.sin(n * 12.9898) * 43758.5453
    return x - math.floor(x)



def _simple_pdf(title: str, lines: list[str]) -> bytes:
    """Build a tiny single-page PDF without extra dependencies."""
    def esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    y = 800
    cmds = [
        "BT /F1 16 Tf 48 " + str(y) + " Td (" + esc(title) + ") Tj ET",
    ]
    y -= 28
    for line in lines:
        cmds.append("BT /F1 10 Tf 48 " + str(y) + " Td (" + esc(line) + ") Tj ET")
        y -= 14
        if y < 60:
            break
    stream = "\n".join(cmds).encode("latin-1", "replace")
    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )
    objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


def load_demo(db: Session, *, actor: str = "ui", user_id: str | None = None) -> dict:
    service.clear_all(db, actor=actor)
    today = sitetime.today_local(sitetime.zone_for_site(db, "site_northbrook"))
    start = _add_days(today, -20)
    now = utcnow_iso()

    state = {
        "equipmentFields": [
            {"id": "fld_tag", "key": "tag", "label": "Tag", "type": "text", "locked": True},
            {"id": "fld_category", "key": "category", "label": "Category", "type": "text"},
            {"id": "fld_location", "key": "location", "label": "Location", "type": "text"},
            {
                "id": "fld_status", "key": "status", "label": "Status", "type": "select",
                "options": ["In service", "Spare", "Out of service"],
            },
        ],
        "equipment": [
            {
                "id": "eq_p101",
                "values": {
                    "fld_tag": "P-101", "fld_category": "Pump",
                    "fld_location": "Pump house — Northbrook Works (fictional)", "fld_status": "In service",
                },
                "params": [
                    {"key": "flow", "label": "Flow", "unit": "m³/h", "min": 40, "max": 60},
                    {"key": "discharge_p", "label": "Discharge pressure", "unit": "bar", "min": 2.5, "max": 4.0},
                    {"key": "vibration", "label": "Vibration", "unit": "mm/s", "min": "", "max": 4.5},
                    {"key": "bearing_t", "label": "Bearing temperature", "unit": "°C", "min": "", "max": 80},
                ],
                "createdAt": start,
            },
            {
                "id": "eq_m204",
                "values": {
                    "fld_tag": "M-204", "fld_category": "Motor",
                    "fld_location": "Compressor hall", "fld_status": "In service",
                },
                "params": [
                    {"key": "current", "label": "Current", "unit": "A", "min": 10, "max": 18},
                    {"key": "winding_t", "label": "Winding temperature", "unit": "°C", "min": "", "max": 90},
                    {"key": "vibration", "label": "Vibration", "unit": "mm/s", "min": "", "max": 4.5},
                ],
                "createdAt": start,
            },
            {
                "id": "eq_ahu12",
                "values": {
                    "fld_tag": "AHU-12", "fld_category": "HVAC",
                    "fld_location": "Roof plant", "fld_status": "In service",
                },
                "params": [
                    {"key": "supply_t", "label": "Supply air temp", "unit": "°C", "min": 16, "max": 22},
                    {"key": "filter_dp", "label": "Filter DP", "unit": "Pa", "min": "", "max": 250},
                    {"key": "rh", "label": "Relative humidity", "unit": "% RH", "min": 35, "max": 60},
                ],
                "createdAt": start,
            },
            {
                "id": "eq_tk03",
                "values": {
                    "fld_tag": "TK-03", "fld_category": "Tank",
                    "fld_location": "Yard", "fld_status": "Spare",
                },
                "params": [{"key": "level", "label": "Level", "unit": "%", "min": 10, "max": 90}],
                "createdAt": start,
            },
        ],
        "templates": [{
            "id": "tpl_pump", "name": "Pump inspection", "kind": "inspection", "category": "Pump",
            "columns": [
                {"id": "col_result", "key": "result", "label": "Result", "type": "passfail"},
                {"id": "col_reading", "key": "reading", "label": "Reading", "type": "number"},
                {"id": "col_unit", "key": "unit", "label": "Unit", "type": "text"},
                {"id": "col_remarks", "key": "remarks", "label": "Remarks", "type": "text"},
            ],
            "rows": [
                {"id": "ti1", "label": "Visual condition and leaks", "hint": "Walk around the unit, base, and seals."},
                {"id": "ti2", "label": "Unusual noise or vibration", "hint": "Compare to the last running condition."},
                {"id": "ti3", "label": "Isolation valves in the intended position", "hint": "Record as-left in remarks."},
                {"id": "ti4", "label": "Coupling / guard in place and secure", "hint": "Do not remove a guard while running."},
                {"id": "ti5", "label": "Operating readings recorded in the log book", "hint": "Flow, pressure, temperature if fitted."},
            ],
        }],
        "records": [
            {
                "id": "rec_demo_1", "templateId": "tpl_pump", "templateName": "Pump inspection",
                "kind": "inspection", "equipmentId": "eq_p101", "createdAt": _add_days(today, -3),
                "columns": [
                    {"id": "col_result", "key": "result", "label": "Result", "type": "passfail"},
                    {"id": "col_reading", "key": "reading", "label": "Reading", "type": "number"},
                    {"id": "col_unit", "key": "unit", "label": "Unit", "type": "text"},
                    {"id": "col_remarks", "key": "remarks", "label": "Remarks", "type": "text"},
                ],
                "items": [
                    {"id": "ti1", "label": "Visual condition and leaks", "hint": "Walk around the unit, base, and seals.",
                     "values": {"col_result": "Pass", "col_reading": "", "col_unit": "", "col_remarks": "No visible leak (fictional demo)."}},
                    {"id": "ti2", "label": "Unusual noise or vibration", "hint": "Compare to the last running condition.",
                     "values": {"col_result": "Pass", "col_reading": "", "col_unit": "", "col_remarks": ""}},
                    {"id": "ti3", "label": "Isolation valves in the intended position", "hint": "Record as-left in remarks.",
                     "values": {"col_result": "Pass", "col_reading": "", "col_unit": "", "col_remarks": ""}},
                    {"id": "ti4", "label": "Coupling / guard in place and secure", "hint": "Do not remove a guard while running.",
                     "values": {"col_result": "Pass", "col_reading": "", "col_unit": "", "col_remarks": ""}},
                    {"id": "ti5", "label": "Operating readings recorded in the log book", "hint": "Flow, pressure, temperature if fitted.",
                     "values": {"col_result": "Pass", "col_reading": "52", "col_unit": "m³/h", "col_remarks": ""}},
                ],
                "punches": [],
                "signoffName": "Amir Rahman",
                "signoffDate": _add_days(today, -3),
                "notes": "Fictional completed inspection.",
            },
            {
                "id": "rec_demo_2", "templateId": "tpl_pump", "templateName": "Pump inspection",
                "kind": "inspection", "equipmentId": "eq_ahu12", "createdAt": _add_days(today, -1),
                "columns": [
                    {"id": "col_result", "key": "result", "label": "Result", "type": "passfail"},
                    {"id": "col_reading", "key": "reading", "label": "Reading", "type": "number"},
                    {"id": "col_unit", "key": "unit", "label": "Unit", "type": "text"},
                    {"id": "col_remarks", "key": "remarks", "label": "Remarks", "type": "text"},
                ],
                "items": [
                    {"id": "h1", "label": "Filter condition and differential pressure", "hint": "Record DP if a gauge is fitted.",
                     "values": {"col_result": "Pass", "col_reading": "140", "col_unit": "Pa", "col_remarks": ""}},
                    {"id": "h2", "label": "Drains clear; no standing water", "hint": "Visual check.",
                     "values": {"col_result": "", "col_reading": "", "col_unit": "", "col_remarks": ""}},
                    {"id": "h3", "label": "Fans run; no abnormal noise", "hint": "Listen and observe.",
                     "values": {"col_result": "", "col_reading": "", "col_unit": "", "col_remarks": ""}},
                ],
                "punches": [{
                    "id": "pn_demo", "description": "Drain pan access panel stiff — fictional punch.",
                    "priority": "C - Deferred / minor", "status": "Open",
                    "raised": _add_days(today, -1), "owner": "Day shift (demo)", "closed": "",
                }],
                "signoffName": "", "signoffDate": "",
                "notes": "Started from a pump template and edited items — showing that records are just tables.",
            },
        ],
        "paramPresets": {
            "Pump": [
                {"key": "flow", "label": "Flow", "unit": "m³/h"},
                {"key": "discharge_p", "label": "Discharge pressure", "unit": "bar"},
                {"key": "vibration", "label": "Vibration", "unit": "mm/s"},
                {"key": "bearing_t", "label": "Bearing temperature", "unit": "°C"},
            ],
            "Motor": [
                {"key": "current", "label": "Current", "unit": "A"},
                {"key": "winding_t", "label": "Winding temperature", "unit": "°C"},
                {"key": "vibration", "label": "Vibration", "unit": "mm/s"},
            ],
            "HVAC": [
                {"key": "supply_t", "label": "Supply air temp", "unit": "°C"},
                {"key": "filter_dp", "label": "Filter DP", "unit": "Pa"},
                {"key": "rh", "label": "Relative humidity", "unit": "% RH"},
            ],
        },
    }
    service.sync_catalog(db, state, actor=actor, legacy_signoffs=True, user_id=user_id)

    from logentra_server.models import Area, Equipment, Site
    site = db.get(Site, "site_northbrook")
    if site is None:
        site = Site(id="site_northbrook", name="Northbrook Works", code="NBK", created_at=now)
        db.add(site)
        db.flush()
    else:
        site.name = "Northbrook Works"
        site.code = site.code or "NBK"
    existing_areas = {a.name for a in (site.areas or [])} if site.areas is not None else set()
    for i, aname in enumerate(("Pump house", "Compressor hall", "Roof plant", "Yard"), start=1):
        if aname not in existing_areas:
            db.add(Area(id=f"area_nbk_{i}", site_id=site.id, name=aname, code=None, created_at=now))
    for eq in db.query(Equipment).all():
        eq.site_id = site.id
    db.flush()

    series = {
        "eq_p101": {"flow": 52, "discharge_p": 3.2, "vibration": 2.1, "bearing_t": 62},
        "eq_m204": {"current": 14.2, "winding_t": 68, "vibration": 1.8},
        "eq_ahu12": {"supply_t": 18.5, "filter_dp": 140, "rh": 48},
    }
    items = []
    for ei, (eid, params) in enumerate(series.items()):
        for day in range(21):
            date = _add_days(start, day)
            entry_id = f"log_{eid}_{day}"
            for ki, (key, base) in enumerate(params.items()):
                v = base + (_seeded(day * 10 + ei * 3 + ki) - 0.5) * base * 0.06
                if eid == "eq_p101" and key == "vibration" and day in (8, 15):
                    v = 6.2
                if eid == "eq_ahu12" and key == "filter_dp" and day == 18:
                    v = 310
                rec = {
                    "id": f"rd_{eid}_{day}_{key}",
                    "entry_id": entry_id,
                    "equipment_id": eid,
                    "parameter": key,
                    "date": date,
                    "time": "08:30",
                    "value": round(v * 100) / 100,
                    "entered_by": "Amir Rahman",
                    "source": "manual",
                }
                if eid == "eq_p101" and key == "vibration" and day == 8:
                    rec["remarks"] = "See vibration annotation."
                    rec["annot_cause"] = "Coupling slightly loose after a weekend restart (fictional demo)."
                    rec["annot_action"] = "Tightened coupling bolts and rechecked vibration on the next shift; reading returned to band."
                    rec["annot_by"] = "A. Rahman"
                    rec["annot_at"] = date + "T09:20"
                items.append(rec)
    # do not fire outbound webhooks for historical demo spikes
    result = service.ingest_readings(db, items, source="manual", actor=actor, fire_webhooks=False)

    # Work orders across the Kanban columns
    from logentra_server import workorders as wo
    from datetime import datetime as _dt
    today_d = _dt.strptime(today, "%Y-%m-%d")
    def due(delta: int, hhmm: str | None = None) -> str:
        d = (today_d + timedelta(days=delta)).strftime("%Y-%m-%d")
        return f"{d}T{hhmm}:00" if hhmm else d
    jobs = [
        {"title": "Investigate P-101 vibration 6.2 mm/s", "priority": "high", "status": "open",
         "equipment_id": "eq_p101", "due_at": due(1), "description": "Unacknowledged high vibration on the second spike."},
        {"title": "Top up TK-03 and record level after transfer", "priority": "medium", "status": "open",
         "equipment_id": "eq_tk03", "due_at": due(4)},
        {"title": "Seasonal start-up checks — commissioning package", "priority": "high", "status": "in_progress",
         "equipment_id": "eq_p101", "due_at": today},
        {"title": "AHU-12 filter inspection and DP reset", "priority": "urgent", "status": "in_progress",
         "equipment_id": "eq_ahu12", "due_at": due(-1)},
        {"title": "M-204 coupling alignment after weekend restart", "priority": "medium", "status": "in_progress",
         "equipment_id": "eq_m204", "due_at": due(3)},
        {"title": "P-101 motor rewind quote — hold pending budget", "priority": "high", "status": "on_hold",
         "equipment_id": "eq_p101", "due_at": None},
        {"title": "AHU-12 damper actuator — parts on order", "priority": "low", "status": "on_hold",
         "equipment_id": "eq_ahu12", "due_at": due(12)},
        {"title": "AHU-12 belt replacement close-out, photos filed", "priority": "medium", "status": "done",
         "equipment_id": "eq_ahu12", "due_at": due(-1)},
        {"title": "TK-03 level transmitter calibration accepted", "priority": "low", "status": "done",
         "equipment_id": "eq_tk03", "due_at": due(-2)},
        {"title": "P-101 oil filter change, 4,000 h service", "priority": "medium", "status": "closed",
         "equipment_id": "eq_p101", "due_at": due(-3)},
    ]
    for job in jobs:
        wo.create_work_order(db, job, actor=actor, fire_webhooks=False, source="demo")

    # A commissioning test sheet already in review (supervisor pending)
    from logentra_server import documents as docs
    from logentra_server import seed as _seed
    routes = _seed.ensure_default_routes(db)
    comm = next((r for r in routes if r.document_type == "commissioning_test_sheet"), routes[0])
    pdf = _simple_pdf("Northbrook Works — P-101 commissioning test sheet (Rev B, fictional)", [
        "Equipment: P-101   Location: Pump house   Date: " + today,
        "Technician: A. Rahman (demo)    Witness: Supervisor",
        "",
        "Checkpoint                         Unit    Expected      Recorded    Result",
        "Flow                               m3/h    40 – 60       52.3        Pass",
        "Discharge pressure                 bar     2.5 – 4.0     3.2         Pass",
        "Bearing temperature                C       <= 80         62          Pass",
        "Vibration, running                 mm/s    <= 4.5        6.2         Fail *",
        "Coupling / guard in place                    yes         yes         Pass",
        "",
        "* Vibration 6.2 mm/s on the first spike (fictional demo). Annotation recorded;",
        "  coupling tightened and a retest is booked on a work order.",
    ])
    doc = docs.create_document(
        db,
        title="P-101 Commissioning Test Sheet — Rev B",
        filename="test-sheet-P-101-revB.pdf",
        data=pdf,
        mime="application/pdf",
        document_type="commissioning_test_sheet",
        equipment_id="eq_p101",
        record_id="rec_demo_1",
        actor="Amir Rahman",
    )
    docs.submit_document(db, doc, route_id=comm.id, actor="Amir Rahman")

    # A second document still in draft so the submit panel can be demonstrated
    photo_pdf = _simple_pdf("AHU-12 filter inspection photos (index, fictional)", [
        "Eight photographs of the AHU-12 final filters, taken during the inspection record.",
        "Linked to the open work order for filter replacement.",
    ])
    docs.create_document(
        db,
        title="AHU-12 filter inspection photos",
        filename="ahu-12-filter-photos.pdf",
        data=photo_pdf,
        mime="application/pdf",
        document_type="inspection_photos",
        equipment_id="eq_ahu12",
        actor="Amir Rahman",
    )

    service.set_setting(db, "demo", "1")
    db.commit()
    return {"ok": True, "readings": result["added"], "at": now}
