from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"
EXAMPLE_CONFIG = ROOT / "config.example.yaml"


class MqttConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 1883
    username: str = ""
    password: str = ""
    tls: bool = False
    client_id: str = "logentra"
    topics: list[str] = Field(default_factory=lambda: ["logentra/readings", "logentra/+/+", "logbook/readings", "logbook/+/+"])
    topic_pattern: str = "logentra/{tag}/{parameter}"


class OutboundWebhookConfig(BaseModel):
    url: str
    events: list[str] = Field(default_factory=lambda: ["out_of_range"])
    secret: str = ""


class Settings(BaseModel):
    profile: Literal["standalone", "development", "production"] = "standalone"
    cors_origins: list[str] = Field(default_factory=list)
    request_limit_bytes: int = Field(30 * 1024 * 1024, ge=1024)
    upload_limit_bytes: int = Field(25 * 1024 * 1024, ge=1)
    login_attempts_per_minute: int = Field(10, ge=1, le=1000)
    api_key_days: int = Field(90, ge=1, le=365)
    webhook_attempts: int = Field(5, ge=1, le=20)
    webhook_backoff_seconds: int = Field(5, ge=1, le=300)
    webhook_private_destinations: bool = False
    host: str = "127.0.0.1"
    port: int = 8787
    open_browser: bool = True
    database_url: str = "sqlite:///data/logentra.db"
    api_key: str = ""
    # open = single-user / standalone (no login). users = require login or a user-bound API key.
    auth_mode: Literal["open", "users"] = "open"
    session_days: int = Field(7, ge=1, le=30)
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_equipment_cap: int = 1500
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    outbound_webhooks: list[OutboundWebhookConfig] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def production_defaults(cls, values):
        if isinstance(values, dict) and values.get("profile") == "production":
            values = {"auth_mode": "users", "session_days": 1, "open_browser": False, **values}
        return values

    @model_validator(mode="after")
    def validate_security(self):
        self.validate_bind(self.host)
        if "*" in self.cors_origins:
            raise ValueError("CORS requires explicit origins")
        if self.profile == "production" and (
            self.auth_mode != "users" or self.api_key or self.webhook_private_destinations
        ):
            raise ValueError("Production requires user authentication and public webhook destinations")
        return self

    def validate_bind(self, host: str) -> None:
        if self.auth_mode == "open" and not self.api_key and host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Unauthenticated mode requires a loopback bind")

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            if path and not path.startswith("/") and not path.startswith(":"):
                abs_path = (ROOT / path).resolve()
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                return "sqlite:///" + str(abs_path)
        return url


# Preferred LOGENTRA_* names; deprecated LOGBOOK_* names are still read as a fallback.
_ENV_FIELDS: list[tuple[str, tuple[str, ...], str]] = [
    ("profile", ("LOGENTRA_PROFILE",), "str"),
    ("cors_origins", ("LOGENTRA_CORS_ORIGINS",), "list"),
    ("request_limit_bytes", ("LOGENTRA_REQUEST_LIMIT_BYTES",), "int"),
    ("upload_limit_bytes", ("LOGENTRA_UPLOAD_LIMIT_BYTES",), "int"),
    ("host", ("LOGENTRA_HOST", "LOGBOOK_HOST"), "str"),
    ("port", ("LOGENTRA_PORT", "LOGBOOK_PORT"), "int"),
    ("open_browser", ("LOGENTRA_OPEN_BROWSER", "LOGBOOK_OPEN_BROWSER"), "bool"),
    ("database_url", ("LOGENTRA_DATABASE_URL", "LOGBOOK_DATABASE_URL"), "str"),
    ("api_key", ("LOGENTRA_API_KEY", "LOGBOOK_API_KEY"), "str"),
    ("auth_mode", ("LOGENTRA_AUTH_MODE", "LOGBOOK_AUTH_MODE"), "auth"),
    ("session_days", ("LOGENTRA_SESSION_DAYS", "LOGBOOK_SESSION_DAYS"), "int"),
    ("bootstrap_admin_username", (
        "LOGENTRA_BOOTSTRAP_ADMIN_USER", "LOGENTRA_BOOTSTRAP_ADMIN_USERNAME",
        "LOGBOOK_BOOTSTRAP_ADMIN_USER", "LOGBOOK_BOOTSTRAP_ADMIN_USERNAME",
    ), "str"),
    ("bootstrap_admin_password", ("LOGENTRA_BOOTSTRAP_ADMIN_PASSWORD", "LOGBOOK_BOOTSTRAP_ADMIN_PASSWORD"), "str"),
    ("bootstrap_equipment_cap", ("LOGENTRA_BOOTSTRAP_EQUIPMENT_CAP", "LOGBOOK_BOOTSTRAP_EQUIPMENT_CAP"), "int"),
]


def env_value(*names: str, default: str | None = None) -> str | None:
    """Return the first non-empty environment value among `names` (LOGENTRA_* first)."""
    for name in names:
        if name in os.environ and os.environ[name] != "":
            return os.environ[name]
    return default


def _cast(kind: str, val: str) -> Any:
    if kind == "int":
        return int(val)
    if kind == "bool":
        return val.lower() in ("1", "true", "yes", "on")
    if kind == "list":
        return [item.strip() for item in val.split(",") if item.strip()]
    if kind == "auth":
        mode = val.lower().strip()
        return "users" if mode in ("users", "user", "on", "1", "true") else mode
    return val


def _from_env(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for key, names, kind in _ENV_FIELDS:
        raw = env_value(*names)
        if raw is not None:
            out[key] = _cast(kind, raw)
    mqtt_on = env_value("LOGENTRA_MQTT_ENABLED", "LOGBOOK_MQTT_ENABLED")
    mqtt_host = env_value("LOGENTRA_MQTT_HOST", "LOGBOOK_MQTT_HOST")
    mqtt_port = env_value("LOGENTRA_MQTT_PORT", "LOGBOOK_MQTT_PORT")
    if mqtt_on is not None or mqtt_host or mqtt_port:
        mqtt = dict(out.get("mqtt") or {})
        if mqtt_on is not None:
            mqtt["enabled"] = mqtt_on.lower() in ("1", "true", "yes", "on")
        if mqtt_host:
            mqtt["host"] = mqtt_host
        if mqtt_port:
            mqtt["port"] = int(mqtt_port)
        out["mqtt"] = mqtt
    return out


def _maybe_legacy_sqlite(settings: Settings) -> Settings:
    """If the default Logentra SQLite path is unused, keep an existing data/logbook.db."""
    if settings.database_url != "sqlite:///data/logentra.db":
        return settings
    new = ROOT / "data" / "logentra.db"
    old = ROOT / "data" / "logbook.db"
    if not new.exists() and old.exists():
        settings.database_url = "sqlite:///data/logbook.db"
    return settings


def load_settings(path: Path | None = None) -> Settings:
    cfg_path = path or Path(env_value("LOGENTRA_CONFIG", "LOGBOOK_CONFIG", default=str(DEFAULT_CONFIG)))
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return _maybe_legacy_sqlite(Settings.model_validate(_from_env(data)))
