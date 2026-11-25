from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from logentra_server import events
from logentra_server.config import Settings
from logentra_server.models import OutboundWebhook, WebhookDelivery
from scripts.verify_evidence import inspect_evidence
from test_hardening import make_client


def test_scoped_engineering_and_approval(db_url):
    config = Settings(database_url=db_url, auth_mode="users", bootstrap_admin_username="admin",
                      bootstrap_admin_password="qualification-test-password")
    with make_client(config) as client:
        def login(username):
            response = client.post("/api/auth/login", json={"username": username, "password": "qualification-test-password"})
            assert response.status_code == 200
            return {"Authorization": "Bearer " + response.json()["token"]}

        admin = login("admin")
        scoped = {}
        for site in ("north", "south"):
            assert client.post("/api/sites", headers=admin, json={
                "id": site, "name": site, "areas": [{"id": site + "-area", "name": "Zone"}],
            }).status_code == 201
            assert client.post("/api/users", headers=admin, json={
                "username": site, "password": "qualification-test-password", "role": "supervisor",
                "site_id": site, "area_id": site + "-area",
            }).status_code == 201
            scoped[site] = login(site)
            assert client.post("/api/equipment", headers=scoped[site], json={
                "id": site + "-asset", "values": {"fld_tag": site + "-asset"}, "params": [{"key": "p"}],
            }).status_code == 201
        north, south = scoped["north"], scoped["south"]
        assert client.patch("/api/equipment/south-asset", headers=north, json={"values": {"fld_tag": "bad"}}).status_code == 404
        assert client.post("/api/records", headers=north, json={"equipmentId": "south-asset"}).status_code == 404
        assert client.post("/api/records", headers=north, json={"id": "record-north", "equipmentId": "north-asset"}).status_code == 201
        assert client.post("/api/work-orders", headers=north, json={"title": "North", "equipment_id": "south-asset"}).status_code == 404
        work = client.post("/api/work-orders", headers=north, json={"title": "North", "equipment_id": "north-asset"})
        assert work.status_code == 201, work.text
        assert client.patch("/api/work-orders/" + work.json()["id"], headers=south, json={"title": "Foreign"}).status_code == 404
        accepted = {"id": "north-reading", "equipment_id": "north-asset", "parameter": "p", "value": 1}
        rejected = {"id": "south-reading", "equipment_id": "south-asset", "parameter": "p", "value": 2}
        assert client.post("/api/ingest/readings", headers=north, json=[accepted, rejected]).status_code == 404
        assert client.get("/api/readings", headers=north).json()["count"] == 0
        assert client.post("/api/ingest/readings", headers=north, json=accepted).status_code == 200
        assert client.post("/api/readings", headers=south, json=rejected).status_code == 201
        csv = client.get("/api/export/readings.csv", headers=north)
        assert "north-reading" in csv.text and "south-reading" not in csv.text
        assert client.post("/api/ingest/file", headers=north, params={"create_missing_equipment": False},
                           files={"file": ("sample.csv", b"tag,parameter,value\nsouth-asset,p,3\n")}).status_code == 404
        assert client.get("/api/webhooks/deliveries", headers=north).status_code == 403
        document = client.post("/api/documents", headers=north, data={"equipment_id": "north-asset", "submit": "true"},
                               files={"file": ("sheet.txt", b"local evidence")})
        assert document.status_code == 201, document.text
        did = document.json()["id"]
        assert document.json()["area_id"] == "north-area"
        assert client.get(f"/api/documents/{did}", headers=south).status_code == 404
        assert client.post(f"/api/documents/{did}/approve", headers=south).status_code == 404
        assert client.post(f"/api/documents/{did}/approve", headers=north).status_code == 200
        assert client.post(f"/api/documents/{did}/approve", headers=admin).json()["status"] == "approved"

        historical_work = client.post("/api/work-orders", headers=admin, json={
            "title": "Legacy conflicting work", "site_id": "north", "area_id": "north-area",
            "equipment_id": "north-asset", "linked_reading_id": "south-reading",
        })
        assert historical_work.status_code == 201
        wid = historical_work.json()["id"]
        assert client.get(f"/api/work-orders/{wid}", headers=admin).status_code == 200
        assert client.get(f"/api/work-orders/{wid}", headers=north).status_code == 404
        assert client.patch(f"/api/work-orders/{wid}", headers=north, json={"title": "Forbidden"}).status_code == 404
        assert wid not in {row["id"] for row in client.get("/api/work-orders", headers=north).json()["items"]}

        historical_doc = client.post("/api/documents", headers=admin, data={
            "title": "Legacy conflicting document", "site_id": "north", "area_id": "north-area",
            "work_order_id": wid,
        }, files={"file": ("legacy.txt", b"foreign evidence")})
        assert historical_doc.status_code == 201, historical_doc.text
        foreign_id = historical_doc.json()["id"]
        assert client.get(f"/api/documents/{foreign_id}/file", headers=admin).status_code == 200
        for suffix in ("", "/file"):
            assert client.get(f"/api/documents/{foreign_id}{suffix}", headers=north).status_code == 404
        assert client.post(f"/api/documents/{foreign_id}/approve", headers=north).status_code == 404
        assert foreign_id not in {row["id"] for row in client.get("/api/documents", headers=north).json()["items"]}


