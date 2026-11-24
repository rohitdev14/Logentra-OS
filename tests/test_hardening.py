from __future__ import annotations

import os
import socket
import time

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from logentra_server import documents, events
from logentra_server.app import create_app
from logentra_server.config import Settings
from logentra_server.database import alembic_config, make_engine
from logentra_server.models import ApiKey, OutboundWebhook, WebhookDelivery
from scripts.recovery import backup, restore


def make_client(settings, peer="127.0.0.1"):
    return TestClient(create_app(settings), base_url="http://127.0.0.1", client=(peer, 50000))


def test_profile_defaults_and_standalone_network_guard(db_url):
    with pytest.raises(ValidationError):
        Settings(host="0.0.0.0")
    with pytest.raises(ValidationError):
        Settings(profile="production", auth_mode="open")
    with pytest.raises(ValidationError):
        Settings(cors_origins=["*"])
    with make_client(Settings(database_url=db_url), "192.0.2.20") as client:
        assert client.get("/api/health").status_code == 403
    with make_client(Settings(database_url=db_url)) as client:
        assert client.get("/api/health", headers={"Host": "example.test"}).status_code == 403
        assert client.get("/api/health", headers={"Origin": "https://example.test"}).status_code == 403
    with make_client(Settings(database_url=db_url, profile="production", host="0.0.0.0")) as client:
        assert client.get("/api/health").json()["auth"] == "users"
        assert client.get("/api/bootstrap").status_code == 401


def test_request_limits_and_login_rate_limit(db_url):
    settings = Settings(database_url=db_url, request_limit_bytes=1024, upload_limit_bytes=16, login_attempts_per_minute=2)
    with make_client(settings) as client:
        oversized = client.post("/api/ingest/readings", content=b"x" * 1025)
        assert oversized.status_code == 413
        streamed = client.post("/api/ingest/readings", content=iter([b"x" * 600, b"x" * 600]))
        assert streamed.status_code == 413
        upload = client.post("/api/documents", files={"file": ("sample.txt", b"x" * 17)})
        assert upload.status_code == 413
        for _ in range(2):
            assert client.post("/api/auth/login", json={"username": "nobody", "password": "invalid"}).status_code == 401
        limited = client.post("/api/auth/login", json={"username": "nobody", "password": "invalid"})
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "60"
        assert limited.headers["x-content-type-options"] == "nosniff"
        assert limited.headers["x-request-id"]


def test_scoped_access_and_expired_key(db_url):
    settings = Settings(database_url=db_url, auth_mode="users", bootstrap_admin_username="admin",
                        bootstrap_admin_password="integration-password")
    with make_client(settings) as client:
        def login(name):
            token = client.post("/api/auth/login", json={"username": name, "password": "integration-password"}).json()["token"]
            return {"Authorization": "Bearer " + token}

        admin = login("admin")
        for site, area in (("one", "zone-a"), ("two", "zone-b")):
            assert client.post("/api/sites", headers=admin, json={
                "id": site, "name": site, "areas": [{"id": area, "name": area}],
            }).status_code == 201
        for equipment, site, area in (("asset-a", "one", "zone-a"), ("asset-b", "two", "zone-b"), ("asset-c", "one", None)):
            response = client.post("/api/equipment", headers=admin, json={
                "id": equipment, "values": {"fld_tag": equipment}, "site_id": site, "area_id": area,
            })
            assert response.status_code == 201, response.text
        user = client.post("/api/users", headers=admin, json={
            "username": "scoped", "password": "integration-password", "role": "viewer",
            "site_id": "one", "area_id": "zone-a",
        }).json()
        scoped = login("scoped")
        assert client.get("/api/equipment/asset-a", headers=scoped).status_code == 200
        for asset in ("asset-b", "asset-c"):
            assert client.get("/api/equipment/" + asset, headers=scoped).status_code == 404
        state = client.get("/api/bootstrap", headers=scoped).json()
        assert [asset["id"] for asset in state["equipmentIndex"]] == ["asset-a"]
        assert client.get("/api/export/equipment.csv", headers=scoped).status_code == 403
        key = client.post(f"/api/users/{user['id']}/api-keys", headers=admin, json={}).json()
        assert key["expires_at"]
        with client.app.state.SessionLocal.begin() as db:
            db.get(ApiKey, key["id"]).expires_at = "2020-01-01T00:00:00Z"
        assert client.get("/api/auth/me", headers={"X-API-Key": key["key"]}).status_code == 401


