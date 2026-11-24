from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any


def uid(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(date: str | None = None, time: str | None = None, ts: str | None = None) -> str:
    """Normalize offset-bearing input to UTC and treat legacy naive input as UTC."""
    raw = str(ts).strip() if ts else f"{(date or '').strip() or _today()}T{(time or '').strip() or '00:00'}"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat(timespec="seconds")


def split_ts(ts: str) -> tuple[str, str]:
    s = (ts or "").replace("Z", "")
    if "T" in s:
        d, rest = s.split("T", 1)
        return d, rest[:5]
    if " " in s:
        d, rest = s.split(" ", 1)
        return d, rest[:5]
    return s[:10], "00:00"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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
