"""Local attachment storage and compatibility with persisted attachment keys."""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile
from typing import BinaryIO, Iterator, Protocol

from logentra_server.config import DEFAULT_ATTACHMENTS_DIR, ROOT


class InvalidStorageKey(ValueError):
    """An attachment key is invalid or resolves outside its allowed directory."""


class Storage(Protocol):
    def safe_key(self, key: str, *, category: str | None = None) -> Path: ...
    def put(self, key: str, data: bytes, *, category: str | None = None, durable: bool = False) -> None: ...
    def open(self, key: str, *, category: str | None = None) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str, *, category: str | None = None) -> bool: ...
    def size(self, key: str) -> int: ...
    def files(self, category: str, *, include_symlinks: bool = False) -> Iterator[str]: ...


class LocalStorage:
    def __init__(self, attachments_dir: Path):
        self.root = (ROOT / attachments_dir).resolve()

    def initialize(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if not os.access(self.root, os.R_OK | os.W_OK | os.X_OK):
                raise PermissionError("Directory must be readable, writable and searchable")
        except OSError as exc:
            raise ValueError(f"Unusable attachment directory {self.root}: {exc}") from exc

    def _relative_key(self, key: str) -> Path:
        # Older document and work-order rows may have native Windows separators.
        windows_key = PureWindowsPath(key)
        relative = Path(*windows_key.parts)
        if windows_key.anchor or not relative.parts or ".." in relative.parts or ":" in key or "\0" in key:
            raise InvalidStorageKey("Invalid attachment key")
        if relative.is_relative_to(DEFAULT_ATTACHMENTS_DIR):
            relative = relative.relative_to(DEFAULT_ATTACHMENTS_DIR)
            if not relative.parts:
                raise InvalidStorageKey("Invalid attachment key")
        return relative

    def safe_key(self, key: str, *, category: str | None = None) -> Path:
        relative = self._relative_key(key)
        try:
            path = (self.root / relative).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise InvalidStorageKey("Cannot resolve attachment key") from exc
        if path == self.root or not path.is_relative_to(self.root):
            raise InvalidStorageKey("Attachment key escapes the attachment directory")
        if category is not None and (
            relative.parts[0] != category or not path.is_relative_to(self.root / category)
        ):
            raise InvalidStorageKey("Attachment key is outside its category")
        return path

    def put(self, key: str, data: bytes, *, category: str | None = None, durable: bool = False) -> None:
        path = self.safe_key(key, category=category)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not durable:
            handle = path.open("xb")
            try:
                with handle:
                    handle.write(data)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return

        staging = None
        try:
            with NamedTemporaryFile(dir=path.parent, prefix=".upload-", delete=False) as handle:
                staging = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(staging, path)
            if os.name == "posix":
                for directory in (path.parent, path.parent.parent):
                    descriptor = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)

    def open(self, key: str, *, category: str | None = None) -> BinaryIO:
        return self.safe_key(key, category=category).open("rb")

    def delete(self, key: str) -> None:
        self.safe_key(key)
        # Remove the keyed directory entry even if it is an in-tree symlink.
        (self.root / self._relative_key(key)).unlink(missing_ok=True)

    def exists(self, key: str, *, category: str | None = None) -> bool:
        return self.safe_key(key, category=category).is_file()

    def size(self, key: str) -> int:
        return self.safe_key(key).stat().st_size

    def files(self, category: str, *, include_symlinks: bool = False) -> Iterator[str]:
        try:
            directory = self.safe_key(category, category=category)
        except InvalidStorageKey:
            return
        for path in directory.rglob("*"):
            if not include_symlinks and path.is_symlink():
                continue
            key = path.relative_to(self.root).as_posix()
            try:
                resolved = self.safe_key(key, category=category)
            except InvalidStorageKey:
                continue
            if resolved.is_file():
                yield key
