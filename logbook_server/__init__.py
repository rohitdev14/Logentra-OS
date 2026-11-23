"""Deprecated import alias for `logentra_server`.

Prefer `import logentra_server`. This shim is kept so existing scripts and
docs that still say `logbook_server` continue to work.
"""
from __future__ import annotations

import importlib
import sys

import logentra_server

for _name in (
    "app",
    "auth",
    "branding",
    "config",
    "database",
    "demo",
    "documents",
    "events",
    "importers",
    "migrate",
    "models",
    "mqtt_worker",
    "pagination",
    "schemas",
    "seed",
    "service",
    "utils",
    "workorders",
    "routers",
    "routers.auth_users",
    "routers.documents",
    "routers.work_orders",
):
    _mod = importlib.import_module(f"logentra_server.{_name}")
    sys.modules[f"logbook_server.{_name}"] = _mod
    _parent, _, _child = _name.rpartition(".")
    if not _parent:
        globals()[_name] = _mod
    else:
        setattr(sys.modules[f"logbook_server.{_parent}"], _child, _mod)

from logentra_server import *  # noqa: E402,F401,F403
from logentra_server.branding import APP_VERSION as __version__  # noqa: E402,F401
from logentra_server.branding import PRODUCT_NAME, PRODUCT_SHORT, RELEASE_LABEL  # noqa: E402,F401

__all__ = list(getattr(logentra_server, "__all__", []))
