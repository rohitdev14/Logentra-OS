"""LOGENTRA_* env vars take precedence; deprecated LOGBOOK_* names still work."""
from __future__ import annotations

from logentra_server.config import env_value, load_settings


def test_logentra_overrides_logbook(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGBOOK_PORT", "9001")
    monkeypatch.setenv("LOGENTRA_PORT", "9002")
    monkeypatch.setenv("LOGENTRA_CONFIG", str(tmp_path / "missing.yaml"))
    settings = load_settings(tmp_path / "missing.yaml")
    assert settings.port == 9002


def test_logbook_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("LOGENTRA_AUTH_MODE", raising=False)
    monkeypatch.setenv("LOGBOOK_AUTH_MODE", "users")
    settings = load_settings(tmp_path / "missing.yaml")
    assert settings.auth_mode == "users"


def test_env_value_prefers_first(monkeypatch):
    monkeypatch.setenv("LOGENTRA_API_KEY", "new")
    monkeypatch.setenv("LOGBOOK_API_KEY", "old")
    assert env_value("LOGENTRA_API_KEY", "LOGBOOK_API_KEY") == "new"
    monkeypatch.delenv("LOGENTRA_API_KEY")
    assert env_value("LOGENTRA_API_KEY", "LOGBOOK_API_KEY") == "old"
