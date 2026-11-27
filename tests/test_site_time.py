import csv
import io
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from logentra_server.app import create_app
from logentra_server.models import AuditLog, Equipment, Reading, Site
from logentra_server.shift_models import ShiftPattern
from logentra_server.sitetime import local_day_bounds, local_to_utc_text
from logentra_server.utils import loads
from test_handover import seeded_evidence
from test_qr import csrf, equipment
from test_shiftlog import post, rostered_shift


SINGAPORE = ZoneInfo("Asia/Singapore")


@pytest.mark.parametrize("host_zone", [None, "Europe/London"])
def test_local_logbook_reading_reaches_handover(client, host_zone):
    shift = rostered_shift(client)
    seeded_evidence(client)
    with client.app.state.SessionLocal.begin() as db:
        db.get(Site, "site_north").timezone = SINGAPORE.key
        db.query(ShiftPattern).one().timezone = SINGAPORE.key
    local = (datetime.now(SINGAPORE) - timedelta(minutes=30)).replace(second=0, microsecond=0)
    original_zone = os.environ.get("TZ")
    try:
        if host_zone:
            os.environ["TZ"] = host_zone
            time.tzset()
        saved = post(client, "/readings/bulk", {"readings": [{
            "equipment_id": "eq_pump", "parameter": "vibration", "value": 9, "source": "manual",
            "date": local.date().isoformat(), "time": local.strftime("%H:%M"),
        }]}, 200)
        reading_id, = saved["ids"]
        items = client.get(f"/api/shifts/{shift['id']}/handover").json()["items"]
        item = next(item for item in items if item["kind"] == "oor_reading" and item["ref_id"] == reading_id)
        assert local.strftime("%H:%M") in item["detail"]
        utc = local.astimezone(timezone.utc)
        assert item["event_at"] == utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        with client.app.state.SessionLocal() as db:
            assert db.get(Reading, reading_id).ts == utc.strftime("%Y-%m-%dT%H:%M:%S")
    finally:
        if original_zone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_zone
        time.tzset()


@pytest.fixture
def site_equipment(client):
    eid, code = equipment(client)
    site = post(client, "/sites", {"name": "Northbrook Works", "timezone": SINGAPORE.key})
    with client.app.state.SessionLocal.begin() as db:
        db.get(Equipment, eid).site_id = site["id"]
    return eid, code, site["id"]


def test_qr_and_desktop_use_the_same_site_clock(client, site_equipment, monkeypatch):
    from logentra_server import qr_entries

    eid, code, _ = site_equipment
    stamp = "2026-11-26T02:30:30Z"
    monkeypatch.setattr(qr_entries, "utcnow_iso", lambda: stamp)
    qr_id = str(uuid4())
    response = client.post(f"/api/qr/{code}/entry", headers=csrf(client, code), data={
        "entry_id": qr_id, "values": '{"value_0": 3}', "entered_by": "Amir Rahman",
    })
    assert response.status_code == 200, response.text
    post(client, "/readings", {"equipment_id": eid, "parameter": "value_0", "value": 4,
                              "entry_id": "desktop", "source": "manual", "date": "2026-11-26", "time": "10:30"})
    logs = client.get("/api/logs", params={"equipment_id": eid}).json()["items"]
    assert [row["id"] for row in logs] == [qr_id, "desktop"]
    assert [(row["date"], row["time"]) for row in logs] == [("2026-11-26", "10:30")] * 2
    assert [row["ts"] for row in logs] == [stamp, "2026-11-26T02:30:00Z"]
    assert client.get(f"/api/qr/{code}").json()["last_logged_at"] == stamp
    assert f'datetime="{stamp}">2026-11-26 10:30<' in client.get(f"/q/{code}").text


