from __future__ import annotations

from unittest.mock import patch
from time import monotonic, sleep

from fastapi.testclient import TestClient


def test_demo_has_unacknowledged_oor(client: TestClient):
    r = client.post("/api/demo")
    assert r.status_code == 200, r.text
    ev = client.get("/api/events/out-of-range", params={"unacknowledged": True}).json()
    assert ev["count"] >= 1
    # P-101 vibration day 15 is unacknowledged in the demo
    tags = {e["tag"] for e in ev["events"]}
    assert "P-101" in tags
    params = {e["parameter"] for e in ev["events"] if e["tag"] == "P-101"}
    assert "vibration" in params


def test_annotation_acknowledges(client: TestClient):
    client.post("/api/demo")
    ev = client.get("/api/events/out-of-range", params={"unacknowledged": True, "tag": "P-101"}).json()
    vibration = [e for e in ev["events"] if e["parameter"] == "vibration"]
    assert vibration
    target = vibration[0]
    put = client.put(
        f"/api/logs/{target['entry_id']}/annotations/{target['parameter']}",
        json={"cause": "Test cause", "action": "Noted in unit test", "by": "T.T."},
    )
    assert put.status_code == 200, put.text
    ev2 = client.get("/api/events/out-of-range", params={"unacknowledged": True, "tag": "P-101"}).json()
    still = [e for e in ev2["events"] if e["entry_id"] == target["entry_id"] and e["parameter"] == "vibration"]
    assert still == []
    all_oor = client.get("/api/events/out-of-range", params={"tag": "P-101"}).json()
    found = [e for e in all_oor["events"] if e["entry_id"] == target["entry_id"] and e["parameter"] == "vibration"]
    assert found and found[0]["unacknowledged"] is False
    assert found[0]["annotation"]["cause"] == "Test cause"


def test_outbound_webhook_on_oor(client: TestClient):
    client.post("/api/equipment", json={
        "id": "eq_p101",
        "values": {"fld_tag": "P-101"},
        "params": [{"key": "vibration", "label": "Vibration", "unit": "mm/s", "max": 4.5}],
    })
    wh = client.post("/api/webhooks", json={"url": "http://127.0.0.1:9/hook", "events": ["out_of_range"]})
    assert wh.status_code == 201, wh.text

    captured = []

    def fake_post(url, event, event_id, body, secret, settings):
        captured.append({"url": url, "body": body})
        return 200

    with patch("logentra_server.events.send_webhook", side_effect=fake_post):
        r = client.post("/api/readings", json={
            "tag": "P-101", "parameter": "vibration", "value": 9.1,
            "ts": "2026-11-22T12:00:00", "source": "api",
        })
        deadline = monotonic() + 5
        while monotonic() < deadline:
            deliveries = client.get("/api/webhooks/deliveries").json()
            if deliveries and deliveries[0]["ok"]:
                break
            sleep(0.05)
    assert r.status_code == 201, r.text
    assert r.json()["out_of_range"] == 1
    assert captured, "webhook should have been called"
    import json
    payload = json.loads(captured[0]["body"].decode("utf-8"))
    assert payload["event"] == "out_of_range"
    assert payload["tag"] == "P-101"
    assert payload["value"] == 9.1
    deliveries = client.get("/api/webhooks/deliveries").json()
    assert deliveries and deliveries[0]["ok"] is True

    # in-range reading should not fire
    captured.clear()
    with patch("logentra_server.events.send_webhook", side_effect=fake_post):
        r = client.post("/api/readings", json={
            "tag": "P-101", "parameter": "vibration", "value": 2.0, "ts": "2026-11-22T13:00:00",
        })
    assert r.json()["out_of_range"] == 0
    assert captured == []
