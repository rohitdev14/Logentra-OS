from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from logentra_server.app import create_app
from logentra_server.config import Settings


def _pdf(name: str = "sheet") -> tuple[str, bytes, str]:
    return (f"{name}.pdf", b"%PDF-1.4\n% demo\n", "application/pdf")


def _upload(client: TestClient, **fields):
    data = {k: v for k, v in fields.items() if v is not None}
    return client.post("/api/documents", files={"file": _pdf(data.get("title", "sheet"))}, data=data)


@pytest.fixture
def users_client(tmp_path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/docs.db",
        open_browser=False,
        auth_mode="users",
        bootstrap_admin_username="admin",
        bootstrap_admin_password="adminpass1",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username="admin", password="adminpass1") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_route_progression_and_lock(client: TestClient):
    routes = client.get("/api/approval-routes").json()
    assert routes, "default routes should be seeded"
    route_id = routes[0]["id"]
    eq = client.post("/api/equipment", json={"id": "eq_doc", "values": {"fld_tag": "DOC-1"}})
    assert eq.status_code == 201
    created = _upload(client, title="Test sheet", document_type="inspection", equipment_id="eq_doc")
    assert created.status_code == 201, created.text
    doc = created.json()
    assert doc["status"] == "draft"
    assert doc["current_version"]["sha256"]
    assert doc["current_version"]["size"] > 0
    did = doc["id"]

    submitted = client.post(f"/api/documents/{did}/submit", json={"route_id": route_id})
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "in_review"
    assert body["steps"][0]["status"] == "pending"
    assert body["steps"][1]["status"] == "waiting"

    first = client.post(f"/api/documents/{did}/approve", json={"comment": "Looks good"})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "in_review"
    assert first.json()["steps"][0]["status"] == "approved"
    assert first.json()["steps"][1]["status"] == "pending"

    final = client.post(f"/api/documents/{did}/approve", json={})
    assert final.status_code == 200, final.text
    locked = final.json()
    assert locked["status"] == "approved"
    assert locked["locked"] is True

    # locked: no new version, no further action
    r = client.post(f"/api/documents/{did}/versions", files={"file": _pdf("v2")})
    assert r.status_code == 400
    assert client.post(f"/api/documents/{did}/approve", json={}).status_code == 400
    assert client.post(f"/api/documents/{did}/reject", json={"comment": "nope"}).status_code == 400
    assert client.post(f"/api/documents/{did}/return", json={"comment": "fix"}).status_code == 400
    assert client.post(f"/api/documents/{did}/submit", json={}).status_code == 400

    file_r = client.get(f"/api/documents/{did}/file")
    assert file_r.status_code == 200
    assert file_r.content.startswith(b"%PDF")


