from __future__ import annotations


from fastapi.testclient import TestClient


def test_health(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["backend"] == "sqlite"
    assert "version" in body


def test_docs_available(client: TestClient):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    spec = client.get("/openapi.json").json()
    assert "paths" in spec
    assert "/api/readings" in spec["paths"]
    assert "/api/ingest/readings" in spec["paths"]


def test_equipment_crud_and_bootstrap(client: TestClient):
    empty = client.get("/api/bootstrap").json()
    assert empty["equipmentFields"]
    assert empty["equipment"] == []

    created = client.post("/api/equipment", json={
        "id": "eq_x1",
        "values": {"fld_tag": "X-1", "fld_category": "Pump"},
        "params": [{"key": "flow", "label": "Flow", "unit": "m3/h", "min": 10, "max": 20}],
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"] == "eq_x1"
    assert body["params"][0]["key"] == "flow"

    listed = client.get("/api/equipment").json()
    assert any(e["id"] == "eq_x1" for e in listed)

    patched = client.patch("/api/equipment/eq_x1", json={
        "values": {"fld_tag": "X-1", "fld_category": "Pump"},
        "params": [{"key": "flow", "label": "Flow", "unit": "m3/h", "min": 10, "max": 20},
                   {"key": "vibration", "label": "Vibration", "unit": "mm/s", "max": 4.5}],
    })
    assert patched.status_code == 200
    assert len(patched.json()["params"]) == 2

    deleted = client.delete("/api/equipment/eq_x1")
    assert deleted.status_code == 200
    assert client.get("/api/equipment/eq_x1").status_code == 404


def test_templates_and_records(client: TestClient):
    client.post("/api/equipment", json={"id": "eq_t", "values": {"fld_tag": "T-1"}})
    tpl = client.post("/api/templates", json={
        "id": "tpl_1", "name": "Inspection", "kind": "inspection",
        "columns": [{"id": "c1", "key": "result", "label": "Result", "type": "passfail"}],
        "rows": [{"id": "r1", "label": "Look over the unit", "hint": "Visual"}],
    })
    assert tpl.status_code == 201
    rec = client.post("/api/records", json={
        "id": "rec_1", "templateId": "tpl_1", "templateName": "Inspection",
        "equipmentId": "eq_t", "kind": "inspection",
        "columns": [{"id": "c1", "key": "result", "label": "Result", "type": "passfail"}],
        "items": [{"id": "i1", "label": "Look over the unit", "values": {"c1": "Pass"}}],
    })
    assert rec.status_code == 201
    got = rec.json()
    assert got["status"] == "draft"
    assert got["items"][0]["values"]["c1"] == "Pass"
    submitted = client.post("/api/records/rec_1/submit", json={"revision": got["revision"]})
    assert submitted.status_code == 200, submitted.text
    approved = client.post("/api/records/rec_1/approve", json={"revision": submitted.json()["revision"]},
                           headers={"X-Logentra-Actor": "A. Tester"})
    assert approved.status_code == 200, approved.text
    assert client.delete("/api/records/rec_1").status_code == 409
    assert client.get("/api/records").json()[0]["signoffName"] == "A. Tester"


def test_api_key_required(auth_client: TestClient):
    assert auth_client.get("/api/health").status_code == 200
    r = auth_client.get("/api/bootstrap")
    assert r.status_code == 401
    r = auth_client.get("/api/bootstrap", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401
    r = auth_client.get("/api/bootstrap", headers={"X-API-Key": "test-key-123"})
    assert r.status_code == 200
    r = auth_client.get("/api/bootstrap", headers={"Authorization": "Bearer test-key-123"})
    assert r.status_code == 200


def test_export_json_and_csv(client: TestClient):
    client.post("/api/demo")
    js = client.get("/api/export/json")
    assert js.status_code == 200
    state = js.json()
    assert state["equipment"]
    assert state["logs"]
    csvr = client.get("/api/export/readings.csv")
    assert csvr.status_code == 200
    text = csvr.content.decode("utf-8-sig")
    assert "parameter" in text.splitlines()[0]
    assert "P-101" in text
    eq = client.get("/api/export/equipment.csv")
    assert eq.status_code == 200
    assert "P-101" in eq.content.decode("utf-8-sig")


def test_audit_log(client: TestClient):
    client.post("/api/demo")
    rows = client.get("/api/audit?limit=20").json()
    actions = {r["action"] for r in rows}
    assert "ingest" in actions or "sync" in actions or "demo" in actions or "clear" in actions
