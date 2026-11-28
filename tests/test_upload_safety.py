from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from PIL import Image

from logentra_server import documents, qr_entries
from logentra_server.routers import work_orders
from logentra_server.upload_safety import DOCX_MIME, XLSX_MIME, detect_upload_mime
from test_qr import csrf, equipment
from test_shiftlog import post, rostered_shift

PDF = b"%PDF-1.4\n% document\n"
HTML = b"<html><body>Example</body></html>"
ELF = b"\x7fELF\0"
LIMIT = 1024


@pytest.fixture
def upload_client(settings, monkeypatch, tmp_path, request):
    settings.upload_limit_bytes = LIMIT
    monkeypatch.setattr(documents, "ROOT", tmp_path)
    monkeypatch.setattr(documents, "ATTACH_DIR", tmp_path / "attachments" / "documents")
    monkeypatch.setattr(qr_entries, "ATTACHMENTS", tmp_path / "attachments")
    monkeypatch.setattr(work_orders, "ROOT", tmp_path)
    monkeypatch.setattr(work_orders, "ATTACH_DIR", tmp_path / "attachments")
    return request.getfixturevalue("client")


@pytest.mark.parametrize("kind", ["document", "version", "work order", "shift log", "table", "backup"])
def test_upload_routes_validate_type_and_size(upload_client, kind):
    client = upload_client
    document_id = None
    created = 201
    allowed = ("notes.TXT", b"Evidence", "image/png")
    if kind == "document":
        path = "/api/documents"
        allowed = ("document.PDF", PDF, "text/html")
    elif kind == "version":
        first = client.post("/api/documents", files={"file": allowed})
        assert first.status_code == 201, first.text
        document_id = first.json()["id"]
        path = f"/api/documents/{document_id}/versions"
    elif kind == "work order":
        work_order = client.post("/api/work-orders", json={"title": "Inspect pump"}).json()
        path = f"/api/work-orders/{work_order['id']}/attachments"
    elif kind == "shift log":
        shift = rostered_shift(client)
        entry = post(client, f"/shifts/{shift['id']}/entries", {"title": "Inspect pump"})
        path = f"/api/shift-log-entries/{entry['id']}/attachments"
    elif kind == "table":
        path = "/api/ingest/file?kind=equipment"
        allowed = ("assets.CSV", b"tag,category\nP-101,Pump\n", "image/png")
        created = 200
    else:
        path = "/api/migrate/v2/file?replace=false"
        allowed = ("backup.JSON", b'{"equipment":[]}', "image/png")
        created = 200

    for sample in [("program.pdf", ELF, "application/pdf"), ("photo.png", HTML, "image/png")]:
        response = client.post(path, files={"file": sample})
        assert response.status_code == 415, response.text
        assert "extension" in response.json()["detail"]
    oversized = client.post(path, files={"file": (allowed[0], b"x" * (LIMIT + 1))})
    assert oversized.status_code == 413, oversized.text
    response = client.post(path, files={"file": allowed})
    assert response.status_code == created, response.text

    result = response.json()
    if kind in {"document", "version"}:
        assert result["current_version"]["mime"] == ("application/pdf" if kind == "document" else "text/plain")
        download = client.get(f"/api/documents/{document_id or result['id']}/file")
        assert_download(download, allowed[1], "inline" if kind == "document" else "attachment")
    elif kind == "shift log":
        assert result["content_type"] == "text/plain"
        assert_download(client.get(f"/api/shift-log-attachments/{result['id']}"), allowed[1], "attachment")
    elif kind == "work order":
        assert result["content_type"] == "text/plain"
        assert result["size"] == len(allowed[1]) and result["storage_path"]
    elif kind == "table":
        assert result["added"] == 1


def assert_download(response, body, disposition):
    assert response.status_code == 200, response.text
    assert response.content == body
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith(disposition + ";")


