#!/usr/bin/env python3
"""Seed a large catalog and measure p50/p95 latency of key list/query endpoints.

Example:
    python scripts/load_test.py --equipment 5000 --readings-per 100 --work-orders 2000
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logbook_server.config import Settings
from logbook_server.database import init_db, make_engine, make_session_factory
from logbook_server.utils import utcnow_iso


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def seed(conn, n_eq: int, n_rd: int, n_wo: int) -> dict:
    now = utcnow_iso()
    # idempotent-ish: skip if already at scale
    existing = conn.execute("SELECT COUNT(*) FROM equipment").fetchone()[0]
    if existing >= n_eq:
        print(f"Database already has {existing} equipment — skipping seed.")
        return {"equipment": existing, "skipped": True}

    print(f"Seeding {n_eq} equipment…")
    fields = list(conn.execute("SELECT id, key FROM field_definitions"))
    if not fields:
        conn.execute(
            "INSERT INTO field_definitions (id, key, label, type, options, locked, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("fld_tag", "tag", "Tag", "text", "[]", 1, 0, now, now),
        )
        conn.execute(
            "INSERT INTO field_definitions (id, key, label, type, options, locked, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("fld_category", "category", "Category", "text", "[]", 0, 1, now, now),
        )
        conn.commit()
        fields = list(conn.execute("SELECT id, key FROM field_definitions"))
    tag_id = next(f[0] for f in fields if f[1] == "tag")
    cat_id = next((f[0] for f in fields if f[1] == "category"), None)

    eq_rows, val_rows, param_rows = [], [], []
    for i in range(n_eq):
        eid = f"eq_lt_{i:05d}"
        tag = f"E-{i:05d}"
        eq_rows.append((eid, tag, now, now))
        val_rows.append((eid, tag_id, tag))
        if cat_id:
            val_rows.append((eid, cat_id, "Pump" if i % 2 == 0 else "Motor"))
        param_rows.append((f"pr_lt_{i:05d}", eid, "vibration", "Vibration", "mm/s", None, 4.5, 0))
    conn.executemany("INSERT INTO equipment (id, tag, created_at, updated_at) VALUES (?,?,?,?)", eq_rows)
    conn.executemany("INSERT INTO equipment_field_values (equipment_id, field_id, value) VALUES (?,?,?)", val_rows)
    conn.executemany(
        "INSERT INTO parameters (id, equipment_id, key, label, unit, min_value, max_value, sort_order) VALUES (?,?,?,?,?,?,?,?)",
        param_rows,
    )
    conn.commit()
    print(f"  equipment+params done ({n_eq})")

    if n_rd:
        print(f"Seeding {n_eq * n_rd:,} readings ({n_rd} per equipment)…")
        batch: list[tuple] = []
        added = 0
        # Space readings over 100 hours ending 'now-like' so a trend window is realistic
        for i in range(n_eq):
            eid = f"eq_lt_{i:05d}"
            for k in range(n_rd):
                hour = k  # 0..n_rd-1
                day = hour // 24
                hr = hour % 24
                ts = f"2026-10-{(1 + day):02d}T{hr:02d}:00:00"
                # ~1% out of range
                oor = 1 if (i + k) % 100 == 0 else 0
                value = 6.0 if oor else 2.0 + (k % 20) * 0.05
                rid = f"rd_lt_{i:05d}_{k:03d}"
                entry = f"log_lt_{i:05d}_{k:03d}"
                batch.append((rid, entry, eid, "vibration", ts, value, "load", "", now, 4.5, oor))
                if len(batch) >= 10000:
                    conn.executemany(
                        "INSERT INTO readings (id, entry_id, equipment_id, parameter_key, ts, value, source, entered_by, created_at, limit_max, out_of_range) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        batch,
                    )
                    conn.commit()
                    added += len(batch)
                    batch = []
                    if added % 100000 == 0:
                        print(f"  {added:,} readings")
        if batch:
            conn.executemany(
                "INSERT INTO readings (id, entry_id, equipment_id, parameter_key, ts, value, source, entered_by, created_at, limit_max, out_of_range) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                batch,
            )
            conn.commit()
            added += len(batch)
        print(f"  readings done ({added:,})")

    if n_wo:
        print(f"Seeding {n_wo} work orders…")
        rows = []
        statuses = ["open", "in_progress", "on_hold", "done", "closed"]
        prios = ["low", "medium", "high", "urgent"]
        for i in range(n_wo):
            eid = f"eq_lt_{i % n_eq:05d}"
            rows.append((
                f"wo_lt_{i:05d}",
                f"Job {i:05d}",
                "Load-test work order",
                statuses[i % 5],
                prios[i % 4],
                eid,
                now,
                now,
                "load",
            ))
        conn.executemany(
            "INSERT INTO work_orders (id, title, description, status, priority, equipment_id, created_at, updated_at, source) VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        print("  work orders done")
    return {"equipment": n_eq, "readings": n_eq * n_rd, "work_orders": n_wo, "skipped": False}


def measure(client, method: str, path: str, repeats: int, warmup: int = 3) -> dict:
    for _ in range(warmup):
        r = client.get(path) if method == "GET" else client.request(method, path)
        r.raise_for_status()
    times = []
    last_status = None
    last_bytes = 0
    for _ in range(repeats):
        t0 = time.perf_counter()
        r = client.get(path) if method == "GET" else client.request(method, path)
        dt = (time.perf_counter() - t0) * 1000
        last_status = r.status_code
        last_bytes = len(r.content)
        if r.status_code >= 400:
            raise RuntimeError(f"{path} -> {r.status_code}: {r.text[:200]}")
        times.append(dt)
    return {
        "path": path,
        "n": repeats,
        "p50_ms": round(_pct(times, 0.50), 2),
        "p95_ms": round(_pct(times, 0.95), 2),
        "mean_ms": round(statistics.mean(times), 2),
        "max_ms": round(max(times), 2),
        "status": last_status,
        "bytes": last_bytes,
    }


def run(url: str, n_eq: int, n_rd: int, n_wo: int, repeats: int, backend_label: str) -> dict:
    from fastapi.testclient import TestClient
    from logbook_server.app import create_app

    settings = Settings(database_url=url, open_browser=False, auth_mode="open")
    # init / migrate first so we can seed with a raw connection
    engine = make_engine(settings.sqlalchemy_url)
    init_db(engine)
    raw_path = settings.sqlalchemy_url.replace("sqlite:///", "")
    import sqlite3
    conn = sqlite3.connect(raw_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        seed(conn, n_eq, n_rd, n_wo)
    finally:
        conn.close()

    app = create_app(settings)
    results = []
    with TestClient(app) as client:
        # pick a mid-range equipment that exists after seed
        mid = f"eq_lt_{n_eq // 2:05d}"
        mid_tag = f"E-{n_eq // 2:05d}"
        endpoints = [
            ("GET", "/api/equipment?page=1&page_size=50"),
            ("GET", f"/api/equipment?q={mid_tag}&page=1&page_size=50"),
            ("GET", f"/api/readings?equipment_id={mid}&parameter=vibration&from=2026-10-01&to=2026-10-31&limit=200"),
            ("GET", "/api/events/out-of-range?limit=50"),
            ("GET", "/api/work-orders?page=1&page_size=50"),
        ]
        for method, path in endpoints:
            print(f"Measuring {path} …")
            row = measure(client, method, path, repeats=repeats)
            print(f"  p50={row['p50_ms']} ms  p95={row['p95_ms']} ms  mean={row['mean_ms']} ms")
            results.append(row)
    return {
        "backend": backend_label,
        "database_url": url,
        "seed": {"equipment": n_eq, "readings_per_equipment": n_rd, "work_orders": n_wo},
        "endpoints": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Logbook v0.4 load test")
    parser.add_argument("--equipment", type=int, default=5000)
    parser.add_argument("--readings-per", type=int, default=100)
    parser.add_argument("--work-orders", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--db", default=str(ROOT / "data" / "loadtest.db"))
    parser.add_argument("--postgres-url", default=os.environ.get("LOGBOOK_LOADTEST_POSTGRES", ""))
    parser.add_argument("--out", default=str(ROOT / "docs" / "load-test-results.json"))
    args = parser.parse_args()

    reports = []
    sqlite_url = "sqlite:///" + str(Path(args.db).resolve())
    print("=== SQLite ===")
    reports.append(run(sqlite_url, args.equipment, args.readings_per, args.work_orders, args.repeats, "sqlite"))

    if args.postgres_url:
        print("=== PostgreSQL ===")
        reports.append(run(args.postgres_url, args.equipment, args.readings_per, args.work_orders, args.repeats, "postgresql"))
    else:
        print("PostgreSQL skipped (no --postgres-url / LOGBOOK_LOADTEST_POSTGRES). The service was not running on this box.")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
