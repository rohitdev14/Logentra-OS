"""File type checks shared by imports, attachments and previews."""
import json
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException

CSV_MIME = "text/csv"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_TYPES = {".txt": "text/plain", ".csv": CSV_MIME}
OFFICE_TYPES = {
    ".xlsx": (XLSX_MIME, "xl/workbook.xml"),
    ".docx": (DOCX_MIME, "word/document.xml"),
}
PREVIEW_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
ZIP_SIGNATURE = b"PK\x03\x04"


def extension(filename):
    return Path(filename or "").suffix.lower()


def require_extension(filename, allowed):
    suffix = extension(filename)
    if suffix[1:] not in allowed:
        types = ", ".join(f".{item}" for item in allowed)
        raise HTTPException(415, f"Unsupported file extension. Allowed extensions: {types or 'none'}")
    return suffix


def binary_mime(data):
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return next((mime for signature, mime in SIGNATURES if data.startswith(signature)), None)


def utf8_text(data):
    if b"\x00" in data:
        raise ValueError("NUL bytes are not allowed")
    return data.decode("utf-8-sig")


def detect_upload_mime(filename, data, allowed):
    suffix = require_extension(filename, allowed)
    detected = binary_mime(data)
    if detected and detected == PREVIEW_TYPES.get(suffix):
        return detected
    if suffix in OFFICE_TYPES and data.startswith(ZIP_SIGNATURE):
        mime, member = OFFICE_TYPES[suffix]
        try:
            with ZipFile(BytesIO(data)) as archive:
                if "[Content_Types].xml" in archive.NameToInfo and member in archive.NameToInfo:
                    return mime
        except (BadZipFile, UnicodeDecodeError):
            pass
    if suffix in TEXT_TYPES and not detected and not data.startswith((ZIP_SIGNATURE, b"\x7fELF", b"MZ")):
        try:
            utf8_text(data)
        except ValueError:
            pass
        else:
            return TEXT_TYPES[suffix]
    raise HTTPException(415, f"File contents do not match the {suffix} extension")


def load_json_upload(filename, data, allowed):
    require_extension(filename, allowed)
    try:
        return json.loads(utf8_text(data))
    except (ValueError, RecursionError):
        raise HTTPException(415, "File must contain valid UTF-8 JSON") from None


def preview_mime(path, expected, allowed):
    if expected not in allowed:
        return None
    with path.open("rb") as handle:
        return expected if binary_mime(handle.read(12)) == expected else None