def test_catalog_and_record_write_preconditions(client):
    catalog = client.get("/api/bootstrap").json()["meta"]["catalogRevision"]
    first = client.post("/api/records", json={"id": "record-a", "notes": "initial"}).json()
    assert first["revision"] == 1
    assert client.put("/api/sync", headers={"If-Match": catalog}, json={"templates": []}).status_code == 409
    response = client.post("/api/records", json={"id": "record-a", "notes": "updated", "revision": 1})
    assert response.status_code == 201
    assert response.json()["revision"] == 2
    assert client.post("/api/records", json={"id": "record-a", "notes": "stale", "revision": 1}).status_code == 409
    assert client.get("/api/records").json()[0]["notes"] == "updated"


def test_idempotent_ingestion(client):
    assert client.post("/api/equipment", json={"id": "asset", "values": {"fld_tag": "A"}}).status_code == 201
    reading = {"id": "sample-a", "tag": "A", "parameter": "pressure", "value": 3, "ts": "2026-03-01T10:00:00+08:00"}
    assert client.post("/api/readings", json=reading).json()["added"] == 1
    assert client.post("/api/readings", json=reading).json()["added"] == 0
    assert client.post("/api/readings", json={**reading, "value": 4}).status_code == 409
    assert client.get("/api/readings", params={"tag": "A"}).json()["readings"][0]["ts"] == "2026-03-01T02:00:00"


def test_destination_restrictions(db_url, monkeypatch):
    with make_client(Settings(database_url=db_url)) as client:
        for url in ("http://127.0.0.1/x", "http://[::1]/x", "file:///etc/hosts", "https://x:y@example.test/"):
            assert client.post("/api/webhooks", json={"url": url}).status_code == 422
        monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
        ])
        assert client.post("/api/webhooks", json={"url": "https://example.test/hook"}).status_code == 422


def test_delivery_rollback_retry_and_dead_letter(db_url, monkeypatch):
    settings = Settings(database_url=db_url, webhook_attempts=2)
    factory = create_app(settings).state.SessionLocal
    with factory.begin() as db:
        db.add(OutboundWebhook(id="hook", url="https://example.test/", events='["*"]', enabled=1,
                               created_at="2026-01-01", secret=""))
    with factory() as db:
        events.enqueue(db, "test", {"value": 1})
        db.rollback()
    assert not events.dispatch_one(factory, settings)
    with factory.begin() as db:
        event_id = events.enqueue(db, "test", {"value": 1})[0]["id"]
    attempts = []

    def unavailable(_url, _event, identifier, *_args):
        attempts.append(identifier)
        return 503

    monkeypatch.setattr(events, "send_webhook", unavailable)
    assert events.dispatch_one(factory, settings)
    assert not events.dispatch_one(factory, settings)
    with factory.begin() as db:
        row = db.get(WebhookDelivery, event_id)
        assert row.state == "pending"
        assert row.available_at > time.time()
        row.available_at = time.time() - 1
    assert events.dispatch_one(factory, settings)
    with factory() as db:
        row = db.get(WebhookDelivery, event_id)
        assert (row.state, row.attempts, row.status_code) == ("dead", 2, 503)
    assert attempts == [event_id, event_id]


