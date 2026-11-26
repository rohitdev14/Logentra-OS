from __future__ import annotations

import hashlib
import hmac
import json
import ipaddress
import logging
import socket
import threading
import time
from urllib.parse import urlsplit
from typing import Any

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from logentra_server.models import OutboundWebhook, WebhookDelivery
from logentra_server import sitetime
from logentra_server.utils import uid, utcnow_iso

log = logging.getLogger("logentra.delivery")


def iter_webhooks(db: Session, event: str):
    rows = db.query(OutboundWebhook).filter(OutboundWebhook.enabled == 1).all()
    for wh in rows:
        try:
            events = json.loads(wh.events or "[]")
        except json.JSONDecodeError:
            events = ["out_of_range"]
        if event in events or "*" in events:
            yield wh


def sign_payload(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def validate_destination(url: str):
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"} or not parts.hostname
        or parts.username is not None or parts.password is not None or parts.fragment
        or any(ord(char) <= 32 or char == "\\" for char in url)
    ):
        raise ValueError("Invalid webhook destination")
    _ = parts.port
    return parts


def resolve_destination(parts, allow_private: bool):
    host = parts.hostname.encode("idna").decode("ascii")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )]
    if not addresses or (not allow_private and any(
        not address.is_global or address.is_multicast or address.is_reserved
        or (getattr(address, "ipv4_mapped", None) and not address.ipv4_mapped.is_global)
        for address in addresses
    )):
        raise ValueError("Webhook destination is outside the allowed address range")
    return host, str(addresses[0])


def send_webhook(url, event, event_id, body, secret, settings):
    parts = validate_destination(url)
    host, address = resolve_destination(parts, settings.webhook_private_destinations)
    destination = httpx.URL(url).copy_with(host=address)
    headers = {
        "Content-Type": "application/json",
        "X-Logentra-Event": event,
        "X-Logbook-Event": event,
        "X-Logentra-Event-ID": event_id,
        "Host": parts.netloc.encode("idna").decode("ascii"),
    }
    if secret:
        signature = "sha256=" + sign_payload(secret, body)
        headers["X-Logentra-Signature"] = signature
        headers["X-Logbook-Signature"] = signature
    with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
        with client.stream("POST", destination, content=body, headers=headers,
                           extensions={"sni_hostname": host}) as response:
            return response.status_code


def enqueue(db: Session, event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Stage delivery rows in the same transaction as the engineering change."""
    body = json.dumps(payload, ensure_ascii=False)
    results = []
    for webhook in iter_webhooks(db, event):
        event_id = uid("whd")
        db.add(WebhookDelivery(
            id=event_id, webhook_id=webhook.id, event=event, payload=body,
            created_at=utcnow_iso(), ok=0, state="pending", available_at=time.time(),
        ))
        results.append({"id": event_id, "webhook_id": webhook.id, "state": "pending"})
    return results


def replay_dead_delivery(db: Session, delivery_id: str) -> bool:
    """Requeue without creating a new event or changing its payload."""
    available = db.query(OutboundWebhook.id).filter(OutboundWebhook.enabled == 1)
    return bool(db.query(WebhookDelivery).filter(
        WebhookDelivery.id == delivery_id,
        WebhookDelivery.state == "dead",
        WebhookDelivery.replays < 3,
        WebhookDelivery.webhook_id.in_(available.statement),
    ).update({
        "state": "pending", "attempts": 0, "replays": WebhookDelivery.replays + 1,
        "available_at": time.time(), "claim_token": None,
    }, synchronize_session=False))


def dispatch_one(session_factory, settings) -> bool:
    now = time.time()
    token = uid("claim")
    with session_factory() as db:
        due = (WebhookDelivery.state.in_(("pending", "sending")), WebhookDelivery.available_at <= now)
        row = db.query(WebhookDelivery).filter(*due).order_by(
            WebhookDelivery.available_at, WebhookDelivery.id
        ).first()
        if row is None:
            return False
        claimed = db.query(WebhookDelivery).filter(WebhookDelivery.id == row.id, *due).update(
            {"state": "sending", "claim_token": token, "available_at": now + 120},
            synchronize_session=False,
        )
        if not claimed:
            return True
        delivery_id, event, attempt = row.id, row.event, row.attempts + 1
        body = (row.payload or "{}").encode("utf-8")
        webhook = row.webhook
        destination = (webhook.url, webhook.secret) if webhook and webhook.enabled else None
        db.commit()
    code = None
    error = None
    if destination is None:
        error = "Webhook disabled or removed"
    else:
        try:
            code = send_webhook(destination[0], event, delivery_id, body, destination[1], settings)
        except ValueError:
            error = "Destination rejected"
        except (httpx.HTTPError, OSError):
            error = "Delivery connection failed"
    ok = code is not None and 200 <= code < 300
    if not ok and error is None:
        error = "Webhook returned an unsuccessful status"
    retry = (
        not ok and attempt < settings.webhook_attempts
        and error not in {"Webhook disabled or removed", "Destination rejected"}
    )
    with session_factory.begin() as db:
        db.query(WebhookDelivery).filter_by(id=delivery_id, claim_token=token).update({
            "state": "delivered" if ok else "pending" if retry else "dead",
            "attempts": attempt, "claim_token": None, "status_code": code, "ok": int(ok), "error": error,
            "available_at": time.time() + min(3600, settings.webhook_backoff_seconds * 2 ** (attempt - 1))
            if retry else None,
        }, synchronize_session=False)
    return True


class DeliveryWorker:
    def __init__(self, session_factory, settings):
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self.run, args=(session_factory, settings), name="logentra-delivery", daemon=True,
        )

    def run(self, session_factory, settings):
        while not self.stop_event.is_set():
            try:
                if dispatch_one(session_factory, settings):
                    continue
            except SQLAlchemyError:
                log.error("Delivery queue database operation failed")
            self.stop_event.wait(0.1)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=12)


def oor_payload(reading, tag: str, parameter_label: str | None = None) -> dict[str, Any]:
    return {
        "event": "out_of_range",
        "at": utcnow_iso(),
        "reading_id": reading.id,
        "entry_id": reading.entry_id,
        "equipment_id": reading.equipment_id,
        "tag": tag,
        "parameter": reading.parameter_key,
        "parameter_label": parameter_label or reading.parameter_key,
        "value": reading.value,
        "min": reading.limit_min,
        "max": reading.limit_max,
        "ts": sitetime.utc_iso_z(reading.ts),
        "source": reading.source,
        "acknowledged": False,
    }
