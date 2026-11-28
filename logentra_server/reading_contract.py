"""Validation and replay metadata for new readings."""
from hashlib import sha256
import json
from typing import get_args

from fastapi import HTTPException

from logentra_server.models import AuditLog, Reading
from logentra_server.schemas import ReadingQuality, ReadingSource

SOURCES = get_args(ReadingSource)
QUALITIES = get_args(ReadingQuality)
RECEIPT_ACTION = "reading.idempotency"


def fields(raw, default_source):
    source = raw.get("source")
    source = default_source if source is None else source
    quality = raw.get("quality")
    quality = "good" if quality is None else quality
    unit = raw.get("unit")
    key = raw.get("idempotency_key")
    if source not in SOURCES:
        raise HTTPException(422, f"Reading source must be one of: {', '.join(SOURCES)}")
    if quality not in QUALITIES:
        raise HTTPException(422, "Reading quality must be good, uncertain, bad or substituted")
    if unit is not None and (not isinstance(unit, str) or "\x00" in unit):
        raise HTTPException(422, "Reading unit must be text without NUL characters")
    if key is not None:
        if not isinstance(key, str) or "\x00" in key:
            raise HTTPException(422, "Idempotency key must be text without NUL characters")
        try:
            key_size = len(key.encode("utf-8"))
        except UnicodeEncodeError:
            raise HTTPException(422, "Idempotency key must be valid UTF-8 text") from None
        # Keep keys within PostgreSQL's B-tree index tuple size for regular equipment IDs.
        if key_size > 1024:
            raise HTTPException(422, "Idempotency key must be at most 1024 UTF-8 bytes")
    return unit, quality, source, key


def snapshot_unit(unit, configured):
    if unit is not None and configured and unit != configured:
        raise HTTPException(422, "Reading unit does not match the equipment parameter")
    return (configured or None) if unit is None else unit


def metadata(reading):
    return {"unit": reading.unit, "quality": reading.quality, "source": reading.source,
            "idempotency_key": reading.idempotency_key}


def fingerprint(payload):
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def find_receipt(db, equipment_id, key):
    return db.query(Reading).filter_by(equipment_id=equipment_id, idempotency_key=key).one_or_none()


def require_matching_receipt(db, reading, digest):
    # Record the original digest in the existing audit log so later annotation
    # edits and parameter changes cannot change a retry's identity.
    recorded = db.query(AuditLog.detail).filter_by(
        entity_type="reading", entity_id=reading.id, action=RECEIPT_ACTION,
    ).order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(1).scalar()
    if recorded is None or json.loads(recorded).get("fingerprint") != digest:
        raise HTTPException(409, "Idempotency key already belongs to different reading data")
