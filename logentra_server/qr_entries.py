"""Validation and storage for phone quick-entry readings."""
import json
import math
from hashlib import sha256
from io import BytesIO
from uuid import UUID

from fastapi import HTTPException
from PIL import Image
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from logentra_server import reading_contract, service
from logentra_server.config import ROOT
from logentra_server.http_security import read_upload
from logentra_server.models import QREntry, Reading, ReadingPhoto
from logentra_server.documents import safe_filename
from logentra_server.utils import uid, utcnow_iso

ATTACHMENTS = ROOT / "data" / "attachments"
PHOTO_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def entry_values(raw, equipment):
    try:
        values = json.loads(raw)
    except ValueError:
        raise HTTPException(422, "Values must be a JSON object") from None
    if not isinstance(values, dict):
        raise HTTPException(422, "Values must be a JSON object")
    parameters = {parameter.key for parameter in equipment.parameters}
    errors = {}
    validated = {}
    for key, value in values.items():
        if key not in parameters:
            errors[key] = "Unknown parameter"
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        try:
            number = float(str(value).strip().replace(",", "."))
            if not math.isfinite(number):
                raise ValueError
        except ValueError:
            errors[key] = "Enter a valid number"
        else:
            validated[key] = number
    if errors:
        raise HTTPException(422, errors)
    return validated


async def photo_bytes(upload, limit):
    data = await read_upload(upload, limit)
    try:
        with Image.open(BytesIO(data), formats=tuple(PHOTO_TYPES)) as image:
            content_type = PHOTO_TYPES[image.format]
            width, height = image.size
            image.verify()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise HTTPException(422, "Photos must be valid JPEG, PNG or WebP images") from None
    return data, content_type, width, height


def attachment_path(relative, category):
    root = (ATTACHMENTS / category).resolve()
    path = (ATTACHMENTS / relative).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(404, "Attachment not found")
    return path


def entry_result(db, entry):
    rows = db.query(Reading).filter_by(entry_id=entry.id, equipment_id=entry.equipment_id, source="qr").all()
    photos = db.query(ReadingPhoto.id).filter_by(entry_id=entry.id, equipment_id=entry.equipment_id)
    return {
        "entry_id": entry.id, "saved_at": entry.created_at, "note": entry.note,
        "values": [{"id": r.id, "parameter": r.parameter_key, "value": r.value,
                    "out_of_range": bool(r.out_of_range), **reading_contract.metadata(r)} for r in rows],
        "photos": [pid for (pid,) in photos],
    }


async def save_entry(db, equipment, principal, settings, entry_id, values, note, entered_by, photos):
    try:
        entry_id = str(UUID(entry_id))
    except ValueError:
        raise HTTPException(422, "entry_id must be a UUID") from None
    existing = db.get(QREntry, entry_id)
    if existing:
        if existing.equipment_id != equipment.id:
            raise HTTPException(409, "Entry ID already belongs to another equipment item")
        return entry_result(db, existing)
    values = entry_values(values, equipment)
    note = note.strip()
    actor = entered_by.strip() if principal.mode == "open" else principal.username
    if not actor:
        raise HTTPException(422, "Enter your name")
    if len(photos) > settings.photos_per_entry:
        raise HTTPException(422, f"Attach at most {settings.photos_per_entry} photos")
    if not (values or note or photos):
        raise HTTPException(422, "Enter a value, a note or a photo")
    photo_limit = min(settings.photo_max_bytes, settings.upload_limit_bytes)
    uploads = [(file, await photo_bytes(file, photo_limit)) for file in photos]
    now = utcnow_iso()
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else postgres_insert
    statement = insert(QREntry).values(id=entry_id, equipment_id=equipment.id, note=note,
                                      entered_by=actor, created_at=now)
    entry = db.execute(statement.on_conflict_do_nothing(index_elements=[QREntry.id])
                       .returning(QREntry)).scalar_one_or_none()
    if entry is None:
        entry = db.get(QREntry, entry_id)
        if entry.equipment_id != equipment.id:
            raise HTTPException(409, "Entry ID already belongs to another equipment item")
        return entry_result(db, entry)
    results = service.ingest_readings(
        db, [{"equipment_id": equipment.id, "parameter": key, "value": value,
              "entry_id": entry_id, "ts": now, "remarks": note, "entered_by": actor}
             for key, value in values.items()],
        source="qr", actor=actor, principal=principal, commit=False,
    )
    paths = []
    try:
        for upload, (data, content_type, width, height) in uploads:
            pid = uid("photo")
            relative = f"readings/{now[:4]}/{now[5:7]}/{pid}"
            path = attachment_path(relative, "readings")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                paths.append(path)
                output.write(data)
            db.add(ReadingPhoto(id=pid, entry_id=entry_id, equipment_id=equipment.id,
                                filename=safe_filename(upload.filename or "photo"), content_type=content_type,
                                size=len(data), width=width, height=height, sha256=sha256(data).hexdigest(),
                                storage_path=relative, uploaded_by=actor, user_id=principal.user_id, created_at=now))
            service.audit(db, "reading.photo_added", actor=actor, user_id=principal.user_id,
                          entity_type="reading_photo", entity_id=pid, detail={"entry_id": entry_id})
        service.audit(db, "qr.entry_saved", actor=actor, user_id=principal.user_id, entity_type="equipment",
                      entity_id=equipment.id, detail={"entry_id": entry_id, "count": results["added"],
                      "out_of_range": results["out_of_range"], "photos": len(uploads)})
        db.flush()
        response = entry_result(db, entry)
        db.commit()
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    return response
