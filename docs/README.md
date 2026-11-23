# Logbook v0.3 — local database and API

v0.3 – early release. See the [root README](../README.md) for a short overview, screenshots, and contributing notes.

v0.3 keeps the v0.2 industry-neutral UI (equipment register, record templates, logs, trends, annotations, CSV/Excel import) and puts the data in an open-source database with a REST API. Other tools can both **write** into it (IoT gateways, MQTT, scripts, agents) and **read** from it (Grafana, Node-RED, Python, another logbook or CMMS).

The v0.2 offline single-file app is shipped at [`standalone/Logbook.html`](../standalone/Logbook.html).

## Quick start

```bash
cd logbook
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python run.py                     # opens http://127.0.0.1:8787/  (API docs at /docs)
```

One command after install: **`python run.py`**. Use `--no-browser` in scripts or CI.

Default database: a SQLite file at `data/logbook.db` (zero setup). To use PostgreSQL, install a driver and set a connection string:

```bash
pip install "psycopg[binary]"
export LOGBOOK_DATABASE_URL="postgresql+psycopg://logbook:logbook@127.0.0.1:5432/logbook"
python run.py
```

Or copy `config.example.yaml` to `config.yaml` and edit `database_url`. Environment variables `LOGBOOK_*` override the file (`LOGBOOK_DATABASE_URL`, `LOGBOOK_API_KEY`, `LOGBOOK_HOST`, `LOGBOOK_PORT`, `LOGBOOK_MQTT_ENABLED`, …).

Optional API key: set `api_key` in config or `LOGBOOK_API_KEY`. Every `/api` route except `/api/health` then requires `X-API-Key: <key>` or `Authorization: Bearer <key>`.

## Architecture

```
┌─────────────┐  HTTP   ┌──────────────────┐  SQL    ┌─────────────────────┐
│ Browser UI  │────────▶│ FastAPI (Python) │────────▶│ SQLite (default) or │
│ (Logbook)   │◀────────│  REST + OpenAPI  │◀────────│ PostgreSQL          │
└─────────────┘         └────────┬─────────┘         └─────────────────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            ▼                    ▼                    ▼
     MQTT subscriber      POST /api/ingest/*    GET /api/readings
     (optional broker)    IoT / gateway hook    Grafana / agent / CSV
                                 │
                                 ▼
                      Outbound webhooks on
                      out-of-range events
```

- **UI** — the v0.2 interface, served by the same process, now calling the API instead of `localStorage`.
- **API** — FastAPI, documented interactively at `/docs` (Swagger UI) and `/redoc`.
- **ORM** — SQLAlchemy 2. The same models run on SQLite and PostgreSQL.
- **Schema** — [schema.sql](schema.sql) is the portable source of truth (also created automatically on first start).

Import a v0.2 JSON backup with **Backup → Import JSON** in the UI, or `POST /api/migrate/v2`.

## Entity-relationship diagram

```mermaid
erDiagram
    FIELD_DEFINITIONS ||--o{ EQUIPMENT_FIELD_VALUES : defines
    EQUIPMENT ||--o{ EQUIPMENT_FIELD_VALUES : has
    EQUIPMENT ||--o{ PARAMETERS : has
    EQUIPMENT ||--o{ READINGS : has
    EQUIPMENT ||--o{ RECORDS : subject_of
    PARAM_PRESETS }o--|| EQUIPMENT : "defaults by category"
    TEMPLATES ||--o{ TEMPLATE_COLUMNS : has
    TEMPLATES ||--o{ TEMPLATE_ITEMS : has
    TEMPLATES ||--o{ RECORDS : instantiated_as
    RECORDS ||--o{ RECORD_COLUMNS : snapshot
    RECORDS ||--o{ RECORD_ITEMS : snapshot
    RECORD_ITEMS ||--o{ RECORD_VALUES : filled
    RECORD_COLUMNS ||--o{ RECORD_VALUES : for
    RECORDS ||--o| SIGNOFFS : signed
    RECORDS ||--o{ PUNCHES : lists
    READINGS ||--o| ANNOTATIONS : explained_by
    OUTBOUND_WEBHOOKS ||--o{ WEBHOOK_DELIVERIES : logs
    AUDIT_LOG }o--o{ EQUIPMENT : "entity_id"
    SETTINGS }o..o{ SETTINGS : kv

    FIELD_DEFINITIONS {
        text id PK
        text key UK
        text label
        text type
        text options
        int locked
    }
    EQUIPMENT {
        text id PK
        text tag UK
        text created_at
    }
    EQUIPMENT_FIELD_VALUES {
        text equipment_id PK,FK
        text field_id PK,FK
        text value
    }
    PARAMETERS {
        text id PK
        text equipment_id FK
        text key
        real min_value
        real max_value
    }
    READINGS {
        text id PK
        text entry_id
        text equipment_id FK
        text parameter_key
        text ts
        real value
        text source
        real limit_min
        real limit_max
    }
    ANNOTATIONS {
        text id PK
        text reading_id UK,FK
        text cause
        text action
        text annotated_by
    }
    TEMPLATES {
        text id PK
        text name
        text kind
    }
    RECORDS {
        text id PK
        text template_id FK
        text equipment_id FK
    }
    OUTBOUND_WEBHOOKS {
        text id PK
        text url
        text events
    }
    AUDIT_LOG {
        text id PK
        text ts
        text action
        text entity_type
        text entity_id
    }
```

