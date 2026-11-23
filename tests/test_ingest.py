from __future__ import annotations

from fastapi.testclient import TestClient

from logentra_server.mqtt_worker import parse_message


def _seed_pump(client: TestClient):
    client.post("/api/equipment", json={
        "id": "eq_p101",
        "values": {"fld_tag": "P-101", "fld_category": "Pump"},
        "params": [
            {"key": "flow", "label": "Flow", "unit": "m3/h", "min": 40, "max": 60},
            {"key": "vibration", "label": "Vibration", "unit": "mm/s", "max": 4.5},
        ],
    })


def test_bulk_and_query(client: TestClient):
    _seed_pump(client)
    body = {
        "readings": [
            {"tag": "P-101", "parameter": "flow", "date": "2026-11-22", "time": "08:00", "value": 52, "entry_id": "log_a"},
            {"tag": "P-101", "parameter": "vibration", "date": "2026-11-22", "time": "08:00", "value": 2.1, "entry_id": "log_a"},
            {"tag": "P-101", "parameter": "flow", "date": "2026-11-22", "time": "16:00", "value": 53, "entry_id": "log_b"},
        ],
        "create_missing_equipment": False,
    }
    r = client.post("/api/readings/bulk", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 3

    q = client.get("/api/readings", params={"tag": "P-101", "parameter": "flow", "from": "2026-11-22", "to": "2026-11-22"})
    assert q.status_code == 200
    data = q.json()
    assert data["count"] == 2
    assert [row["value"] for row in data["readings"]] == [52, 53]

    one = client.post("/api/readings", json={
        "tag": "P-101", "parameter": "flow", "ts": "2026-11-23T09:15:00", "value": 54.5, "source": "api",
    })
    assert one.status_code == 201


def test_webhook_ingest_and_csv(client: TestClient, tmp_path):
    _seed_pump(client)
    r = client.post("/api/ingest/readings", json={
        "tag": "P-101", "parameter": "flow", "value": 51, "ts": "2026-11-22T10:00:00", "entered_by": "gateway",
    })
    assert r.status_code == 200
    assert r.json()["added"] == 1

    csv_path = tmp_path / "logs.csv"
    csv_path.write_text(
        "tag,date,time,parameter,value,remarks\n"
        "P-101,2026-11-21,09:00,flow,50,csv ingest\n"
        "P-101,2026-11-21,09:00,vibration,1.8,\n",
        encoding="utf-8",
    )
    with csv_path.open("rb") as fh:
        up = client.post("/api/ingest/file", params={"kind": "logs"}, files={"file": ("logs.csv", fh, "text/csv")})
    assert up.status_code == 200, up.text
    assert up.json()["added"] == 2

    # xlsx
    from openpyxl import Workbook
    xlsx = tmp_path / "logs.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["tag", "date", "time", "parameter", "value"])
    ws.append(["P-101", "2026-11-20", "11:00", "flow", 49])
    wb.save(xlsx)
    with xlsx.open("rb") as fh:
        up = client.post(
            "/api/ingest/file",
            params={"kind": "logs"},
            files={"file": ("logs.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert up.status_code == 200, up.text
    assert up.json()["added"] == 1


def test_mqtt_parser():
    items = parse_message(
        "logbook/P-101/vibration",
        b'{"value": 3.2, "ts": "2026-11-22T08:00:00"}',
        "logbook/{tag}/{parameter}",
    )
    assert items[0]["tag"] == "P-101"
    assert items[0]["parameter"] == "vibration"
    assert items[0]["value"] == 3.2
    items = parse_message("logbook/P-101/flow", b"55.5", "logbook/{tag}/{parameter}")
    assert items[0]["value"] == 55.5


def test_migrate_v2(client: TestClient):
    backup = {
        "meta": {"version": 2, "demo": False},
        "equipmentFields": [
            {"id": "fld_tag", "key": "tag", "label": "Tag", "type": "text", "locked": True},
            {"id": "fld_category", "key": "category", "label": "Category", "type": "text"},
        ],
        "equipment": [{
            "id": "eq_old",
            "values": {"fld_tag": "OLD-9", "fld_category": "Pump"},
            "params": [{"key": "flow", "label": "Flow", "unit": "m3/h", "min": 1, "max": 9}],
            "createdAt": "2026-11-01",
        }],
        "templates": [],
        "records": [],
        "paramPresets": {},
        "logs": [{
            "id": "log_old_1",
            "equipmentId": "eq_old",
            "date": "2026-11-02",
            "time": "08:00",
            "readings": {"flow": 5},
            "remarks": "migrated",
            "enteredBy": "v2",
            "annotations": {},
        }],
    }
    r = client.post("/api/migrate/v2", json=backup)
    assert r.status_code == 200, r.text
    assert r.json()["equipment"] == 1
    assert r.json()["readings"] == 1
    state = client.get("/api/bootstrap").json()
    tags = []
    for e in state["equipment"]:
        tags.extend(e["values"].values())
    assert "OLD-9" in tags
    assert state["logs"][0]["readings"]["flow"] == 5
