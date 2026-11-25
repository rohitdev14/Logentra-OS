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


def _sqlite_bundle(tmp_path: Path, marker: int = 1) -> tuple[Path, Path]:
    import sqlite3
    source = tmp_path / f"source-{marker}.db"
    attachments = tmp_path / f"origin-{marker}"
    _write_restricted_tree(attachments)
    (attachments / "documents" / "doc_1" / "proof.txt").chmod(0o600)
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE t (id INTEGER)")
        db.execute("INSERT INTO t VALUES (?)", (marker,))
    bundle = tmp_path / f"bundle-{marker}"
    backup("sqlite:///" + str(source), attachments, bundle)
    return bundle, attachments


def test_restore_refuses_existing_install_without_replace(tmp_path):
    from scripts.recovery import RecoveryError
    bundle, _ = _sqlite_bundle(tmp_path)
    live = tmp_path / "data" / "attachments"
    live.mkdir(parents=True)
    with pytest.raises(RecoveryError, match="--replace"):
        restore(bundle, "sqlite:///" + str(tmp_path / "data" / "app.db"), live)


def test_restore_replace_keeps_previous_install(tmp_path):
    import sqlite3
    bundle, _ = _sqlite_bundle(tmp_path, marker=7)
    data = tmp_path / "data"
    live = data / "attachments"
    live.mkdir(parents=True)
    (live / "old.txt").write_text("old")
    db_path = data / "app.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE t (id INTEGER)")
        db.execute("INSERT INTO t VALUES (1)")
    (data / "app.db-wal").write_bytes(b"stale wal from the old database")
    result = restore(bundle, "sqlite:///" + str(db_path), live, replace=True)
    assert (live / "documents" / "doc_1" / "proof.txt").read_bytes() == b"restored evidence"
    assert not (live / "old.txt").exists()
    kept = Path(result["kept"]["attachments"])
    assert (kept / "old.txt").read_text() == "old"
    assert Path(result["kept"]["database"]).exists()
    assert not (data / "app.db-wal").exists()
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT id FROM t").fetchall() == [(7,)]
    # No staging leftovers next to the live data.
    assert not [p for p in data.iterdir() if p.name.startswith(".")]


def test_restore_as_app_user_needs_no_chown(tmp_path, monkeypatch):
    bundle, _ = _sqlite_bundle(tmp_path)
    monkeypatch.delenv("LOGENTRA_ATTACHMENTS_OWNER", raising=False)
    monkeypatch.setattr("scripts.recovery.running_as_app_user", lambda: True)
    chowned = []
    monkeypatch.setattr("scripts.recovery.os.chown", lambda *a: chowned.append(a))
    restored = tmp_path / "restored"
    restore(bundle, "sqlite:///" + str(tmp_path / "r.db"), restored)
    evidence = restored / "documents" / "doc_1" / "proof.txt"
    assert evidence.read_bytes() == b"restored evidence"
    assert S_IMODE(evidence.stat().st_mode) == 0o640
    assert S_IMODE(restored.stat().st_mode) == 0o750


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any bundle")
def test_unreadable_bundle_gives_a_plain_message(tmp_path):
    from scripts.recovery import RecoveryError
    bundle, _ = _sqlite_bundle(tmp_path)
    (bundle / "manifest.json").chmod(0o000)
    try:
        with pytest.raises(RecoveryError, match="Cannot read the backup"):
            restore(bundle, "sqlite:///" + str(tmp_path / "r.db"), tmp_path / "restored")
    finally:
        (bundle / "manifest.json").chmod(0o600)


def test_cli_prints_message_instead_of_traceback(tmp_path):
    import subprocess
    import sys
    env = {**os.environ, "LOGENTRA_DATABASE_URL": "sqlite:///" + str(tmp_path / "x.db")}
    run = subprocess.run([sys.executable, "scripts/recovery.py", "restore", str(tmp_path / "missing")],
                         env=env, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert run.returncode == 2
    assert "No backup found" in run.stderr
    assert "Traceback" not in run.stderr
