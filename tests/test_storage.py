from pathlib import Path, PureWindowsPath

import pytest
from pydantic import ValidationError

from logentra_server import storage
from logentra_server.app import create_app
from logentra_server.config import Settings, load_settings
from logentra_server.storage import InvalidStorageKey, LocalStorage


def test_storage_settings_and_startup(tmp_path, monkeypatch, db_url):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    monkeypatch.delenv("LOGENTRA_STORAGE", raising=False)
    monkeypatch.delenv("LOGENTRA_ATTACHMENTS_DIR", raising=False)
    config = tmp_path / "missing.yaml"
    defaults = load_settings(config)
    assert defaults.storage == "local"
    assert LocalStorage(defaults.attachments_dir).root == tmp_path / "data" / "attachments"

    monkeypatch.setenv("LOGENTRA_STORAGE", "local")
    monkeypatch.setenv("LOGENTRA_ATTACHMENTS_DIR", "custom/attachments")
    settings = load_settings(config)
    settings.database_url = db_url
    app = create_app(settings)
    try:
        assert app.state.storage.root == tmp_path / "custom" / "attachments"
        assert app.state.storage.root.is_dir()
    finally:
        app.state.engine.dispose()

    monkeypatch.setenv("LOGENTRA_STORAGE", "invalid")
    with pytest.raises(ValidationError, match="storage"):
        load_settings(config)


def test_startup_rejects_attachment_root_that_is_a_file(tmp_path, db_url):
    root = tmp_path / "attachment-file"
    root.touch()
    with pytest.raises(ValueError, match="Unusable attachment directory"):
        create_app(Settings(database_url=db_url, attachments_dir=root))


def test_local_storage_contract(tmp_path):
    backend = LocalStorage(tmp_path / "uploads")
    backend.initialize()
    key = "readings/2026/11/photo_123"
    backend.put(key, b"photo bytes")
    assert backend.exists(key)
    assert backend.size(key) == len(b"photo bytes")
    assert list(backend.files("readings")) == [key]
    assert backend.safe_key(key) == backend.root / key
    with pytest.raises(FileExistsError):
        backend.put(key, b"replacement")
    with backend.open(key) as handle:
        assert handle.read() == b"photo bytes"
    backend.delete(key)
    backend.delete(key)
    assert not backend.exists(key)


def test_existing_keys_resolve_without_rewriting(tmp_path):
    backend = LocalStorage(tmp_path / "relocated")
    key = Path("documents/doc_1/v1_hash_file_1_evidence.txt")
    existing = backend.root / key
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing evidence")
    info = existing.stat()
    legacy = Path("data/attachments") / key
    for alias in (key.as_posix(), legacy.as_posix(), str(PureWindowsPath(legacy))):
        assert backend.safe_key(alias, category="documents") == existing
        with backend.open(alias) as handle:
            assert handle.read() == b"existing evidence"
    assert existing.stat().st_ino == info.st_ino
    assert existing.stat().st_mtime_ns == info.st_mtime_ns
    assert list(existing.parent.iterdir()) == [existing]


@pytest.mark.parametrize("key", [
    "", ".", "../outside", "readings/../outside", r"readings\..\outside",
    "data/attachments", "data/attachments/../outside", "/absolute",
    r"C:\absolute", r"C:relative", r"\\server\share\attachment",
])
def test_invalid_keys_are_rejected(tmp_path, key):
    backend = LocalStorage(tmp_path / "uploads")
    with pytest.raises(InvalidStorageKey, match="Invalid attachment key"):
        backend.put(key, b"should never be written")
    assert not backend.root.exists()


def test_symlink_escapes_are_rejected(tmp_path):
    backend = LocalStorage(tmp_path / "uploads")
    backend.initialize()
    outside = tmp_path / "outside"
    outside.mkdir()
    original = outside / "original"
    original.write_bytes(b"outside")
    (backend.root / "readings").symlink_to(outside, target_is_directory=True)
    (backend.root / "alias").symlink_to(original)
    with pytest.raises(InvalidStorageKey, match="escapes"):
        backend.put("readings/new", b"outside write")
    for operation in (backend.safe_key, backend.open, backend.exists, backend.size, backend.delete):
        with pytest.raises(InvalidStorageKey, match="escapes"):
            operation("alias")
    assert list(backend.files("readings")) == []
    assert list(outside.iterdir()) == [original]
    assert original.read_bytes() == b"outside"


def test_category_rejects_in_tree_alias_to_another_category(tmp_path):
    backend = LocalStorage(tmp_path)
    backend.put("shiftlog/attachment", b"shift")
    (tmp_path / "readings").symlink_to(tmp_path / "shiftlog", target_is_directory=True)
    with pytest.raises(InvalidStorageKey, match="outside its category"):
        backend.open("readings/attachment", category="readings")
    assert list(backend.files("readings")) == []
