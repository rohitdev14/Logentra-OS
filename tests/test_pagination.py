from __future__ import annotations

from fastapi.testclient import TestClient


def test_equipment_pagination_and_search(client: TestClient):
    for i in range(25):
        tag = f"EQ-{i:03d}"
        r = client.post("/api/equipment", json={"values": {"fld_tag": tag, "fld_category": "Pump" if i % 2 == 0 else "Motor"}})
        assert r.status_code == 201, r.text
    # legacy unpaginated list still returns an array
    raw = client.get("/api/equipment").json()
    assert isinstance(raw, list)
    assert len(raw) == 25

    page = client.get("/api/equipment?page=1&page_size=10").json()
    assert page["total"] == 25
    assert page["page"] == 1
    assert page["page_size"] == 10
    assert page["pages"] == 3
    assert len(page["items"]) == 10
    page2 = client.get("/api/equipment?page=3&page_size=10").json()
    assert len(page2["items"]) == 5

    found = client.get("/api/equipment?q=EQ-007&page=1&page_size=10").json()
    assert found["total"] == 1
    assert found["items"][0]["tag"] == "EQ-007"
    # Substring search remains available for operators who do not type a full tag.
    partial = client.get("/api/equipment?q=007&page=1&page_size=10").json()
    assert partial["total"] >= 1
    assert any(item["tag"] == "EQ-007" for item in partial["items"])


def test_work_order_pagination(client: TestClient):
    for i in range(12):
        client.post("/api/work-orders", json={"title": f"Job {i}", "priority": "low" if i < 6 else "high"})
    page = client.get("/api/work-orders?page=1&page_size=5").json()
    assert page["total"] == 12
    assert len(page["items"]) == 5
    high = client.get("/api/work-orders?priority=high&page=1&page_size=20").json()
    assert high["total"] == 6


def test_audit_pagination(client: TestClient):
    client.post("/api/equipment", json={"values": {"fld_tag": "AUD-1"}})
    page = client.get("/api/audit?page=1&page_size=5").json()
    assert "items" in page
    assert page["total"] >= 1
