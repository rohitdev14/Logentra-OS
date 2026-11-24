from __future__ import annotations

import os
from pathlib import Path
from stat import S_IMODE

import pytest

from scripts.recovery import (
    DEFAULT_APP_GID,
    DEFAULT_APP_UID,
    apply_attachment_ownership,
    backup,
    parse_owner,
    restore,
)


def test_parse_owner_numeric_and_current_user():
    assert parse_owner(None) is None
    assert parse_owner("") is None
    assert parse_owner("10001") == (10001, 10001)
    assert parse_owner("10001:10002") == (10001, 10002)
    uid = os.getuid()
    assert parse_owner(str(uid)) == (uid, uid)
    import pwd
    account = pwd.getpwuid(uid).pw_name
    parsed = parse_owner(account)
    assert parsed[0] == uid


def _write_restricted_tree(root: Path) -> Path:
    root.mkdir()
    folder = root / "documents" / "doc_1"
    folder.mkdir(parents=True)
    evidence = folder / "proof.txt"
    evidence.write_bytes(b"restored evidence")
    evidence.chmod(0o600)
    folder.chmod(0o700)
    (root / "documents").chmod(0o700)
    root.chmod(0o700)
    return evidence


def test_apply_attachment_ownership_makes_tree_readable(tmp_path):
    tree = tmp_path / "attachments"
    evidence = _write_restricted_tree(tree)
    # Explicitly apply the current user as owner so the test does not depend on root.
    result = apply_attachment_ownership(tree, owner=f"{os.getuid()}:{os.getgid()}")
    assert result["chowned"] is True
    assert S_IMODE(evidence.stat().st_mode) == 0o640
    assert S_IMODE(tree.stat().st_mode) == 0o750
    assert os.stat(evidence).st_uid == os.getuid()
    assert evidence.read_bytes() == b"restored evidence"


def test_apply_attachment_ownership_chmod_fallback_without_root(tmp_path, monkeypatch):
    tree = tmp_path / "attachments"
    evidence = _write_restricted_tree(tree)
    monkeypatch.delenv("LOGENTRA_ATTACHMENTS_OWNER", raising=False)
    monkeypatch.setattr("scripts.recovery.os.geteuid", lambda: 1000)
    result = apply_attachment_ownership(tree)
    assert result["chowned"] is False
    assert S_IMODE(evidence.stat().st_mode) == 0o644
    assert S_IMODE(tree.stat().st_mode) == 0o755
    assert os.access(evidence, os.R_OK)


def test_restore_applies_owner_to_copied_attachments(tmp_path):
    source = tmp_path / "source.db"
    attachments = tmp_path / "origin"
    _write_restricted_tree(attachments)
    # A tiny valid sqlite database for the backup API
    import sqlite3
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE t (id INTEGER)")
        db.execute("PRAGMA user_version = 1")
    bundle = tmp_path / "bundle"
    backup("sqlite:///" + str(source), attachments, bundle)
    restored = tmp_path / "restored-attachments"
    restore(bundle, "sqlite:///" + str(tmp_path / "restored.db"), restored, owner=f"{os.getuid()}:{os.getgid()}")
    evidence = restored / "documents" / "doc_1" / "proof.txt"
    assert evidence.read_bytes() == b"restored evidence"
    assert S_IMODE(evidence.stat().st_mode) == 0o640
    assert os.stat(evidence).st_uid == os.getuid()


@pytest.mark.skipif(os.geteuid() != 0, reason="chown to the app UID requires root")
def test_restore_chowns_app_uid_when_run_as_root(tmp_path):
    attachments = tmp_path / "origin"
    _write_restricted_tree(attachments)
    import sqlite3
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE t (id INTEGER)")
    bundle = tmp_path / "bundle"
    backup("sqlite:///" + str(source), attachments, bundle)
    restored = tmp_path / "restored-attachments"
    restore(bundle, "sqlite:///" + str(tmp_path / "restored.db"), restored)
    evidence = restored / "documents" / "doc_1" / "proof.txt"
    info = os.stat(evidence)
    assert info.st_uid == DEFAULT_APP_UID
    assert info.st_gid == DEFAULT_APP_GID
    assert S_IMODE(info.st_mode) == 0o640
