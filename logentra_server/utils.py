from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from logentra_server.sitetime import UTC, local_to_utc_text, today_local, utc_iso_z


def uid(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(
    date: str | None = None, time: str | None = None, ts: str | None = None, *, zone: ZoneInfo = UTC,
) -> str:
    """Store naive UTC text as YYYY-MM-DDTHH:MM:SS.

    Explicit timestamps represent instants, with UTC implied when naive.
    Date and time fields represent site-local wall time. Missing fields use
    local midnight or the local date, and completely missing input uses UTC now.
    """
    if ts:
        return utc_iso_z(str(ts).strip()).removesuffix("Z")
    date, time = (date or "").strip(), (time or "").strip()
    if date or time:
        return local_to_utc_text(date or today_local(zone), time or "00:00", zone)
    return utcnow_iso().removesuffix("Z")


def dumps(obj: Any) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False)


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def as_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def out_of_range(value: float | None, min_value: float | None, max_value: float | None) -> bool:
    if value is None:
        return False
    if min_value is not None and value < min_value:
        return True
    if max_value is not None and value > max_value:
        return True
    return False
