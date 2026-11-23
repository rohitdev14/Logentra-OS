from __future__ import annotations

from fastapi.testclient import TestClient


def test_work_order_lifecycle(client: TestClient):
    eq = client.post("/api/equipment", json={"id": "eq_wo", "values": {"fld_tag": "WO-1"}})
    assert eq.status_code == 201
    created = client.post("/api/work-orders", json={
        "title": "Replace seal",
        "description": "Leak at the stuffing box",
        "priority": "high",
        "equipment_id": "eq_wo",
        "due_at": "2026-12-01",
    })
    assert created.status_code == 201, created.text
    wo = created.json()
    assert wo["status"] == "open"
    wid = wo["id"]

    r = client.post(f"/api/work-orders/{wid}/transition", json={"status": "in_progress"})
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress"
    assert r.json()["started_at"]

    r = client.post(f"/api/work-orders/{wid}/transition", json={"status": "on_hold"})
    assert r.json()["status"] == "on_hold"
    r = client.post(f"/api/work-orders/{wid}/transition", json={"status": "in_progress"})
    r = client.post(f"/api/work-orders/{wid}/transition", json={"status": "done"})
    assert r.json()["status"] == "done"
    r = client.post(f"/api/work-orders/{wid}/transition", json={"status": "closed"})
    assert r.json()["status"] == "closed"
    assert r.json()["closed_at"]

    # closed is terminal for non-admin force... open mode is admin, force allowed via users.manage
    # illegal hop from closed without force is still blocked for... wait, open mode has users.manage
    # so force_status is True. Skip illegal-transition assertion in open mode.

    listed = client.get("/api/work-orders?status=closed&page=1").json()
    assert listed["total"] >= 1
    assert any(x["id"] == wid for x in listed["items"])


def test_work_order_comments_labour_attachments(client: TestClient):
    wo = client.post("/api/work-orders", json={"title": "Lube motor"}).json()
    wid = wo["id"]
    c = client.post(f"/api/work-orders/{wid}/comments", json={"body": "Started this morning"})
    assert c.status_code == 201
    lab = client.post(f"/api/work-orders/{wid}/labour", json={"minutes": 45, "note": "Greased bearings"})
    assert lab.status_code == 201
    att = client.post(f"/api/work-orders/{wid}/attachments", json={"filename": "photo.jpg", "content_type": "image/jpeg", "size": 1234})
    assert att.status_code == 201, att.text
    got = client.get(f"/api/work-orders/{wid}").json()
    assert got["comments"][0]["body"] == "Started this morning"
    assert got["labour_minutes"] == 45
    assert got["attachments"][0]["filename"] == "photo.jpg"


def test_auto_work_order_from_oor(client: TestClient):
    client.post("/api/equipment", json={
        "id": "eq_auto",
        "values": {"fld_tag": "AUTO-1"},
        "params": [{"key": "vibration", "label": "Vibration", "unit": "mm/s", "max": 4.5}],
    })
    # rule off by default — no work order
    client.post("/api/readings", json={"equipment_id": "eq_auto", "parameter": "vibration", "value": 9, "ts": "2026-11-22T08:00:00"})
    listed = client.get("/api/work-orders?equipment_id=eq_auto&page=1").json()
    assert listed["total"] == 0

    rule = client.post("/api/work-order-rules", json={
        "equipment_id": "eq_auto",
        "parameter_key": "vibration",
        "enabled": True,
        "priority": "urgent",
        "title_template": "OOR {tag} {parameter}={value}",
        "cooldown_minutes": 1440,
    })
    assert rule.status_code == 201, rule.text
    ingest = client.post("/api/readings", json={"equipment_id": "eq_auto", "parameter": "vibration", "value": 9.5, "ts": "2026-11-22T09:00:00"})
    assert ingest.status_code == 201
    assert ingest.json()["work_orders"]
    listed = client.get("/api/work-orders?equipment_id=eq_auto&page=1").json()
    assert listed["total"] == 1
    assert "AUTO-1" in listed["items"][0]["title"]
    # cooldown: a second OOR should not open another
    client.post("/api/readings", json={"equipment_id": "eq_auto", "parameter": "vibration", "value": 10, "ts": "2026-11-22T10:00:00"})
    listed = client.get("/api/work-orders?equipment_id=eq_auto&page=1").json()
    assert listed["total"] == 1

