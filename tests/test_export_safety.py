import csv
from datetime import datetime, timezone
from io import StringIO
from uuid import uuid4

import pytest

from logentra_server.export_safety import safe_cell
from logentra_server.models import FieldDefinition
from test_qr import csrf, equipment
from test_shiftlog import post, rostered_shift

FORMULA = '=HYPERLINK("http://x","y")'


@pytest.mark.parametrize("trigger", ["=", "+", "-", "@", "\t", "\r"])
def test_safe_cell_prefixes_formula_triggers(trigger):
    assert safe_cell(f"{trigger}text") == f"'{trigger}text"


@pytest.mark.parametrize("value", [-4.2, -4, None, datetime(2026, 1, 1, tzinfo=timezone.utc), "normal text"])
def test_safe_cell_preserves_other_values(value):
    assert safe_cell(value) is value


@pytest.mark.parametrize("value", ["-4.2", "+5", "1e3", "  -.5e+2  "])
def test_safe_cell_preserves_plain_numeric_strings(value):
    assert safe_cell(value) == value


@pytest.mark.parametrize("value", ["-1+1", "=1", "=1+1", "+A1", "@SUM(A1)", "-2+3", "-1e999"])
def test_safe_cell_still_quotes_formulas_and_nonfinite_strings(value):
    assert safe_cell(value) == "'" + value


def export_rows(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    return list(csv.reader(StringIO(response.content.decode("utf-8-sig"), newline="")))


def test_equipment_export_escapes_user_fields_and_values(client):
    added = client.post("/api/equipment", json={"values": {"fld_tag": "P-101", "fld_category": FORMULA}})
    assert added.status_code == 201, added.text
    with client.app.state.SessionLocal.begin() as db:
        db.get(FieldDefinition, "fld_category").label = FORMULA

    header, row = export_rows(client, "/api/export/equipment.csv")
    assert header == ["Tag", "'" + FORMULA]
    assert row == ["P-101", "'" + FORMULA]


def test_readings_export_escapes_reading_and_qr_notes(client):
    identifier, code = equipment(client)
    reading = client.post("/api/readings", json={
        "equipment_id": identifier, "parameter": "value_0", "value": -4.2, "remarks": FORMULA,
    })
    assert reading.status_code == 201, reading.text
    note = client.post(f"/api/qr/{code}/entry", headers=csrf(client, code), data={
        "entry_id": str(uuid4()), "note": FORMULA, "entered_by": "Operator",
    })
    assert note.status_code == 200, note.text

    columns, *rows = export_rows(client, "/api/export/readings.csv")
    records = {row["parameter"]: row for row in (dict(zip(columns, row, strict=True)) for row in rows)}
    assert records["value_0"]["remarks"] == records["Note"]["remarks"] == "'" + FORMULA
    assert records["value_0"]["value"] == "-4.2"


def test_handover_export_escapes_summary(client):
    shift = rostered_shift(client)
    handover = client.get(f"/api/shifts/{shift['id']}/handover").json()
    post(client, f"/handovers/{handover['id']}/sign-off", {"summary": FORMULA}, 200)

    header, row = export_rows(client, "/api/handovers/export.csv")
    assert row[header.index("Summary")] == "'" + FORMULA
