from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from logentra_server.models import OutboundWebhook, WebhookDelivery
from logentra_server.utils import dumps, uid, utcnow_iso


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


def deliver(db: Session, event: str, payload: dict[str, Any], timeout: float = 5.0) -> list[dict[str, Any]]:
    """POST payload to every enabled webhook for this event. Returns delivery summaries."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    results = []
    for wh in list(iter_webhooks(db, event)):
        headers = {
            "Content-Type": "application/json",
            "X-Logentra-Event": event,
            "X-Logbook-Event": event,  # deprecated alias
        }
        if wh.secret:
            sig = "sha256=" + sign_payload(wh.secret, body)
            headers["X-Logentra-Signature"] = sig
            headers["X-Logbook-Signature"] = sig  # deprecated alias
        rec = WebhookDelivery(
            id=uid("whd"),
            webhook_id=wh.id,
            event=event,
            payload=dumps(payload),
            created_at=utcnow_iso(),
            ok=0,
        )
        try:
            resp = httpx.post(wh.url, content=body, headers=headers, timeout=timeout)
            rec.status_code = resp.status_code
            rec.ok = 1 if resp.is_success else 0
            if not resp.is_success:
                rec.error = (resp.text or "")[:500]
        except Exception as exc:  # noqa: BLE001 — record any delivery failure
            rec.error = str(exc)[:500]
        db.add(rec)
        results.append({"webhook_id": wh.id, "url": wh.url, "ok": bool(rec.ok), "status_code": rec.status_code, "error": rec.error})
    if results:
        db.commit()
    return results


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
        "ts": reading.ts,
        "source": reading.source,
        "acknowledged": False,
    }