def test_work_order_metadata_has_no_verified_mime(upload_client):
    client = upload_client
    work_order = client.post("/api/work-orders", json={"title": "Inspect pump"}).json()
    path = f"/api/work-orders/{work_order['id']}/attachments"
    body = {"filename": "notes.pdf", "size": 100, "content_type": "text/html"}
    response = client.post(path, json=body)
    assert response.status_code == 201, response.text
    assert response.json()["content_type"] is None
    assert response.json()["storage_path"] is None
    assert client.post(path, json={**body, "size": LIMIT + 1}).status_code == 413
    assert client.post(path, json={**body, "filename": "notes.exe"}).status_code == 415
    assert client.post(path, files={"unused": ("test.txt", b"unused")}).status_code == 400


def test_qr_photo_has_global_size_limit_and_verified_preview(upload_client):
    client = upload_client
    _, code = equipment(client)
    headers = csrf(client, code)
    path = f"/api/qr/{code}/entry"
    body = {"entry_id": str(uuid4()), "entered_by": "Operator"}
    assert client.post(path, data=body, headers=headers, files={"photos": ("photo.png", HTML, "image/png")}).status_code == 422
    assert client.post(path, data=body, headers=headers, files={"photos": ("photo.png", b"x" * (LIMIT + 1))}).status_code == 413
    image = BytesIO()
    Image.new("RGB", (2, 2)).save(image, "PNG")
    response = client.post(path, data=body, headers=headers, files={"photos": ("photo.png", image.getvalue(), "text/html")})
    assert response.status_code == 200, response.text
    photo, = response.json()["photos"]
    download = client.get(f"/api/readings/photos/{photo}")
    assert download.headers["content-type"] == "image/png"
    assert_download(download, image.getvalue(), "inline")


def test_backup_upload_rejects_invalid_json_and_caps_raw_json(upload_client):
    client = upload_client
    bad = client.post("/api/migrate/v2/file", files={"file": ("backup.json", HTML, "application/json")})
    assert bad.status_code == 415, bad.text
    assert client.post("/api/migrate/v2?replace=false", json={"equipment": []}).status_code == 200
    assert client.post("/api/migrate/v2", json={"content": "x" * LIMIT}).status_code == 413


@pytest.mark.parametrize(("filename", "data"), [
    ("notes.txt", b"one\0two"), ("readings.csv", b"tag,\xff"), ("notes.txt", PDF), ("sheet.xlsx", b"PK\x03\x04"),
])
def test_upload_detector_rejects_mismatches(settings, filename, data):
    with pytest.raises(HTTPException) as exc_info:
        detect_upload_mime(filename, data, settings.attachment_extensions)
    assert exc_info.value.status_code == 415


@pytest.mark.parametrize(("filename", "member", "mime"), [
    ("sheet.xlsx", "xl/workbook.xml", XLSX_MIME), ("notes.docx", "word/document.xml", DOCX_MIME),
])
def test_office_upload_requires_matching_package(settings, filename, member, mime):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(member, "<document/>")
    data = buffer.getvalue()
    assert detect_upload_mime(filename, data, settings.attachment_extensions) == mime
    wrong = "sheet.docx" if filename.endswith(".xlsx") else "notes.xlsx"
    with pytest.raises(HTTPException) as exc_info:
        detect_upload_mime(wrong, data, settings.attachment_extensions)
    assert exc_info.value.status_code == 415


def test_attachment_allowlist_can_be_narrowed(upload_client, settings):
    settings.attachment_extensions = ("txt",)
    response = upload_client.post("/api/documents", files={"file": ("document.pdf", PDF)})
    assert response.status_code == 415
    assert ".txt" in response.json()["detail"]


def test_legacy_document_stays_downloadable_without_inline_preview(upload_client):
    client = upload_client
    with client.app.state.SessionLocal.begin() as db:
        legacy = documents.create_document(
            db, title="Existing file", filename="legacy.png", data=HTML, mime="image/png", commit=False,
        )
        identifier = legacy.id
    response = client.get(f"/api/documents/{identifier}/file")
    assert_download(response, HTML, "attachment")
    assert response.headers["content-type"] == "application/octet-stream"
    assert client.get(f"/api/documents/{identifier}").json()["current_version"]["mime"] == "image/png"
