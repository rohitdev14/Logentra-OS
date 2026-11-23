from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"
EXAMPLE_CONFIG = ROOT / "config.example.yaml"


class MqttConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 1883
    username: str = ""
    password: str = ""
    client_id: str = "logbook-v3"
    topics: list[str] = Field(default_factory=lambda: ["logbook/readings", "logbook/+/+"])
    topic_pattern: str = "logbook/{tag}/{parameter}"


class OutboundWebhookConfig(BaseModel):
    url: str
    events: list[str] = Field(default_factory=lambda: ["out_of_range"])
    secret: str = ""


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    open_browser: bool = True
    database_url: str = "sqlite:///data/logbook.db"
    api_key: str = ""
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    outbound_webhooks: list[OutboundWebhookConfig] = Field(default_factory=list)

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


def _from_env(data: dict[str, Any]) -> dict[str, Any]:
    env_map = {
        "LOGBOOK_HOST": "host",
        "LOGBOOK_PORT": "port",
        "LOGBOOK_OPEN_BROWSER": "open_browser",
        "LOGBOOK_DATABASE_URL": "database_url",
        "LOGBOOK_API_KEY": "api_key",
    }
    out = dict(data)
    for env, key in env_map.items():
        if env in os.environ and os.environ[env] != "":
            val = os.environ[env]
            if key == "port":
                out[key] = int(val)
            elif key == "open_browser":
                out[key] = val.lower() in ("1", "true", "yes", "on")
            else:
                out[key] = val
    if os.environ.get("LOGBOOK_MQTT_ENABLED"):
        mqtt = dict(out.get("mqtt") or {})
        mqtt["enabled"] = os.environ["LOGBOOK_MQTT_ENABLED"].lower() in ("1", "true", "yes", "on")
        if os.environ.get("LOGBOOK_MQTT_HOST"):
            mqtt["host"] = os.environ["LOGBOOK_MQTT_HOST"]
        if os.environ.get("LOGBOOK_MQTT_PORT"):
            mqtt["port"] = int(os.environ["LOGBOOK_MQTT_PORT"])
        out["mqtt"] = mqtt
    return out


def load_settings(path: Path | None = None) -> Settings:
    cfg_path = path or Path(os.environ.get("LOGBOOK_CONFIG", DEFAULT_CONFIG))
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(_from_env(data))
