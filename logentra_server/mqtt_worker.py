"""Optional MQTT subscriber. Disabled unless mqtt.enabled is true in config."""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

from fastapi import HTTPException

from logentra_server.auth import Principal
from logentra_server.config import Settings
from logentra_server.service import ingest_readings

log = logging.getLogger("logentra.mqtt")


def _match_pattern(pattern: str, topic: str) -> dict[str, str]:
    """Extract {tag} / {parameter} style placeholders from a topic."""
    p_parts = pattern.strip("/").split("/")
    t_parts = topic.strip("/").split("/")
    if len(p_parts) != len(t_parts):
        return {}
    out = {}
    for p, t in zip(p_parts, t_parts):
        if p.startswith("{") and p.endswith("}"):
            out[p[1:-1]] = t
        elif p not in ("+", t):
            return {}
    return out


def parse_message(topic: str, payload: bytes, pattern: str) -> list[dict[str, Any]]:
    text = payload.decode("utf-8", errors="replace").strip()
    data: Any
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        data = text
    if isinstance(data, (int, float)) or (isinstance(data, str) and data):
        if isinstance(data, str):
            try:
                data = float(data)
            except ValueError:
                return []
        if isinstance(data, (int, float)):
            extracted = _match_pattern(pattern, topic)
            if extracted.get("tag") and extracted.get("parameter"):
                return [{"tag": extracted["tag"], "parameter": extracted["parameter"], "value": float(data), "source": "mqtt"}]
            return []

    extracted = _match_pattern(pattern, topic)
    items = data if isinstance(data, list) else data.get("readings") if isinstance(data, dict) and "readings" in data else [data]
    out = []
    if not isinstance(items, list):
        return []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.setdefault("tag", extracted.get("tag") or item.get("equipment"))
        item.setdefault("parameter", extracted.get("parameter") or item.get("key"))
        item["source"] = "mqtt"
        if item.get("tag") and item.get("parameter") is not None and item.get("value") is not None:
            out.append(item)
    return out


def start_mqtt(settings: Settings, session_factory):
    connected = threading.Event()
    if not settings.mqtt.enabled:
        return None, connected
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log.warning("paho-mqtt is not installed; MQTT subscriber not started")
        return None, connected

    cfg = settings.mqtt
    principal = Principal(mode="integration", username="mqtt", site_id=cfg.site_id,
                          area_id=cfg.area_id, permissions={"equipment.write"})

    def on_connect(client, _u, _f, reason_code, *_a):
        if reason_code.is_failure:
            log.warning("MQTT connection rejected")
            return
        connected.set()
        log.info("MQTT connected")
        for t in cfg.topics:
            client.subscribe(t)

    def on_disconnect(_client, _u, _flags, _reason, _properties):
        connected.clear()

    def on_message(_client, _u, msg):
        try:
            if len(msg.payload) > settings.request_limit_bytes:
                log.warning("MQTT payload too large")
                return
            items = parse_message(msg.topic, msg.payload, cfg.topic_pattern)
            if not items:
                log.warning("MQTT message could not be parsed")
                return
            db = session_factory()
            try:
                result = ingest_readings(db, items, source="mqtt", actor="mqtt", create_missing_equipment=False, fire_webhooks=True, principal=principal)
                log.info("MQTT ingest added=%s skipped=%s oor=%s", result["added"], result["skipped"], result["out_of_range"])
            finally:
                db.close()
        except HTTPException as error:
            log.warning("MQTT reading rejected (%s): %s", error.status_code, error.detail)
        except Exception:
            log.error("MQTT message handling failed")

    client = mqtt.Client(client_id=cfg.client_id, protocol=mqtt.MQTTv311, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    if cfg.tls:
        client.tls_set()
    if cfg.username:
        client.username_pw_set(cfg.username, cfg.password or None)
    client.on_connect = on_connect
    client.on_message = on_message

    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect_async(cfg.host, cfg.port, keepalive=60)
    client.loop_start()
    return client, connected
