"""Measure mixed authenticated HTTP workflows against a dedicated synthetic instance."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from itertools import count
import json
import ssl
from pathlib import Path
import time
from uuid import uuid4

import httpx

READS = (
    ("equipment", "/api/equipment?page=1&page_size=50"),
    ("search", "/api/equipment?q=E-02500&page=1&page_size=50"),
    ("readings", "/api/readings?equipment_id=eq_lt_02500&parameter=vibration&from=2026-10-01&to=2026-10-31&limit=200"),
    ("events", "/api/events/out-of-range?limit=50"),
    ("work_orders", "/api/work-orders?page=1&page_size=50"),
)


def summarize(observations, duration):
    times = sorted(elapsed for elapsed, _ in observations)
    statuses = Counter(status for _, status in observations)

    def percentile(q):
        return round(times[min(int(len(times) * q), len(times) - 1)], 2) if times else None

    failures = sum(value for status, value in statuses.items() if status not in {"200", "201"})
    return {
        "p50_ms": percentile(.50), "p95_ms": percentile(.95), "p99_ms": percentile(.99),
        "requests": len(times), "requests_per_second": round(len(times) / duration, 2),
        "errors": failures, "statuses": dict(statuses),
    }


async def run(url, cert, tokens, users, seconds, output):
    if len(tokens) < users:
        raise ValueError("Supply a distinct test credential for each simulated user")
    labels = [label for label, _ in READS] + ["ingest"]
    observations = {name: [] for name in labels}
    counter = count()
    run_id = uuid4().hex[:12]
    verify = ssl.create_default_context(cafile=str(cert))
    limits = httpx.Limits(max_connections=users, max_keepalive_connections=users)
    async with httpx.AsyncClient(base_url=url, verify=verify, trust_env=False, limits=limits, timeout=20) as client:
        started = time.monotonic()

        async def user(number):
            iteration = number
            headers = {"X-API-Key": tokens[number]}
            while time.monotonic() - started < seconds:
                slot = iteration % 6
                iteration += 1
                send = time.monotonic()
                name = labels[slot]
                try:
                    if name == "ingest":
                        response = await client.post("/api/readings", headers=headers, json={
                            "id": f"qual_{run_id}_{next(counter)}", "equipment_id": "eq_lt_02500",
                            "parameter": "vibration", "value": 3.5, "ts": "2026-11-25T10:00:00Z",
                        })
                        status = str(response.status_code)
                        if status == "201" and response.json().get("added") != 1:
                            status = "unexpected_ingest_result"
                    else:
                        response = await client.get(READS[slot][1], headers=headers)
                        status = str(response.status_code)
                except (httpx.HTTPError, ValueError) as error:
                    status = type(error).__name__
                observations[name].append(((time.monotonic() - send) * 1000, status))

        await asyncio.gather(*(user(number) for number in range(users)))
        duration = time.monotonic() - started
    result = {
        "users": users, "duration_s": round(duration, 2), "reading_id_prefix": f"qual_{run_id}_",
        "endpoints": {name: summarize(rows, duration) for name, rows in observations.items()},
    }
    output.write_text(json.dumps(result, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--users", type=int, choices=(10, 50, 100), required=True)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error("Duration must be positive")
    tokens = [line for line in args.token_file.read_text().splitlines() if line]
    if len(tokens) != len(set(tokens)):
        parser.error("Credentials must be distinct")
    asyncio.run(run(args.url, args.certificate, tokens, args.users, args.seconds, args.output))


if __name__ == "__main__":
    main()
