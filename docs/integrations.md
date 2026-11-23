# Pointing other tools at Logbook v0.3

All examples assume the server is at `http://127.0.0.1:8787`. Add `X-API-Key` if you enabled one. Demo limits and tag names below are fictional.

## Grafana

Two workable paths:

### A. SQL data source (simplest for trends)

1. SQLite: install a Grafana SQLite plugin (for example *frser-sqlite-datasource*) and point it at `data/logbook.db`.
2. PostgreSQL: use Grafana’s built-in Postgres data source and the same connection string as `LOGBOOK_DATABASE_URL`.

Example panel query (vibration for P-101):

```sql
SELECT
  r.ts AS time,
  r.value AS vibration,
  r.limit_max AS "max limit"
FROM readings r
JOIN equipment e ON e.id = r.equipment_id
WHERE e.tag = 'P-101'
  AND r.parameter_key = 'vibration'
  AND r.ts BETWEEN '$__fromTime' AND '$__toTime'
ORDER BY r.ts;
```

Unacknowledged out-of-range table:

```sql
SELECT r.ts, e.tag, r.parameter_key, r.value, r.limit_min, r.limit_max
FROM readings r
JOIN equipment e ON e.id = r.equipment_id
LEFT JOIN annotations a ON a.reading_id = r.id
WHERE (
        (r.limit_min IS NOT NULL AND r.value < r.limit_min)
     OR (r.limit_max IS NOT NULL AND r.value > r.limit_max)
      )
  AND a.id IS NULL
ORDER BY r.ts DESC;
```

### B. JSON API data source

Use the Grafana *Infinity* (or *SimpleJson* / *JSON API*) plugin:

- URL: `http://127.0.0.1:8787/api/readings?tag=P-101&parameter=vibration&from=${__from:date:iso}&to=${__to:date:iso}`
- Rows at `readings`, time column `ts`, value column `value`.

A Grafana alert can also call `GET /api/events/out-of-range?unacknowledged=true`.

## Node-RED

A minimal flow (also saved as `examples/nodered_flow.json`):

- **mqtt in** (or **inject**) → **function** that builds `{ tag, parameter, value, ts }` → **http request** `POST http://127.0.0.1:8787/api/ingest/readings` with `Content-Type: application/json`.
- The other direction: **http in** `/hook` to receive Logbook’s out-of-range webhook, or a periodic **http request** to `GET /api/events/out-of-range?unacknowledged=true`.

Function node example:

```javascript
msg.headers = { "Content-Type": "application/json" };
msg.payload = {
  tag: msg.payload.tag || "P-101",
  parameter: msg.payload.parameter || "flow",
  value: Number(msg.payload.value),
  ts: new Date().toISOString()
};
return msg;
```

## Python script

See `examples/python_client.py`. Short version:

```python
import httpx
base = "http://127.0.0.1:8787/api"
httpx.post(f"{base}/readings", json={
    "tag": "P-101", "parameter": "flow", "value": 52.0, "ts": "2026-11-23T08:30:00",
})
rows = httpx.get(f"{base}/readings", params={"tag": "P-101", "parameter": "flow"}).json()
print(rows["count"], "points")
```

## AI agent calling the API

Give the agent the OpenAPI document at `http://127.0.0.1:8787/openapi.json` (or this checklist) and a rule: **read first, write only with the user’s confirmation, never invent process limits**. A starter prompt is in `examples/ai_agent_prompt.md`.

Useful tools for an agent:

- `GET /api/bootstrap` — what equipment, templates, and recent logs exist.
- `GET /api/readings?tag=&parameter=&from=&to=` — trend for a question like “how has P-101 vibration looked this week?”
- `GET /api/events/out-of-range?unacknowledged=true` — what still needs a note.
- `PUT /api/logs/{entry_id}/annotations/{parameter}` — add a cause / action / initials after the user reviews it.
- `POST /api/readings` — only when the user is handing the agent a value to file.

The agent should treat this as an operations notebook, not as a control system.