def test_csv_round_trip_keeps_utc_seconds(client, site_equipment, settings, tmp_path):
    eid, _, _ = site_equipment
    post(client, "/readings", {"equipment_id": eid, "parameter": "value_0", "value": 0,
                              "ts": "2026-11-25T16:30:39Z"})
    exported = client.get("/api/export/readings.csv")
    row, = csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig")))
    assert (row["date"], row["time"], row["ts"]) == ("2026-11-26", "00:30", "2026-11-25T16:30:39Z")
    fresh = create_app(settings.model_copy(update={"database_url": f"sqlite:///{tmp_path / 'fresh.db'}"}))
    with TestClient(fresh, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as dest:
        response = dest.post("/api/ingest/file", files={"file": ("readings.csv", exported.content, "text/csv")})
        assert response.json()["added"] == 1, response.text
        with fresh.state.SessionLocal() as db:
            assert db.query(Reading).one().ts == row["ts"].removesuffix("Z")


def test_date_filters_use_local_day_and_timestamp_filters_use_utc(client, site_equipment):
    eid, _, _ = site_equipment
    data = {"equipment_id": eid, "parameter": "value_0", "value": 7}
    row = post(client, "/readings", {**data, "date": "2026-11-26", "time": "00:30"})
    assert row["added"] == 1
    for path, list_key in (("readings", "readings"), ("events/out-of-range", "events"), ("logs", "items")):
        def rows(**filters):
            result = client.get(f"/api/{path}", params={"tag": "P-101", **filters})
            assert result.status_code == 200, result.text
            return result.json()[list_key]

        assert len(rows(**{"from": "2026-11-26"})) == 1
        assert not rows(to="2026-11-25")
        assert len(rows(to="2026-11-26T00:30:00+08:00")) == 1
        assert not rows(to="2026-11-26T00:29:59+08:00")
        assert not rows(**{"from": "2026-11-26T00:30:01+08:00"})


def test_missing_clock_and_naive_explicit_timestamp(client, site_equipment):
    eid, _, _ = site_equipment
    reading = {"equipment_id": eid, "parameter": "value_0", "value": 4}
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    now_id, = post(client, "/readings", reading)["ids"]
    other_id, = post(client, "/readings", {**reading, "timestamp": "2026-11-26T10:30:00"})["ids"]
    midnight_id, = post(client, "/readings", {**reading, "date": "2026-11-26"})["ids"]
    time_only_id, = post(client, "/readings", {**reading, "time": "10:30"})["ids"]
    after = datetime.now(timezone.utc)
    with client.app.state.SessionLocal() as db:
        saved = datetime.fromisoformat(db.get(Reading, now_id).ts).replace(tzinfo=timezone.utc)
        assert before <= saved <= after
        assert db.get(Reading, other_id).ts == "2026-11-26T10:30:00"
        assert db.get(Reading, midnight_id).ts == "2026-11-25T16:00:00"
        assert db.get(Reading, time_only_id).ts in {
            local_to_utc_text(instant.astimezone(SINGAPORE).date().isoformat(), "10:30", SINGAPORE)
            for instant in (before, after)
        }


def test_dst_uses_first_fold_and_moves_gap_forward():
    london = ZoneInfo("Europe/London")
    assert local_to_utc_text("2026-10-25", "01:30", london) == "2026-10-25T00:30:00"
    assert local_to_utc_text("2026-03-29", "01:30", london) == "2026-03-29T01:30:00"
    assert local_day_bounds("2026-10-25", london) == ("2026-10-24T23:00:00", "2026-10-26T00:00:00")


def test_site_timezone_change_is_audited_and_keeps_reading_instants(client, site_equipment):
    eid, _, site = site_equipment
    rid, = post(client, "/readings", {"equipment_id": eid, "parameter": "value_0", "value": 4,
                                     "date": "2026-11-26", "time": "10:30"})["ids"]
    assert client.patch(f"/api/sites/{site}", json={"timezone": "Invalid/Zone"}).status_code == 422
    changed = client.patch(f"/api/sites/{site}", json={"timezone": "UTC", "name": "Northbrook Works West"})
    assert changed.status_code == 200, changed.text
    assert client.get("/api/bootstrap").json()["meta"]["siteTimezones"][site] == "UTC"
    log, = client.get("/api/logs", params={"equipment_id": eid}).json()["items"]
    assert log["time"] == "02:30"
    with client.app.state.SessionLocal() as db:
        assert db.get(Reading, rid).ts == "2026-11-26T02:30:00"
        audit = db.query(AuditLog).filter_by(action="site.timezone_changed", entity_id=site).one()
        assert loads(audit.detail) == {"old": "Asia/Singapore", "new": "UTC"}


def test_automatic_work_order_shows_site_time_and_webhooks_keep_utc(client, site_equipment):
    from logentra_server.models import OutboundWebhook, WebhookDelivery
    from logentra_server.utils import dumps, utcnow_iso

    eid, _, _ = site_equipment
    client.app.state.delivery_worker.stop()
    with client.app.state.SessionLocal.begin() as db:
        db.add(OutboundWebhook(id="work_order_clock", url="https://example.invalid/hook",
                               events=dumps(["out_of_range", "workorder.created"]), enabled=1,
                               created_at=utcnow_iso()))
    post(client, "/work-order-rules", {"equipment_id": eid, "parameter_key": "value_0", "enabled": True})
    result = post(client, "/readings", {"equipment_id": eid, "parameter": "value_0", "value": 8,
                                       "date": "2026-11-26", "time": "10:30"})
    order_id, = result["work_orders"]
    order = client.get(f"/api/work-orders/{order_id}").json()
    assert "at 2026-11-26 10:30 (Asia/Singapore)" in order["description"]
    with client.app.state.SessionLocal() as db:
        payloads = {delivery.event: loads(delivery.payload) for delivery in db.query(WebhookDelivery)}
        assert set(payloads) == {"out_of_range", "workorder.created"}
        assert all(payload["ts"] == "2026-11-26T02:30:00Z" for payload in payloads.values())
    alert, = client.get("/api/dashboard").json()["recent_alerts"]
    assert alert["ts"] == "2026-11-26T02:30:00Z"


def test_retry_of_reading_without_clock_preserves_the_first_instant(client, site_equipment, monkeypatch):
    from logentra_server import utils

    eid, _, _ = site_equipment
    reading = {"id": "reading-retry", "equipment_id": eid, "parameter": "value_0", "value": 4}
    monkeypatch.setattr(utils, "utcnow_iso", lambda: "2026-11-26T02:30:04Z")
    assert post(client, "/readings", reading)["added"] == 1
    monkeypatch.setattr(utils, "utcnow_iso", lambda: "2026-11-26T02:30:42Z")
    assert post(client, "/readings", reading)["added"] == 0
    with client.app.state.SessionLocal() as db:
        assert db.get(Reading, reading["id"]).ts == "2026-11-26T02:30:04"


@pytest.mark.parametrize(("timestamp", "expected_ts"), [
    (None, "2026-11-26T08:30:00Z"),
    ("2026-11-26T16:30:00", "2026-11-26T08:30:00Z"),
    ("2026-11-26T10:30:41+02:00", "2026-11-26T08:30:41Z"),
])
def test_legacy_json_reading_import_uses_explicit_offset_or_site_clock(client, site_equipment, timestamp, expected_ts):
    eid, _, _ = site_equipment
    log = {"equipmentId": eid, "date": "2026-11-26", "time": "16:30", "readings": {"value_0": 4}}
    if timestamp is not None:
        log["ts"] = timestamp
    imported = post(client, "/migrate/v2?replace=false", {"logs": [log]}, 200)
    assert imported["readings"] == 1
    row, = client.get("/api/logs", params={"equipment_id": eid}).json()["items"]
    assert (row["date"], row["time"], row["ts"]) == ("2026-11-26", "16:30", expected_ts)


def test_json_export_reimport_keeps_instant_and_seconds(client, site_equipment):
    eid, _, _ = site_equipment
    stamp = "2026-11-26T08:30:41Z"
    post(client, "/readings", {"equipment_id": eid, "parameter": "value_0", "value": 4, "ts": stamp})
    backup = client.get("/api/export/json").json()
    assert backup["logs"][0]["ts"] == stamp
    imported = post(client, "/migrate/v2", backup, 200)
    assert imported["readings"] == 1
    with client.app.state.SessionLocal() as db:
        assert db.query(Reading).one().ts == stamp.removesuffix("Z")
    row, = client.get("/api/logs", params={"equipment_id": eid}).json()["items"]
    assert (row["date"], row["time"], row["ts"]) == ("2026-11-26", "16:30", stamp)
