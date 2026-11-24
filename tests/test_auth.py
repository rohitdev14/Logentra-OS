from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from logentra_server.app import create_app
from logentra_server.config import Settings


@pytest.fixture
def users_client(tmp_path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/users.db",
        open_browser=False,
        auth_mode="users",
        bootstrap_admin_username="admin",
        bootstrap_admin_password="adminpass1",
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as c:
        yield c


def _login(client: TestClient, username="admin", password="adminpass1") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_users_mode_requires_login(users_client: TestClient):
    assert users_client.get("/api/health").json()["auth_mode"] == "users"
    r = users_client.get("/api/bootstrap")
    assert r.status_code == 401
    r = users_client.get("/api/equipment")
    assert r.status_code == 401


def test_login_success_and_bad_password(users_client: TestClient):
    bad = users_client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert bad.status_code == 401
    body = _login(users_client)
    assert body["token"]
    assert body["user"]["username"] == "admin"
    assert "users.manage" in body["user"]["permissions"]
    me = users_client.get("/api/auth/me", headers=_auth(body["token"]))
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_permission_denials(users_client: TestClient):
    admin = _login(users_client)
    # create a viewer and a technician
    r = users_client.post(
        "/api/users",
        headers=_auth(admin["token"]),
        json={"username": "view", "password": "viewpass1", "role": "viewer"},
    )
    assert r.status_code == 201, r.text
    r = users_client.post(
        "/api/users",
        headers=_auth(admin["token"]),
        json={"username": "tech", "password": "techpass1", "role": "technician"},
    )
    assert r.status_code == 201, r.text

    view = _login(users_client, "view", "viewpass1")
    vh = _auth(view["token"])
    assert users_client.get("/api/equipment", headers=vh).status_code == 200
    assert users_client.post("/api/equipment", headers=vh, json={"values": {"fld_tag": "X"}}).status_code == 403
    assert users_client.post("/api/demo", headers=vh).status_code == 403
    assert users_client.get("/api/users", headers=vh).status_code == 403
    assert users_client.post("/api/work-orders", headers=vh, json={"title": "Nope"}).status_code == 403

    tech = _login(users_client, "tech", "techpass1")
    th = _auth(tech["token"])
    wo = users_client.post("/api/work-orders", headers=th, json={"title": "Inspect P-101"})
    assert wo.status_code == 201, wo.text
    wid = wo.json()["id"]
    # technician cannot assign or close
    assert users_client.post(f"/api/work-orders/{wid}/assign", headers=th, json={"assignee_id": tech["user"]["id"]}).status_code == 403
    closed = users_client.post(f"/api/work-orders/{wid}/transition", headers=th, json={"status": "closed"})
    assert closed.status_code == 403
    # but can move open -> in_progress
    moved = users_client.post(f"/api/work-orders/{wid}/transition", headers=th, json={"status": "in_progress"})
    assert moved.status_code == 200, moved.text
    assert users_client.get("/api/users", headers=th).status_code == 403


def test_api_key_bound_to_user(users_client: TestClient):
    admin = _login(users_client)
    created = users_client.post(
        f"/api/users/{admin['user']['id']}/api-keys",
        headers=_auth(admin["token"]),
        json={"name": "ci"},
    )
    assert created.status_code == 201, created.text
    raw = created.json()["key"]
    assert raw.startswith("lbk_")
    r = users_client.get("/api/auth/me", headers={"X-API-Key": raw})
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    # revoke
    kid = created.json()["id"]
    users_client.delete(f"/api/users/{admin['user']['id']}/api-keys/{kid}", headers=_auth(admin["token"]))
    assert users_client.get("/api/auth/me", headers={"X-API-Key": raw}).status_code == 401


def test_custom_role(users_client: TestClient):
    admin = _login(users_client)
    role = users_client.post(
        "/api/roles",
        headers=_auth(admin["token"]),
        json={"name": "planner", "permissions": ["equipment.read", "workorders.read", "workorders.create"]},
    )
    assert role.status_code == 201, role.text
    user = users_client.post(
        "/api/users",
        headers=_auth(admin["token"]),
        json={"username": "plan", "password": "planpass1", "role": "planner"},
    )
    assert user.status_code == 201
    plan = _login(users_client, "plan", "planpass1")
    h = _auth(plan["token"])
    assert users_client.post("/api/work-orders", headers=h, json={"title": "Plan job"}).status_code == 201
    assert users_client.post("/api/equipment", headers=h, json={"values": {"fld_tag": "Z"}}).status_code == 403


def test_audit_records_user(users_client: TestClient):
    admin = _login(users_client)
    users_client.post("/api/equipment", headers=_auth(admin["token"]), json={"id": "eq_a", "values": {"fld_tag": "A-1"}})
    rows = users_client.get("/api/audit?limit=20", headers=_auth(admin["token"])).json()
    assert any(r.get("actor") == "admin" and r.get("user_id") == admin["user"]["id"] for r in rows)
