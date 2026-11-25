"""Creating something that already exists returns 409, not a server error."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from logentra_server.app import create_app
from logentra_server.config import Settings


@pytest.fixture
def admin(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/conflicts.db",
        open_browser=False,
        auth_mode="users",
        bootstrap_admin_username="admin",
        bootstrap_admin_password="adminpass1",
    )
    with TestClient(create_app(settings), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as c:
        token = c.post("/api/auth/login", json={"username": "admin", "password": "adminpass1"}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


def test_duplicate_site_id_and_code(admin: TestClient):
    assert admin.post("/api/sites", json={"id": "south", "name": "South", "code": "S1"}).status_code == 201
    again = admin.post("/api/sites", json={"id": "south", "name": "South"})
    assert again.status_code == 409
    assert "already exists" in again.json()["detail"]
    same_code = admin.post("/api/sites", json={"name": "Other", "code": "S1"})
    assert same_code.status_code == 409
    # The database is still usable after the rejected writes.
    assert admin.post("/api/sites", json={"id": "north", "name": "North"}).status_code == 201
    assert {s["id"] for s in admin.get("/api/sites").json()} == {"south", "north"}


def test_duplicate_area_ids(admin: TestClient):
    created = admin.post("/api/sites", json={"id": "s", "name": "S", "areas": [{"id": "a1", "name": "A1"}]})
    assert created.status_code == 201
    assert admin.post("/api/sites/s/areas", json={"id": "a1", "name": "Again"}).status_code == 409
    nested = admin.post("/api/sites", json={"name": "T", "areas": [{"id": "a1", "name": "x"}]})
    assert nested.status_code == 409
    twice = admin.post("/api/sites", json={"name": "U", "areas": [{"id": "b", "name": "x"}, {"id": "b", "name": "y"}]})
    assert twice.status_code == 409
    assert [s["id"] for s in admin.get("/api/sites").json()] == ["s"]


def test_duplicate_role_user_and_work_order_ids(admin: TestClient):
    assert admin.post("/api/roles", json={"id": "r1", "name": "Inspector"}).status_code == 201
    assert admin.post("/api/roles", json={"id": "r1", "name": "Other"}).status_code == 409
    assert admin.post("/api/users", json={"id": "u1", "username": "amy", "password": "amypass12"}).status_code == 201
    assert admin.post("/api/users", json={"id": "u1", "username": "bob", "password": "bobpass12"}).status_code == 409
    assert admin.post("/api/work-orders", json={"id": "wo1", "title": "Check pump"}).status_code == 201
    assert admin.post("/api/work-orders", json={"id": "wo1", "title": "Again"}).status_code == 409
    assert admin.post("/api/work-order-rules", json={"id": "rule1"}).status_code == 201
    assert admin.post("/api/work-order-rules", json={"id": "rule1"}).status_code == 409


def test_integrity_errors_become_conflicts(admin: TestClient, monkeypatch):
    # Simulate a race that slips past the pre-check: the database constraint still answers 409.
    monkeypatch.setattr("logentra_server.routers.auth_users._require_new", lambda *a, **k: None)
    assert admin.post("/api/sites", json={"id": "race", "name": "Race"}).status_code == 201
    raced = admin.post("/api/sites", json={"id": "race", "name": "Race"})
    assert raced.status_code == 409
    assert raced.json()["detail"] == "This item already exists or conflicts with another record."