def test_replay_preserves_event_and_requires_dead_state(client):
    factory = client.app.state.SessionLocal
    client.app.state.delivery_worker.stop()
    with factory.begin() as db:
        db.add(OutboundWebhook(id="replay-hook", url="http://127.0.0.1:9", events='["*"]',
                               enabled=1, created_at="2026-01-01", secret=""))
        db.flush()
        delivery_id = events.enqueue(db, "qualification", {"sample": 7})[0]["id"]
        db.flush()
        db.get(WebhookDelivery, delivery_id).state = "dead"
    url = f"/api/webhooks/deliveries/{delivery_id}/replay"
    def send(_index):
        return client.post(url, json={"reason": "Receiver is available"}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send, range(2))) == [202, 409]
    with factory() as db:
        delivery = db.get(WebhookDelivery, delivery_id)
        assert (delivery.state, delivery.replays, delivery.id, delivery.payload) == (
            "pending", 1, delivery_id, '{"sample": 7}',
        )
    assert any(row["action"] == "replay" for row in client.get("/api/audit").json())


def test_sqlite_audit_and_upload_reconciliation(client, monkeypatch, tmp_path):
    from logentra_server import documents
    from scripts import verify_evidence

    monkeypatch.setattr(documents, "ROOT", tmp_path)
    monkeypatch.setattr(documents, "ATTACH_DIR", tmp_path / "attachments")
    monkeypatch.setattr(verify_evidence, "ATTACH_DIR", tmp_path / "attachments")
    assert client.post("/api/equipment", json={"values": {"fld_tag": "AUDIT"}}).status_code == 201
    factory = client.app.state.SessionLocal
    with factory() as db:
        initial = inspect_evidence(db)["audit_sha256"]
    for statement in ("UPDATE audit_log SET detail = 'changed'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError), factory.begin() as db:
            db.execute(text(statement))
    failed = client.post("/api/documents", data={"submit": "true", "route_id": "unknown"},
                         files={"file": ("sheet.txt", b"orphan test")})
    assert failed.status_code == 400
    with factory() as db:
        assert inspect_evidence(db)["audit_sha256"] == initial
        documents.store_file("interrupted", 1, "sheet.txt", b"unreferenced")
        result = inspect_evidence(db)
        assert len(result["unreferenced_files"]) == 1
        assert result["document_issues"] == []


def test_tls_proxy_qualification(tmp_path, db_url):
    nginx = os.getenv("LOGENTRA_TEST_NGINX")
    if not nginx:
        pytest.skip("Set LOGENTRA_TEST_NGINX to an nginx executable")

    def port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    app_port, tls_port = port(), port()
    key, certificate = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                    "-keyout", str(key), "-out", str(certificate)], check=True, capture_output=True)
    env = {**os.environ, "LOGENTRA_PUBLIC_HOST": "localhost logbook.plant.local 192.168.1.50 [2001:db8::50]"}
    source = subprocess.run(["sh", "deployment/render-nginx.sh", "deployment/nginx.conf"],
                            env=env, check=True, text=True, capture_output=True).stdout
    source = source.replace("/run/logentra-nginx.pid", str(tmp_path / "nginx.pid"))
    source = source.replace("listen 443 ssl", f"listen 127.0.0.1:{tls_port} ssl")
    source = source.replace("app:8787", f"127.0.0.1:{app_port}")
    source = source.replace("/etc/logentra/tls/fullchain.pem", str(certificate))
    source = source.replace("/etc/logentra/tls/privkey.pem", str(key))
    config = tmp_path / "nginx.conf"
    config.write_text(source.replace("http {", "http {\n" + "\n".join(
        f"    {directive}_temp_path {tmp_path / directive};"
        for directive in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")
    )))
    subprocess.run([nginx, "-p", str(tmp_path), "-c", str(config), "-t"], check=True, capture_output=True)
    env = {**os.environ, "LOGENTRA_DATABASE_URL": db_url, "LOGENTRA_PROFILE": "production"}
    commands = [
        ([sys.executable, "run.py", "--no-browser", "--port", str(app_port)], env),
        ([nginx, "-p", str(tmp_path), "-c", str(config), "-g", "daemon off;"], os.environ),
    ]
    processes = []
    try:
        for args, environ in commands:
            processes.append(subprocess.Popen(args, env=environ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        with httpx.Client(verify=ssl.create_default_context(cafile=str(certificate)), trust_env=False,
                          base_url=f"https://localhost:{tls_port}") as client:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    if client.get("/api/ready").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.1)
            else:
                pytest.fail("TLS proxy failed to reach production readiness")
            for host in ("LOGBOOK.PLANT.LOCAL", "logbook.plant.local:58443", "192.168.1.50",
                         "192.168.1.50:58443", "[2001:DB8::50]", "[2001:db8::50]:58443"):
                assert client.get("/api/ready", headers={"Host": host}).status_code == 200, host
            with pytest.raises(httpx.RemoteProtocolError):
                client.get("/api/ready", headers={"Host": "unknown.invalid:58443"})
            response = client.get("/api/bootstrap")
            assert response.status_code == 401
            assert response.headers["strict-transport-security"] == "max-age=31536000"
            responses = [client.post("/api/auth/login", json={}) for _ in range(25)]
            limited = [r for r in responses if r.status_code == 429]
            assert limited, [r.status_code for r in responses]
            assert "Too many sign-in attempts" in limited[0].json()["detail"]
            assert 1 <= int(limited[0].headers["retry-after"]) <= 60
    finally:
        for process in reversed(processes):
            process.terminate()
            process.wait(timeout=10)


def test_sustained_webhook_delivery(db_url):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from logentra_server.app import create_app

    delivered = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            delivered.append(self.headers["X-Logentra-Event-ID"])
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    settings = Settings(database_url=db_url, webhook_private_destinations=True)
    factory = create_app(settings).state.SessionLocal
    with ThreadingHTTPServer(("127.0.0.1", 0), Receiver) as endpoint:
        thread = Thread(target=endpoint.serve_forever, daemon=True)
        thread.start()
        try:
            with factory.begin() as db:
                db.add(OutboundWebhook(id="load-hook", url=f"http://127.0.0.1:{endpoint.server_port}/load",
                                       events='["qualification"]', enabled=1, created_at="2026-01-01", secret=""))
                db.flush()
                expected = [events.enqueue(db, "qualification", {"sequence": n})[0]["id"] for n in range(100)]
            started = time.monotonic()
            while events.dispatch_one(factory, settings):
                pass
            duration = time.monotonic() - started
            assert delivered == expected
            with factory() as db:
                assert db.query(WebhookDelivery).filter_by(state="delivered", attempts=1).count() == 100
            print(f"Webhook sustained load: {len(delivered)} events in {duration:.3f} s")
        finally:
            endpoint.shutdown()
            thread.join()
            factory.kw["bind"].dispose()


def test_failed_file_publication_removes_staging(monkeypatch, tmp_path):
    from logentra_server import documents

    monkeypatch.setattr(documents, "ROOT", tmp_path)
    monkeypatch.setattr(documents, "ATTACH_DIR", tmp_path / "attachments")

    def fail_link(_source, _destination):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(documents.os, "link", fail_link)
    with pytest.raises(OSError, match="synthetic publication failure"):
        documents.store_file("failed", 1, "sample.txt", b"draft")
    assert not any(path.is_file() for path in (tmp_path / "attachments").rglob("*"))
