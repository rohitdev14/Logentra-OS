"""Optional MQTT subscriber. Disabled unless mqtt.enabled is true in config."""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

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


def start_mqtt(settings: Settings, session_factory) -> threading.Thread | None:
    if not settings.mqtt.enabled:
        return None
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log.warning("paho-mqtt is not installed; MQTT subscriber not started")
        return None

    cfg = settings.mqtt

    def on_connect(client, _u, _f, reason_code, *_a):
        log.info("MQTT connected (%s) — subscribing %s", reason_code, cfg.topics)
        for t in cfg.topics:
            client.subscribe(t)

    def on_message(_client, _u, msg):
        try:
            items = parse_message(msg.topic, msg.payload, cfg.topic_pattern)
            if not items:
                log.info("MQTT message on %s ignored (could not parse)", msg.topic)
                return
            db = session_factory()
            try:
                result = ingest_readings(db, items, source="mqtt", actor="mqtt", create_missing_equipment=False, fire_webhooks=True)
                log.info("MQTT ingest topic=%s added=%s oor=%s", msg.topic, result["added"], result["out_of_range"])
            finally:
                db.close()
        except Exception:
            log.exception("MQTT message handling failed")

    client = mqtt.Client(client_id=cfg.client_id, protocol=mqtt.MQTTv311, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    if cfg.username:
        client.username_pw_set(cfg.username, cfg.password or None)
    client.on_connect = on_connect
    client.on_message = on_message

    def run():
        try:
            client.connect(cfg.host, cfg.port, keepalive=60)
            client.loop_forever()
        except Exception:
            log.exception("MQTT loop exited")

    t = threading.Thread(target=run, name="logbook-mqtt", daemon=True)
    t.start()
    log.info("MQTT subscriber starting for %s:%s", cfg.host, cfg.port)
    return t