def test_document_checksum(client):
    response = client.post("/api/documents", files={"file": ("test.txt", b"evidence")})
    doc_id = response.json()["id"]
    try:
        with client.app.state.SessionLocal() as db:
            version = documents.current_version(documents.get_document(db, doc_id))
            path = documents.absolute_path(version.storage_path)
        assert client.get(f"/api/documents/{doc_id}/file").content == b"evidence"
        path.write_bytes(b"tampered")
        assert client.get(f"/api/documents/{doc_id}/file").status_code == 409
        assert client.post(f"/api/documents/{doc_id}/submit").status_code == 400
    finally:
        if "path" in locals():
            path.unlink()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_checkpoint_upgrade_and_recovery(tmp_path, backend):
    if backend == "sqlite":
        source = f"sqlite:///{tmp_path}/legacy.db"
        destination = f"sqlite:///{tmp_path}/restored.db"
    else:
        source = os.getenv("LOGENTRA_TEST_POSTGRES")
        destination = os.getenv("LOGENTRA_TEST_POSTGRES_RESTORE")
        if not (source and destination):
            pytest.skip("Set dedicated empty PostgreSQL source and restore database URLs")
    engine = make_engine(source)
    config = alembic_config(source)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0003_v05")
        conn.execute(text("INSERT INTO equipment (id, tag, created_at, updated_at) VALUES ('old_asset', 'OLD-1', '2025', '2025')"))
    with make_client(Settings(database_url=source)) as client:
        assert client.get("/api/equipment/old_asset").json()["id"] == "old_asset"
        assert client.post("/api/records", json={"id": "new_record", "notes": "preserve"}).status_code == 201
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    (attachments / "evidence.txt").write_text("document evidence", encoding="utf-8")
    bundle = tmp_path / "bundle"
    backup(source, attachments, bundle)
    restored_attachments = tmp_path / "restored-attachments"
    restore(bundle, destination, restored_attachments)
    assert (restored_attachments / "evidence.txt").read_text(encoding="utf-8") == "document evidence"
    with make_client(Settings(database_url=destination)) as client:
        assert client.get("/api/equipment/old_asset").json()["id"] == "old_asset"
        assert client.get("/api/records").json()[0]["notes"] == "preserve"
    (bundle / "attachments" / "evidence.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        restore(bundle, destination, tmp_path / "another-restore")


def test_concurrent_record_and_catalog_writers(client):
    from concurrent.futures import ThreadPoolExecutor

    initial = client.post("/api/records", json={"id": "race", "notes": "initial"}).json()
    catalog = client.get("/api/bootstrap").json()["meta"]["catalogRevision"]

    def write(note):
        return client.put("/api/sync", headers={"If-Match": catalog}, json={
            "records": [{"id": "race", "revision": initial["revision"], "notes": note}],
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ("first", "second")))
    assert sorted(results) == [200, 409]
    assert client.get("/api/records").json()[0]["revision"] == 2


def test_http_delivery_survives_unavailable_endpoint(db_url):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            received.append(self.headers["X-Logentra-Event-ID"])
            self.send_response(503 if len(received) == 1 else 204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Receiver) as receiver:
        thread = Thread(target=receiver.serve_forever, daemon=True)
        thread.start()
        config = Settings(database_url=db_url, webhook_private_destinations=True, webhook_backoff_seconds=1)
        try:
            with make_client(config) as client:
                target = f"http://127.0.0.1:{receiver.server_port}/integration"
                assert client.post("/api/webhooks", json={"url": target, "events": ["workorder.created"]}).status_code == 201
                assert client.post("/api/work-orders", json={"title": "Inspect motor"}).status_code == 201
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    rows = client.get("/api/webhooks/deliveries").json()
                    if rows and rows[0]["state"] == "delivered":
                        break
                    time.sleep(0.05)
                assert rows[0]["state"] == "delivered"
                assert rows[0]["attempts"] == 2
                assert received == [rows[0]["id"]] * 2
        finally:
            receiver.shutdown()
            thread.join()


def test_mqtt_disconnected_broker_and_lifecycle(db_url):
    from threading import Event
    from unittest.mock import Mock, patch

    mqtt = Mock()
    config = Settings(database_url=db_url, mqtt={"enabled": True, "tls": True})
    with patch("paho.mqtt.client.Client", return_value=mqtt):
        with make_client(config) as client:
            assert client.get("/api/integrations/health").json()["mqtt_connected"] is False
            mqtt.on_connect(mqtt, None, {}, Mock(is_failure=True), None)
            assert client.get("/api/integrations/health").json()["mqtt_connected"] is False
            mqtt.on_connect(mqtt, None, {}, Mock(is_failure=False), None)
            assert isinstance(client.app.state.mqtt_connected, Event)
            assert client.get("/api/integrations/health").json()["mqtt_connected"] is True
            mqtt.on_disconnect(mqtt, None, None, Mock(), None)
            assert client.get("/api/integrations/health").json()["mqtt_connected"] is False
    mqtt.tls_set.assert_called_once()
    mqtt.connect_async.assert_called_once()
    mqtt.reconnect_delay_set.assert_called_once()
    mqtt.disconnect.assert_called_once()
    mqtt.loop_stop.assert_called_once()


def test_live_mqtt_reconnect(db_url, tmp_path):
    from contextlib import contextmanager
    import json
    import subprocess

    import paho.mqtt.publish as publish

    executable = os.getenv("LOGENTRA_TEST_MQTT_BROKER")
    if not executable:
        pytest.skip("Set LOGENTRA_TEST_MQTT_BROKER to a Mosquitto executable")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    config = tmp_path / "mosquitto.conf"
    config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\n", encoding="utf-8")

    @contextmanager
    def broker():
        with subprocess.Popen([executable, "-c", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
            try:
                yield
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)

    def eventually(check):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(0.05)
        pytest.fail("MQTT integration did not reach the expected state")

    settings = Settings(database_url=db_url, mqtt={"enabled": True, "port": port})
    with make_client(settings) as client:
        assert client.post("/api/equipment", json={"values": {"fld_tag": "MQTT-ASSET"}}).status_code == 201
        def connected():
            return client.get("/api/integrations/health").json()["mqtt_connected"]

        assert not connected()
        for index in (1, 2):
            with broker():
                eventually(connected)
                publish.single("logentra/readings", json.dumps({
                    "id": f"mqtt-sample-{index}", "tag": "MQTT-ASSET", "parameter": "pressure", "value": index,
                }), hostname="127.0.0.1", port=port, qos=1)
                eventually(lambda: client.get("/api/readings", params={"tag": "MQTT-ASSET"}).json()["count"] == index)
            eventually(lambda: not connected())