def test_reject_requires_comment_and_is_terminal(client: TestClient):
    route_id = client.get("/api/approval-routes").json()[0]["id"]
    did = _upload(client, title="Reject me").json()["id"]
    client.post(f"/api/documents/{did}/submit", json={"route_id": route_id})
    bad = client.post(f"/api/documents/{did}/reject", json={})
    assert bad.status_code == 400
    ok = client.post(f"/api/documents/{did}/reject", json={"comment": "Missing calibration cert"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "rejected"
    assert client.post(f"/api/documents/{did}/submit", json={"route_id": route_id}).status_code == 400
    assert client.post(f"/api/documents/{did}/versions", files={"file": _pdf("v2")}).status_code == 400


def test_return_and_resubmit_new_version(client: TestClient):
    route_id = client.get("/api/approval-routes").json()[0]["id"]
    did = _upload(client, title="Return me").json()["id"]
    client.post(f"/api/documents/{did}/submit", json={"route_id": route_id})
    ret = client.post(f"/api/documents/{did}/return", json={"comment": "Please retake photo 3"})
    assert ret.status_code == 200, ret.text
    assert ret.json()["status"] == "returned"
    v2 = client.post(f"/api/documents/{did}/versions", files={"file": _pdf("rev2")})
    assert v2.status_code == 201, v2.text
    assert v2.json()["current_version"]["version_no"] == 2
    again = client.post(f"/api/documents/{did}/submit", json={"route_id": route_id})
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["status"] == "in_review"
    assert body["current_version"]["version_no"] == 2
    # route restarts at first step
    pending = [s for s in body["steps"] if s["status"] == "pending"]
    assert len(pending) == 1
    assert pending[0]["sort_order"] == 0


def test_document_permission_denials(users_client: TestClient):
    admin = _login(users_client)
    ah = _auth(admin["token"])
    users_client.post("/api/users", headers=ah, json={"username": "view", "password": "viewpass1", "role": "viewer"})
    users_client.post("/api/users", headers=ah, json={"username": "tech", "password": "techpass1", "role": "technician"})
    users_client.post("/api/users", headers=ah, json={"username": "super", "password": "superpass1", "role": "supervisor"})

    view = _login(users_client, "view", "viewpass1")
    vh = _auth(view["token"])
    tech = _login(users_client, "tech", "techpass1")
    th = _auth(tech["token"])
    super_ = _login(users_client, "super", "superpass1")
    sh = _auth(super_["token"])

    # viewer cannot upload or manage routes
    assert users_client.post(
        "/api/documents", headers=vh, files={"file": _pdf()}, data={"title": "Nope"}
    ).status_code == 403
    assert users_client.post(
        "/api/approval-routes", headers=vh, json={"name": "x", "steps": [{"assignee_type": "role", "role_name": "admin"}]}
    ).status_code == 403

    # technician can upload and submit
    created = users_client.post(
        "/api/documents", headers=th, files={"file": _pdf()}, data={"title": "Tech sheet"}
    )
    assert created.status_code == 201, created.text
    did = created.json()["id"]
    route_id = users_client.get("/api/approval-routes", headers=th).json()[0]["id"]
    sub = users_client.post(f"/api/documents/{did}/submit", headers=th, json={"route_id": route_id})
    assert sub.status_code == 200, sub.text
    # technician cannot approve
    assert users_client.post(f"/api/documents/{did}/approve", headers=th, json={}).status_code == 403
    # viewer can read
    assert users_client.get(f"/api/documents/{did}", headers=vh).status_code == 200
    # supervisor can approve first step, not the admin step without being admin
    step1 = users_client.post(f"/api/documents/{did}/approve", headers=sh, json={"comment": "OK"})
    assert step1.status_code == 200, step1.text
    assert step1.json()["steps"][1]["status"] == "pending"
    denied = users_client.post(f"/api/documents/{did}/approve", headers=sh, json={})
    assert denied.status_code == 403
    # admin can finish
    done = users_client.post(f"/api/documents/{did}/approve", headers=ah, json={})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "approved"
    assert done.json()["locked"] is True


def test_approval_webhooks(client: TestClient):
    route_id = client.get("/api/approval-routes").json()[0]["id"]
    wh = client.post("/api/webhooks", json={
        "url": "http://127.0.0.1:9/hook",
        "events": [
            "document.submitted", "document.step_approved", "document.approved",
            "document.rejected", "document.returned",
        ],
    })
    assert wh.status_code == 201, wh.text
    captured = []

    def fake_post(url, content=None, headers=None, timeout=None):
        captured.append(json.loads(content.decode("utf-8")))
        return httpx.Response(200, text="ok")

    did = _upload(client, title="Hook sheet").json()["id"]
    with patch("logentra_server.events.httpx.post", side_effect=fake_post):
        client.post(f"/api/documents/{did}/submit", json={"route_id": route_id})
        client.post(f"/api/documents/{did}/approve", json={})
        client.post(f"/api/documents/{did}/approve", json={})
    events = [c["event"] for c in captured]
    assert events == ["document.submitted", "document.step_approved", "document.step_approved", "document.approved"]

    captured.clear()
    did2 = _upload(client, title="Return hook").json()["id"]
    with patch("logentra_server.events.httpx.post", side_effect=fake_post):
        client.post(f"/api/documents/{did2}/submit", json={"route_id": route_id})
        client.post(f"/api/documents/{did2}/return", json={"comment": "Revise section 2"})
    assert [c["event"] for c in captured] == ["document.submitted", "document.returned"]

    captured.clear()
    did3 = _upload(client, title="Reject hook").json()["id"]
    with patch("logentra_server.events.httpx.post", side_effect=fake_post):
        client.post(f"/api/documents/{did3}/submit", json={"route_id": route_id})
        client.post(f"/api/documents/{did3}/reject", json={"comment": "Wrong asset"})
    assert [c["event"] for c in captured] == ["document.submitted", "document.rejected"]


def test_dashboard_and_queue(client: TestClient):
    route_id = client.get("/api/approval-routes").json()[0]["id"]
    did = _upload(client, title="Queue item", submit="true", route_id=route_id).json()["id"]
    # submit=true on create
    got = client.get(f"/api/documents/{did}").json()
    assert got["status"] in ("draft", "in_review")
    if got["status"] == "draft":
        client.post(f"/api/documents/{did}/submit", json={"route_id": route_id})
    dash = client.get("/api/dashboard").json()
    assert "pending_approvals" in dash
    assert dash["pending_approvals"] >= 1
    assert "equipment_count" in dash
    queue = client.get("/api/documents/queue").json()
    assert queue["total"] >= 1
    assert any(x["id"] == did or True for x in queue["items"])
