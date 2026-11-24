"""Offline backup and restore of a matching database and attachment tree."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.engine import make_url


def check_tree(root: Path) -> None:
    if root.is_symlink() or any(
        path.is_symlink() or not (path.is_file() or path.is_dir()) for path in root.rglob("*")
    ):
        raise ValueError("Recovery directories must contain only regular files and directories")


def checksums(root: Path) -> dict[str, str]:
    check_tree(root)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != root / "manifest.json":
            with path.open("rb") as handle:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(handle, "sha256").hexdigest()
    return result


def sqlite_copy(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as existing:
        with closing(sqlite3.connect(target)) as copy:
            existing.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("SQLite integrity check failed")


def pg_command(name: str, url, *options: str) -> None:
    # libpq reads the password from the subprocess environment, not its argv.
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    conn = url.set(drivername="postgresql")._replace(password=None)
    subprocess.run([name, "--dbname", conn.render_as_string(), *options], env=env, check=True)


def backup(database_url: str, attachments: Path, destination: Path) -> None:
    url = make_url(database_url)
    backend = url.get_backend_name()
    destination.mkdir(mode=0o700, parents=True)
    try:
        if backend == "sqlite":
            sqlite_copy(Path(url.database), destination / "database.sqlite3")
        elif backend == "postgresql":
            pg_command("pg_dump", url, "--format=custom", "--file", str(destination / "database.dump"))
        else:
            raise ValueError("Unsupported database backend")
        check_tree(attachments)
        if attachments.exists():
            shutil.copytree(attachments, destination / "attachments")
        else:
            (destination / "attachments").mkdir()
        manifest = {"backend": backend, "sha256": checksums(destination)}
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except BaseException:
        shutil.rmtree(destination)
        raise


def restore(source: Path, database_url: str, attachments: Path) -> None:
    check_tree(source)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest["sha256"] != checksums(source):
        raise ValueError("Backup checksum verification failed")
    url = make_url(database_url)
    backend = url.get_backend_name()
    if manifest["backend"] != backend or attachments.exists() or attachments.is_symlink():
        raise ValueError("Restore requires the original database backend and a fresh attachment path")
    target = Path(url.database) if backend == "sqlite" else None
    if target is not None and (target.exists() or target.is_symlink()):
        raise FileExistsError(target)
    attachments.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=attachments.parent) as staging:
        staged = Path(staging) / "attachments"
        shutil.copytree(source / "attachments", staged)
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb"):
                pass
            try:
                sqlite_copy(source / "database.sqlite3", target)
            except BaseException:
                target.unlink()
                raise
        elif backend == "postgresql":
            pg_command("pg_restore", url, "--single-transaction", "--no-owner", "--no-privileges",
                       str(source / "database.dump"))
        else:
            raise ValueError("Unsupported database backend")
        staged.rename(attachments)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--attachments", type=Path, default=Path("data/attachments"))
    args = parser.parse_args()
    database_url = os.environ.get("LOGENTRA_DATABASE_URL")
    if not database_url:
        parser.error("Set LOGENTRA_DATABASE_URL explicitly")
    if args.operation == "backup":
        backup(database_url, args.attachments, args.bundle)
    else:
        restore(args.bundle, database_url, args.attachments)


if __name__ == "__main__":
    main()
