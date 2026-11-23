#!/usr/bin/env python3
"""Minimal example: write a reading and query a trend from Logentra OS."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

BASE = os.environ.get("LOGENTRA_URL") or os.environ.get("LOGBOOK_URL", "http://127.0.0.1:8787/api")
HEADERS = {}
key = os.environ.get("LOGENTRA_API_KEY") or os.environ.get("LOGBOOK_API_KEY")
if key:
    HEADERS["X-API-Key"] = key


def main() -> None:
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    r = httpx.post(
        f"{BASE}/readings",
        headers=HEADERS,
        json={"tag": "P-101", "parameter": "flow", "value": 52.4, "ts": ts, "entered_by": "python-example", "source": "api"},
        timeout=10,
    )
    print("ingest", r.status_code, r.text[:300])
    q = httpx.get(
        f"{BASE}/readings",
        headers=HEADERS,
        params={"tag": "P-101", "parameter": "flow", "from": "2020-01-01", "to": "2099-01-01"},
        timeout=10,
    )
    q.raise_for_status()
    data = q.json()
    print(f"{data['count']} flow readings for P-101; last 3:")
    for row in data["readings"][-3:]:
        print(f"  {row['ts']}  {row['value']}  source={row['source']}")
    ev = httpx.get(f"{BASE}/events/out-of-range", headers=HEADERS, params={"unacknowledged": "true"}, timeout=10)
    ev.raise_for_status()
    print("unacknowledged out-of-range:", ev.json()["count"])


if __name__ == "__main__":
    main()
