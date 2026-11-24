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



DEFAULT_APP_UID = 10001
DEFAULT_APP_GID = 10001


def parse_owner(spec: str | None) -> tuple[int, int] | None:
    """Parse UID, UID:GID, or name[:name] into numeric ids."""
    if not spec or not str(spec).strip():
        return None
    value = str(spec).strip()
    if ":" in value:
        user_part, group_part = value.split(":", 1)
    else:
        user_part, group_part = value, value
    def _id(part: str, *, group: bool) -> int:
        part = part.strip()
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            return int(part)
        import grp
        import pwd
        return (grp.getgrnam(part).gr_gid if group else pwd.getpwnam(part).pw_uid)
    return _id(user_part, group=False), _id(group_part, group=True)


def default_owner_spec() -> str | None:
    env = os.environ.get("LOGENTRA_ATTACHMENTS_OWNER") or os.environ.get("LOGENTRA_APP_OWNER")
    if env:
        return env
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return f"{DEFAULT_APP_UID}:{DEFAULT_APP_GID}"
    return None


def apply_attachment_ownership(root: Path, owner: str | None = None) -> dict:
    """Chown/chmod a restored attachment tree so the application UID can read it.

    Directories become 0o755 and files 0o644, or 0o750/0o640 after a successful
    chown onto a dedicated owner. When *owner* is omitted, LOGENTRA_ATTACHMENTS_OWNER
    is used, then 10001:10001 if this process is root. A non-root restore still
    makes files world-readable so a later app process can open them. An explicit
    --owner that cannot be applied raises PermissionError.
    """
    check_tree(root)
    requested = owner if owner is not None else default_owner_spec()
    target = parse_owner(requested) if requested else None
    chowned = False
    if target is not None:
        uid, gid = target
        try:
            for path in [root, *sorted(root.rglob("*"))]:
                os.chown(path, uid, gid)
            chowned = True
        except PermissionError:
            if owner is not None:
                raise
            chowned = False
    dir_mode = 0o750 if chowned else 0o755
    file_mode = 0o640 if chowned else 0o644
    for path in [root, *sorted(root.rglob("*"))]:
        path.chmod(dir_mode if path.is_dir() else file_mode)
    return {"owner": f"{target[0]}:{target[1]}" if chowned and target else None, "chowned": chowned}


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
    if destination.resolve().is_relative_to(attachments.resolve()):
        raise ValueError("Backup directory must be outside the attachment tree")
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


def restore(source: Path, database_url: str, attachments: Path, owner: str | None = None) -> None:
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
        apply_attachment_ownership(attachments, owner=owner)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--attachments", type=Path, default=Path("data/attachments"))
    parser.add_argument(
        "--owner",
        default=None,
        help="UID, UID:GID, or name[:name] for restored attachments. "
             "Defaults to LOGENTRA_ATTACHMENTS_OWNER, or 10001:10001 when run as root.",
    )
    args = parser.parse_args()
    database_url = os.environ.get("LOGENTRA_DATABASE_URL")
    if not database_url:
        parser.error("Set LOGENTRA_DATABASE_URL explicitly")
    if args.operation == "backup":
        backup(database_url, args.attachments, args.bundle)
    else:
        restore(args.bundle, database_url, args.attachments, owner=args.owner)


if __name__ == "__main__":
    main()
