from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import uvicorn
from alembic import command
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine

import run
from logentra_server import app as server
from logentra_server.config import load_settings
from logentra_server.database import MigrationRequired, alembic_config, make_engine, run_migrations

ROOT = Path(__file__).resolve().parents[1]
HEAD = "0012_reading_contract"


def cli(*args, db_url, **overrides):
    env = {**os.environ, "LOGENTRA_CONFIG": str(ROOT / "tests" / "absent-config.yaml"),
           "LOGENTRA_DATABASE_URL": db_url, "LOGENTRA_AUTO_MIGRATE": "off", **overrides}
    return subprocess.run([sys.executable, "run.py", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)


def test_migrate_fresh_and_at_head(db_url):
    for should_upgrade in (True, False):
        result = cli("migrate", db_url=db_url)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"Current revision: {HEAD}"
        assert ("Running upgrade" in result.stderr) is should_upgrade
    result = cli("migrate", "--check", db_url=db_url)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"Current revision: {HEAD}", f"Head revision: {HEAD}"]


def test_migrate_check_fresh_and_pending(db_url, tmp_path):
    result = cli("migrate", "--check", db_url=db_url)
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == ["Current revision: uninitialized", f"Head revision: {HEAD}"]
    assert not (tmp_path / "test.db").exists()

    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            config = alembic_config(db_url)
            config.attributes["connection"] = connection
            command.upgrade(config, "0001_initial")
        result = cli("migrate", "--check", db_url=db_url)
        assert result.returncode == 1, result.stderr
        assert result.stdout.splitlines() == ["Current revision: 0001_initial", f"Head revision: {HEAD}"]
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0001_initial"
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
    finally:
        engine.dispose()


def test_create_admin_requires_head_when_auto_migrate_off(db_url):
    args = ("create-admin", "--username", "operator-admin", "--password", "test-only-password")
    result = cli(*args, db_url=db_url)
    assert result.returncode == 1
    assert "python run.py migrate" in result.stderr

    assert cli("migrate", db_url=db_url).returncode == 0
    result = cli(*args, db_url=db_url)
    assert result.returncode == 0, result.stderr
    assert "Created admin user 'operator-admin'" in result.stdout


@pytest.mark.parametrize("name,field", [("LOGENTRA_ROLE", "role"), ("LOGENTRA_AUTO_MIGRATE", "auto_migrate")])
def test_invalid_process_setting(monkeypatch, tmp_path, name, field):
    monkeypatch.setenv(name, "invalid")
    with pytest.raises(ValidationError, match=field):
        load_settings(tmp_path / "config.yaml")


@pytest.fixture
def jobs(monkeypatch):
    delivery = Mock()
    escalation = Mock()
    mqtt = Mock()
    monkeypatch.setattr(server.events, "DeliveryWorker", delivery)
    monkeypatch.setattr(server.handover_service, "EscalationWorker", escalation)
    monkeypatch.setattr(server.mqtt_worker, "start_mqtt", mqtt)
    mqtt_client = Mock()
    mqtt.return_value = mqtt_client, Mock()
    return delivery, escalation, mqtt, mqtt_client


@pytest.mark.parametrize("role,starts_jobs", [("web", False), ("all", True)])
def test_http_role_gates_background_jobs(settings, jobs, role, starts_jobs):
    settings.role = role
    settings.mqtt.enabled = True
    delivery, escalation, mqtt, mqtt_client = jobs
    app = server.create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/ready").status_code == 200
        if starts_jobs:
            delivery.return_value.start.assert_called_once_with()
            escalation.return_value.start.assert_called_once_with()
            mqtt.assert_called_once_with(settings, app.state.SessionLocal)
        else:
            for job in (delivery, escalation, mqtt):
                job.assert_not_called()
    if starts_jobs:
        delivery.return_value.stop.assert_called_once_with()
        escalation.return_value.stop.assert_called_once_with()
        mqtt_client.disconnect.assert_called_once_with()
        mqtt_client.loop_stop.assert_called_once_with()


def test_worker_role_starts_jobs_and_shuts_down_without_http(monkeypatch, jobs, db_url, tmp_path):
    monkeypatch.setenv("LOGENTRA_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("LOGENTRA_ROLE", "worker")
    monkeypatch.setenv("LOGENTRA_AUTO_MIGRATE", "on")
    monkeypatch.setenv("LOGENTRA_DATABASE_URL", db_url)
    monkeypatch.setenv("LOGENTRA_MQTT_ENABLED", "on")
    monkeypatch.setattr(sys, "argv", ["run.py"])
    http = Mock()
    browser = Mock()
    seed_roles = Mock()
    monkeypatch.setattr(uvicorn, "run", http)
    monkeypatch.setattr(run.webbrowser, "open", browser)
    monkeypatch.setattr(server.seed, "ensure_builtin_roles", seed_roles)
    monkeypatch.setattr(run, "time", SimpleNamespace(sleep=lambda _: signal.raise_signal(signal.SIGTERM)))

    run.main()

    delivery, escalation, mqtt, mqtt_client = jobs
    delivery.return_value.start.assert_called_once_with()
    escalation.return_value.start.assert_called_once_with()
    mqtt.assert_called_once()
    delivery.return_value.stop.assert_called_once_with()
    escalation.return_value.stop.assert_called_once_with()
    mqtt_client.disconnect.assert_called_once_with()
    mqtt_client.loop_stop.assert_called_once_with()
    http.assert_not_called()
    browser.assert_not_called()
    seed_roles.assert_not_called()


def test_create_app_requires_head_when_auto_migrate_off(settings):
    settings.auto_migrate = "off"
    with pytest.raises(MigrationRequired, match=r"python run\.py migrate"):
        server.create_app(settings)

    engine = make_engine(settings.sqlalchemy_url)
    try:
        run_migrations(engine)
    finally:
        engine.dispose()
    with TestClient(server.create_app(settings), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/ready").status_code == 200


@pytest.mark.parametrize("role,workers", [("worker", "1"), ("all", "2")])
def test_cli_startup_refuses_pending_migrations(db_url, role, workers):
    result = cli("--no-browser", db_url=db_url, LOGENTRA_ROLE=role, LOGENTRA_WORKERS=workers)
    assert result.returncode == 1
    assert "python run.py migrate" in result.stderr
