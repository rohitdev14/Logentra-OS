from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from PIL import Image

from logentra_server import documents, service
from logentra_server.models import DocumentVersion, QREntry, ReadingPhoto
from logentra_server.shift_models import ShiftLogAttachment
from logentra_server.upload_safety import DOCX_MIME, XLSX_MIME, detect_upload_mime
from test_qr import csrf, equipment
from test_shiftlog import post, rostered_shift

PDF = b"%PDF-1.4\n% document\n"
HTML = b"<html><body>Example</body></html>"
ELF = b"\x7fELF\0"
LIMIT = 1024


@pytest.fixture
def upload_client(settings, request):
    settings.upload_limit_bytes = LIMIT
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
        version = result["current_version"]
        assert version["mime"] == ("application/pdf" if kind == "document" else "text/plain")
        doc_id = document_id or result["id"]
        with client.app.state.SessionLocal() as db:
            stored = Path(db.get(DocumentVersion, version["id"]).storage_path)
        relative = stored.relative_to("data/attachments")
        assert relative.parent == Path("documents") / doc_id
        assert (client.app.state.storage.root / relative).read_bytes() == allowed[1]
        download = client.get(f"/api/documents/{doc_id}/file")
        assert_download(download, allowed[1], "inline" if kind == "document" else "attachment")
        count = 1 if kind == "document" else 2
        assert client.get("/api/storage").json()["documents"] == {"files": count, "bytes": count * len(allowed[1])}
    elif kind == "shift log":
        assert result["content_type"] == "text/plain"
        relative = Path(result["storage_path"])
        assert relative.parts[0] == "shiftlog" and relative.name == result["id"]
        assert (client.app.state.storage.root / relative).read_bytes() == allowed[1]
        assert_download(client.get(f"/api/shift-log-attachments/{result['id']}"), allowed[1], "attachment")
        assert client.get("/api/storage").json()["shift_log_attachments"] == {"files": 1, "bytes": len(allowed[1])}
    elif kind == "work order":
        assert result["content_type"] == "text/plain"
        assert result["size"] == len(allowed[1])
        relative = Path(result["storage_path"]).relative_to("data/attachments")
        assert relative.parent == Path(work_order["id"])
        assert (client.app.state.storage.root / relative).read_bytes() == allowed[1]
        assert all(total == {"files": 0, "bytes": 0} for total in client.get("/api/storage").json().values())
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
    assert not list(client.app.state.storage.root.iterdir())
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
    with client.app.state.SessionLocal() as db:
        relative = Path(db.get(ReadingPhoto, photo).storage_path)
    assert relative.parts[0] == "readings" and relative.name == photo
    assert (client.app.state.storage.root / relative).read_bytes() == image.getvalue()
    download = client.get(f"/api/readings/photos/{photo}")
    assert download.headers["content-type"] == "image/png"
    assert_download(download, image.getvalue(), "inline")


@pytest.mark.parametrize("kind", ["reading photo", "shift log"])
def test_attachment_database_failure_deletes_file(client, monkeypatch, kind):
    if kind == "reading photo":
        _, code = equipment(client)
        image = BytesIO()
        Image.new("RGB", (2, 2)).save(image, "PNG")
        entry_id = str(uuid4())
        url = f"/api/qr/{code}/entry"
        kwargs = {
            "data": {"entry_id": entry_id, "entered_by": "Operator"},
            "headers": csrf(client, code),
            "files": {"photos": ("photo.png", image.getvalue())},
        }
        folder, action, model = "readings", "reading.photo_added", ReadingPhoto
    else:
        shift = rostered_shift(client)
        entry = post(client, f"/shifts/{shift['id']}/entries", {"title": "Inspect pump"})
        url = f"/api/shift-log-entries/{entry['id']}/attachments"
        kwargs = {"files": {"file": ("notes.txt", b"Evidence")}}
        folder, action, model = "shiftlog", "shiftlog.attachment_added", ShiftLogAttachment

    storage = client.app.state.storage
    deleted = []
    audit, delete = service.audit, storage.delete

    def fail_attachment_audit(db, name, **fields):
        if name == action:
            assert len(list(storage.files(folder))) == 1
            raise RuntimeError("synthetic attachment database failure")
        return audit(db, name, **fields)

    def record_delete(key):
        deleted.append(key)
        delete(key)

    monkeypatch.setattr(service, "audit", fail_attachment_audit)
    monkeypatch.setattr(storage, "delete", record_delete)
    with pytest.raises(RuntimeError, match="synthetic attachment database failure"):
        client.post(url, **kwargs)
    assert len(deleted) == 1 and deleted[0].startswith(folder + "/")
    assert not list(storage.files(folder))
    with client.app.state.SessionLocal() as db:
        assert db.query(model).count() == 0
        if kind == "reading photo":
            assert db.get(QREntry, entry_id) is None


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
            db, storage=client.app.state.storage, title="Existing file", filename="legacy.png", data=HTML, mime="image/png", commit=False,
        )
        identifier = legacy.id
    response = client.get(f"/api/documents/{identifier}/file")
    assert_download(response, HTML, "attachment")
    assert response.headers["content-type"] == "application/octet-stream"
    assert client.get(f"/api/documents/{identifier}").json()["current_version"]["mime"] == "image/png"