User-defined equipment columns live in `field_definitions` + `equipment_field_values` (entity-attribute-value), not a hard-coded set of columns. Time-series facts are one row per parameter in `readings`, indexed on `(equipment_id, parameter_key, ts)`. A shared `entry_id` groups several parameters taken at the same moment (a log-book line). Annotations hang off a single reading. Limits in force at ingest time are snapshotted on the reading (`limit_min` / `limit_max`).

## API examples

Interactive docs: [http://127.0.0.1:8787/docs](http://127.0.0.1:8787/docs). A few copy-paste calls:

```bash
# Health
curl -s http://127.0.0.1:8787/api/health

# Load the fictional demo (generic plant)
curl -s -X POST http://127.0.0.1:8787/api/demo

# Query one parameter over a time range
curl -s "http://127.0.0.1:8787/api/readings?tag=P-101&parameter=vibration&from=2026-11-01&to=2026-11-23"

# Unacknowledged out-of-range events
curl -s "http://127.0.0.1:8787/api/events/out-of-range?unacknowledged=true"

# Bulk ingest (manual, script, or historian extract)
curl -s -X POST http://127.0.0.1:8787/api/readings/bulk \
  -H 'Content-Type: application/json' \
  -d '{"readings":[{"tag":"P-101","parameter":"flow","value":52.1,"ts":"2026-11-23T08:30:00","source":"api"}]}'

# IoT gateway webhook (same shapes: one object, a list, or { "readings": [...] })
curl -s -X POST http://127.0.0.1:8787/api/ingest/readings \
  -H 'Content-Type: application/json' \
  -d '{"tag":"P-101","parameter":"vibration","value":2.2,"ts":"2026-11-23T08:31:00"}'

# Export
curl -s -o backup.json http://127.0.0.1:8787/api/export/json
curl -s -o readings.csv http://127.0.0.1:8787/api/export/readings.csv

# Import a v0.2 JSON backup
curl -s -X POST http://127.0.0.1:8787/api/migrate/v2 \
  -H 'Content-Type: application/json' --data @../path/to/logbook-backup.json
```

Main route groups (all under `/api`):

| Group | Routes |
| --- | --- |
| System | `GET /health`, `GET /audit` |
| Catalog | `GET /bootstrap`, `PUT /sync`, `POST /demo`, `DELETE /data`, `POST /migrate/v2` |
| Equipment | `GET/POST /equipment`, `GET/PATCH/DELETE /equipment/{id}`, `GET /fields` |
| Templates & records | `GET/POST /templates`, `DELETE /templates/{id}`, `GET/POST /records`, `DELETE /records/{id}` |
| Readings | `GET/POST /readings`, `POST /readings/bulk`, `DELETE /readings/{id}`, `PUT /readings/{id}/annotation`, `GET /events/out-of-range` |
| Ingest | `POST /ingest/readings`, `POST /ingest/file` (CSV or .xlsx) |
| Export | `GET /export/json`, `GET /export/readings.csv`, `GET /export/equipment.csv` |
| Outbound hooks | `GET/POST /webhooks`, `DELETE /webhooks/{id}`, `GET /webhooks/deliveries` |

`source` on a reading is one of `manual`, `import`, `mqtt`, `webhook`, `api`, `demo`.

## Inputs (into Logbook)

- **UI** — same as v0.2: type in the grid, fill a record, enter a log line, map a CSV/Excel file.
- **REST** — `POST /api/readings`, `/api/readings/bulk`, or `/api/ingest/readings`.
- **File** — `POST /api/ingest/file?kind=logs|equipment` with a CSV or `.xlsx` upload.
- **MQTT** (optional) — set `mqtt.enabled: true` in `config.yaml`. Default topics: `logbook/readings` (JSON body) and `logbook/{tag}/{parameter}` (JSON `{ "value": 12.3, "ts": "..." }` or a bare number). The subscriber writes through the same ingest path, so out-of-range webhooks still fire.
- **v0.2 backup** — `POST /api/migrate/v2`.

## Outputs (out of Logbook)

- **REST / CSV / JSON** — as above. Good for agents, scripts, and other applications.
- **Outbound webhooks** — `POST /api/webhooks` with `{ "url": "https://…", "events": ["out_of_range"], "secret": "optional" }`. On an out-of-range ingest the server POSTs a JSON payload (`event`, `tag`, `parameter`, `value`, `min`, `max`, `ts`, `reading_id`, `acknowledged`) and stores the delivery. If `secret` is set, the request includes `X-Logbook-Signature: sha256=…`.
- **Direct SQL** — point Grafana or any SQL tool at `data/logbook.db` or at PostgreSQL. See [integrations.md](integrations.md).
- **UI trends / print** — unchanged from v0.2.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/test_api.py tests/test_ingest.py tests/test_oor.py -q
python -m pytest tests/test_ui.py -q    # optional; starts a local server, needs Chrome + Playwright
```

## Recommended packaging path (desktop installer)

This backend was written in **Python + FastAPI** so it can be frozen later without a Node runtime.

1. **Now (developers / plant PC):** `pip install -r requirements.txt && python run.py`. Keep the folder, the `data/` file, and a shortcut to `run.py`.
2. **Near-term Windows / macOS / Linux installer — PyInstaller (recommended first package):** freeze `run.py` plus `logbook_server/` and `ui/Logbook.html` into one executable that binds 127.0.0.1:8787, opens the browser, and stores `logbook.db` next to the executable (or in the user data directory). A starting spec is in [packaging.md](packaging.md). Ship the Microsoft VC++ runtime on Windows. Expect a 30–50 MB onefile build.
3. **Nicer desktop window — Tauri sidecar (second step):** a small Tauri (or pywebview) shell that starts the PyInstaller binary as a sidecar, shows `http://127.0.0.1:8787/` in a WebView, and can live in the system tray. The Python sidecar remains the source of truth for the database and API, so Grafana and MQTT keep working while the window is closed if you allow the sidecar to keep running.
4. **Always-on plant server:** Docker (or a systemd unit) with PostgreSQL, no browser auto-open, API key enabled, MQTT pointed at the site broker.

Logbook is not a certified historian or a multi-user CMMS.

## Known limits

- SQLite is an excellent single-writer local file; many concurrent ingest processes are better served by PostgreSQL.
- The xlsx ingest path (UI and API) handles simple cell values, not formulas, charts, or merged cells.
- MQTT is a single-process subscriber; there is no built-in store-and-forward if the broker is down.
- Outbound webhooks are attempted once per event (the delivery is logged). There is no retry queue yet.
- Out-of-range detection uses the limits snapshotted at ingest; changing a limit later does not rewrite history, and the events query uses those snapshots.
- The UI upserts the catalog (`PUT /api/sync`) and writes readings through the readings API. A long-open UI tab will not automatically show readings ingested by MQTT until you reload (or revisit Logs / Trends after a refresh).
- localStorage from v0.2 is not read automatically; export a JSON backup in v0.2 and import it here.
- Do not store passwords or confidential site data in the logbook.
