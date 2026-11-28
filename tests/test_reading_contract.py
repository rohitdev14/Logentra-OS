import csv
import os
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url

from logentra_server.app import create_app
from logentra_server.config import Settings
from logentra_server.models import Reading
from logentra_server.mqtt_worker import parse_message
from logentra_server.service import ingest_readings

READING = {"tag": "P-101", "parameter": "flow", "value": 42.5}


def add_asset(client, tag="P-101", unit="m³/h"):
    response = client.post("/api/equipment", json={
        "values": {"fld_tag": tag},
        "params": [{"key": "flow", "label": "Flow", "unit": unit}],
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_keyed_retry_keeps_original_reading_and_timestamp(client):
    add_asset(client)
    payload = {**READING, "parameter": "Flow", "idempotency_key": "reading-1",
               "quality": "substituted", "annot_cause": "Example"}
    first = client.post("/api/readings", json=payload)
    assert first.status_code == 201, first.text
    saved = first.json()["reading"]
    assert saved["unit"] == "m³/h"
    assert saved["quality"] == "substituted"
    assert saved["idempotency_key"] == "reading-1"
    assert first.json()["duplicate"] is False

    # Annotation updates and configured unit changes do not change the original payload.
    changed = client.put(f"/api/readings/{saved['id']}/annotation", json={"cause": "Updated note"})
    assert changed.status_code == 200, changed.text
    response = client.patch(f"/api/equipment/{saved['equipment_id']}", json={
        "params": [{"key": "flow", "label": "Updated flow", "unit": "L/s"}],
    })
    assert response.status_code == 200, response.text
    replay = client.post("/api/readings", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["duplicate"] is True
    assert replay.json()["added"] == 0
    reading = replay.json()["reading"]
    assert (reading["id"], reading["unit"], reading["ts"]) == (saved["id"], "m³/h", saved["ts"])

    conflict = client.post("/api/readings", json={**payload, "value": 44})
    assert conflict.status_code == 409, conflict.text
    assert client.get("/api/readings").json()["count"] == 1


def test_key_scope_and_batch_order(client):
    add_asset(client)
    add_asset(client, tag="P-102")
    payload = {**READING, "idempotency_key": "reading-1"}
    response = client.post("/api/readings/bulk", json={
        "readings": [payload, payload, {**payload, "tag": "P-102"}],
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["added"] == 2
    assert [row["duplicate"] for row in result["results"]] == [False, True, False]
    assert result["skipped_items"] == [{"reason": "duplicate", "id": result["ids"][0], "duplicate": True}]
    assert result["results"][0]["reading"]["id"] == result["results"][1]["reading"]["id"]
    assert result["results"][2]["reading"]["id"] != result["ids"][0]

    changed = {**payload, "idempotency_key": "different-batch"}
    bad = client.post("/api/readings/bulk", json={"readings": [changed, {**changed, "value": 99}]})
    assert bad.status_code == 409, bad.text
    assert client.get("/api/readings").json()["count"] == 2


@pytest.mark.parametrize("extra", [
    {"unit": "L/s"}, {"quality": "unknown"}, {"source": "webhook"}, {"idempotency_key": "ø" * 513},
])
def test_invalid_contract_values_rejected(client, extra):
    add_asset(client)
    for endpoint in ("/api/readings", "/api/ingest/readings"):
        response = client.post(endpoint, json={**READING, **extra})
        assert response.status_code == 422, response.text
    assert client.get("/api/readings").json()["count"] == 0


def test_optional_unit_and_legacy_writes(client):
    add_asset(client, unit="")
    first = client.post("/api/readings", json={**READING, "unit": "L/s"})
    second = client.post("/api/readings", json={**READING, "unit": "L/s"})
    assert first.status_code == second.status_code == 201
    assert "results" not in first.json()
    rows = client.get("/api/readings").json()["readings"]
    assert len(rows) == 2
    assert all((row["unit"], row["quality"], row["source"], row["idempotency_key"]) ==
               ("L/s", "good", "api", None) for row in rows)


def test_grouped_reading_metadata_and_csv_column_order(client):
    add_asset(client)
    response = client.post("/api/readings", json={
        **READING, "quality": "uncertain", "idempotency_key": "reading-1", "source": "modbus",
    })
    assert response.status_code == 201, response.text
    log, = client.get("/api/logs").json()["items"]
    assert log["readings"] == {"flow": 42.5}
    assert log["readingMeta"] == {"flow": {
        "unit": "m³/h", "quality": "uncertain", "source": "modbus", "idempotency_key": "reading-1",
    }}

    csv_response = client.get("/api/export/readings.csv")
    assert csv_response.status_code == 200, csv_response.text
    header, data = csv.reader(StringIO(csv_response.content.decode("utf-8-sig"), newline=""))
    assert header[:-4] == ["tag", "equipment_id", "date", "time", "ts", "parameter", "value", "min", "max",
                           "out_of_range", "unacknowledged", "remarks", "entered_by", "source",
                           "annot_cause", "annot_action", "annot_by", "annot_at", "reading_id", "entry_id"]
    assert header[-4:] == ["unit", "quality", "source", "idempotency_key"]
    assert data[-4:] == ["m³/h", "uncertain", "modbus", "reading-1"]
    assert data[header.index("source")] == "modbus"


def test_mqtt_packet_passes_contract_to_ingest(client):
    add_asset(client, unit="L/s")
    items = parse_message(
        "logentra/readings/P-101/flow",
        b'{"value":42.5,"quality":"bad","unit":"L/s","idempotency_key":"mqtt-42"}',
        "logentra/readings/{tag}/{parameter}",
    )
    with client.app.state.SessionLocal() as db:
        first = ingest_readings(db, items, source="mqtt")
        second = ingest_readings(db, items, source="mqtt")
    assert first["results"][0]["reading"]["source"] == "mqtt"
    assert first["results"][0]["reading"]["quality"] == "bad"
    assert second["results"][0]["duplicate"] is True


def test_postgres_concurrent_keyed_requests_insert_once():
    source = os.environ.get("LOGENTRA_TEST_POSTGRES")
    if not source:
        pytest.skip("Set LOGENTRA_TEST_POSTGRES for concurrent key verification")
    name = f"reading_concurrency_{uuid4().hex}"
    admin = create_engine(source, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as conn:
        conn.exec_driver_sql(f"CREATE DATABASE {name}")
    url = make_url(source).set(database=name).render_as_string(hide_password=False)
    app = None
    try:
        app = create_app(Settings(database_url=url))
        client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
        add_asset(client)
        barrier = Barrier(2)

        def post():
            barrier.wait(timeout=10)
            return client.post("/api/readings", json={**READING, "idempotency_key": "concurrent"})

        with ThreadPoolExecutor(2) as pool:
            answers = list(pool.map(lambda _: post(), range(2)))
        assert sorted(response.status_code for response in answers) == [200, 201]
        assert len({response.json()["reading"]["id"] for response in answers}) == 1
        with app.state.SessionLocal() as db:
            assert db.query(Reading).filter_by(idempotency_key="concurrent").count() == 1
    finally:
        if app is not None:
            app.state.engine.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql(f"DROP DATABASE {name} WITH (FORCE)")
        admin.dispose()
