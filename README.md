# Logentra OS

[![Logentra OS](docs/assets/logentra-os-lockup-light.svg)](docs/assets/README.md)

The logbook that stays yours.

[![Tests](https://github.com/rohitdev14/Logentra-OS/actions/workflows/tests.yml/badge.svg)](https://github.com/rohitdev14/Logentra-OS/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[Quick start](#quick-start) · [Roadmap](docs/ROADMAP.md) · [Changelog](CHANGELOG.md)

**v0.8.0.** Open-source logbook, engineering records & traceability for industrial plants — an on-prem equipment, records, work-order, and document-approval app with a local database and REST API.

Built first for small and medium plants, and for the vendors and engineers who support them.

**Logentra OS** is **Log**book + **Eng**ineering + **Tra**ceability. Use it to move commissioning checks, inspections, and daily readings out of spreadsheets and paper books. Define your own equipment columns, record templates, and log parameters. IoT gateways can write readings, and tools such as Grafana, Node-RED, scripts, and AI agents can read them. The app includes users, roles, work orders, document approvals, and light and dark themes.

It runs on your machine. There is no hosted account.

## Mission & Objectives

**Mission:** Stop losing plant knowledge to shift changes and spreadsheets. One open database, built for the people on the floor.

**Objectives**
1. Digitise paper and spreadsheet records for vendors, freelance engineers and small to medium plants.
2. Put all of a site's records in one shared database the owner controls.
3. Keep it free, open source and easy to self-host.
4. Open the data to AI: vector search across logs, handovers and commissioning records, plus exports and an API for analytics tools (planned).
5. Build a community around Logentra OS: answer questions, help plants get set up, and keep improving it based on what users actually need.

![Dashboard](docs/images/dashboard-light.png)

![Dashboard (dark)](docs/images/dashboard-dark.png)

## Features

- **Equipment QR tags** with A4 label sheets and phone quick entry for readings, notes, and photos
- **Shift log and handover** with crew rosters, append-only entries, versioned sign-off, acknowledgement, return, and escalation

- **Equipment register** with user-defined columns (text, number, date, dropdown) and spreadsheet-like grid editing
- **Record templates** you design yourself (commissioning, inspection, maintenance, or anything else): custom rows and result columns, instantiate per asset, add punches, submit for approval, and print with the audit trail
- **Log book and trends** with optional min/max limits, annotations (cause, action, initials, time), and unacknowledged out-of-range flags
- **Local database** — SQLite by default (a single file, zero setup); PostgreSQL via a connection string
- **Work orders** with a Kanban board and a status workflow (open → in progress → on hold → done → closed), assignment, due dates, comments, labour notes, attachment metadata, and optional auto-create from out-of-range readings
- **Document approvals** — upload a test sheet, report, or photos against equipment, a record, or a work order; route it through admin-defined sequential approvers (role or named user); approve / reject / return for changes with a comment; resubmit as a new version; lock the approved file; full audit trail
- **Light and dark themes** (indigo light, industrial cyan/amber dark), toggled in the top bar and remembered in the browser; the default follows the OS color scheme
- **Users, roles, and permissions** — built-in admin / supervisor / technician / viewer roles, custom roles from a permission list, optional site/area scope, session login, and API keys bound to a user. A dev / single-user mode (the default) still runs with no login.
- **REST API** with OpenAPI / Swagger docs, optional API key or user tokens, bulk ingest, time-range queries, server-side pagination, and CSV/JSON export
- **Inputs:** the UI, CSV/Excel import, a simple IoT webhook, and an optional MQTT subscriber
- **Outputs:** REST and CSV, direct SQL (for Grafana), and outbound webhooks on out-of-range events, work-order changes, and record and document approval events
- **Optional starter packs** (generic plant equipment, plus a generic semiconductor-utilities pack) — not loaded unless you choose them
- **v0.2 offline app** included at [`standalone/Logbook.html`](standalone/Logbook.html) for a no-server, browser-only workflow; a v0.2 JSON backup can be imported into the database-backed app

![Trends](docs/images/trends-light.png)

![Work orders](docs/images/work-orders-light.png)

![Approvals](docs/images/approvals-light.png)

[QR phone entry](docs/v0.7/images/qr-phone-entry.png) · [Print QR tags](docs/v0.7/images/qr-print-tags.png) · [Outgoing shift log](docs/v0.7/images/handover-outgoing.png) · [Incoming handover](docs/v0.7/images/handover-incoming.png)

## Quick start

Python 3.11+ recommended.

```bash
git clone https://github.com/rohitdev14/Logentra-OS.git
cd Logentra-OS
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python run.py                     # opens http://127.0.0.1:8787/  — API docs at /docs
```

One command after install: `python run.py`. Use `--no-browser` in scripts or CI.

Default database: `data/logentra.db` (an existing `data/logbook.db` is still used if you have not set a URL). To use PostgreSQL:

```bash
pip install "psycopg[binary]"
export LOGENTRA_DATABASE_URL="postgresql+psycopg://logentra:logentra@127.0.0.1:5432/logentra"
python run.py
```

Copy `config.example.yaml` to `config.yaml` to set host, port, auth mode, an optional API key, MQTT, or startup webhooks. `LOGENTRA_*` environment variables override the file. The older `LOGBOOK_*` names are still accepted as a deprecated fallback. `config.yaml` is gitignored so secrets stay local.

The default is single-user / open mode (no login). To require accounts:

```bash
export LOGENTRA_AUTH_MODE=users
export LOGENTRA_BOOTSTRAP_ADMIN_USER=admin
export LOGENTRA_BOOTSTRAP_ADMIN_PASSWORD='choose-a-strong-password'
python run.py
# or, after start: python run.py create-admin --username admin --password '...'
```

The display name lives in [`logentra_server/branding.py`](logentra_server/branding.py). The Python package is `logentra_server` (a `logbook_server` import shim is kept for compatibility).

The offline single-file app needs no install: open `standalone/Logbook.html` in a browser.

## Try the sample backup

A small fictional v0.2 JSON backup lives at [`examples/sample-backup/logbook-backup.json`](examples/sample-backup/logbook-backup.json). It has one site (Cedar Creek Pump Station), two equipment items (booster pump P-210 and contact tank T-210), a handful of readings, and exactly one out-of-range point: P-210 vibration at 6.8 mm/s on 12 March 2026, with an operator note already attached. The plant, tags, and people are fictional.

The default import **replaces** catalog and readings in the running instance. Try this on a fresh `python run.py` database, or pass `?replace=false` if you want to merge.

From the UI, open [http://127.0.0.1:8787/](http://127.0.0.1:8787/), go to **Backup**, choose **Import JSON…**, and select `examples/sample-backup/logbook-backup.json`. Leave **Replace all data in this browser** selected on an empty instance.

From the API:

```bash
curl -s -X POST http://127.0.0.1:8787/api/migrate/v2 \
  -H 'Content-Type: application/json' \
  --data @examples/sample-backup/logbook-backup.json
```

Then open **Trends**, choose equipment **P-210** and parameter **Vibration**. You should see four daily points from 10–13 March 2026. The 12 March 08:00 point sits above the 4.5 mm/s max line, is flagged out of range, and shows the operator note (cause, action, initials, and time) in the annotations table under the chart.

## Deployment

See the [security policy](SECURITY.md) for deployment scope and private vulnerability reporting.

**Upgrading the HTTPS profile.** Set `LOGENTRA_PUBLIC_HOST` in the Compose environment or `.env` before upgrading with `compose.production.yaml`. List the hostnames or IPs users open, separated by spaces and without ports. See the [HTTPS upgrade instructions](docs/v0.7/OPERATIONS.md#upgrading-the-https-profile).

- [0.7 operations and upgrade guide](docs/v0.7/OPERATIONS.md#upgrade)
- [Backup and recovery guide](docs/v0.6/RECOVERY_GUIDE.md)

## Architecture

```
┌─────────────┐  HTTP   ┌──────────────────┐  SQL    ┌─────────────────────┐
│ Browser UI  │────────▶│ FastAPI (Python) │────────▶│ SQLite (default) or │
│ (Logentra OS)│◀────────│  REST + OpenAPI  │◀────────│ PostgreSQL          │
└─────────────┘         └────────┬─────────┘         └─────────────────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            ▼                    ▼                    ▼
     MQTT subscriber      POST /api/ingest/*    GET /api/readings
     (optional broker)    IoT / gateway hook    Grafana / agent / CSV
                                 │
                                 ▼
                      Outbound webhooks on
                      out-of-range, work-order,
                      and document events
```

User-defined equipment columns are stored as field definitions plus values (not a hard-coded table shape). Time-series facts are one row per parameter in `readings`, indexed on `(equipment_id, parameter_key, ts)`. A shared `entry_id` groups several parameters taken at the same moment. The portable schema is in [`docs/schema.sql`](docs/schema.sql).

```mermaid
erDiagram
    FIELD_DEFINITIONS ||--o{ EQUIPMENT_FIELD_VALUES : defines
    EQUIPMENT ||--o{ EQUIPMENT_FIELD_VALUES : has
    EQUIPMENT ||--o{ PARAMETERS : has
    EQUIPMENT ||--o{ READINGS : has
    EQUIPMENT ||--o{ RECORDS : subject_of
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
    EQUIPMENT ||--o{ WORK_ORDERS : subject_of
    USERS ||--o{ WORK_ORDERS : assigned
    ROLES ||--o{ USERS : grants
    USERS ||--o{ SESSION_TOKENS : has
    USERS ||--o{ API_KEYS : has
    WORK_ORDERS ||--o{ WORK_ORDER_COMMENTS : has
    WORK_ORDERS ||--o{ WORK_ORDER_LABOUR : has
    WORK_ORDERS ||--o{ WORK_ORDER_ATTACHMENTS : has
    SITES ||--o{ AREAS : has
    SITES ||--o{ EQUIPMENT : locates
    EQUIPMENT ||--o{ DOCUMENTS : subject_of
    WORK_ORDERS ||--o{ DOCUMENTS : linked
    RECORDS ||--o{ DOCUMENTS : linked
    APPROVAL_ROUTES ||--o{ APPROVAL_ROUTE_STEPS : has
    APPROVAL_ROUTES ||--o{ DOCUMENTS : used_by
    DOCUMENTS ||--o{ DOCUMENT_VERSIONS : has
    DOCUMENTS ||--o{ DOCUMENT_APPROVAL_STEPS : has
    DOCUMENTS ||--o{ DOCUMENT_COMMENTS : has
    OUTBOUND_WEBHOOKS ||--o{ WEBHOOK_DELIVERIES : logs
    PARAM_PRESETS }o--o{ EQUIPMENT : "defaults by category"

    EQUIPMENT {
        text id PK
        text tag UK
    }
    READINGS {
        text id PK
        text entry_id
        text equipment_id FK
        text parameter_key
        text ts
        real value
        text source
    }
    ANNOTATIONS {
        text id PK
        text reading_id UK,FK
        text cause
        text action
    }
```

## API and integrations

Interactive docs when the server is running: [http://127.0.0.1:8787/docs](http://127.0.0.1:8787/docs).

Use `GET /api/records?status=submitted` to find records awaiting review, `GET /api/records/{id}` to open one, and `GET /api/records/{id}/audit` to read its audit trail. The `POST /api/records/{id}/submit`, `/approve` and `/return` endpoints take the current record `revision`. Return requires a `comment`. Use `POST /api/sites` or `PATCH /api/sites/{id}` to set a site's timezone. See the [0.7 operations guide](docs/v0.7/OPERATIONS.md).

![API documentation](docs/images/api-docs.png)

```bash
curl -s http://127.0.0.1:8787/api/health
curl -s -X POST http://127.0.0.1:8787/api/demo
curl -s "http://127.0.0.1:8787/api/readings?tag=P-101&parameter=vibration&from=2026-11-01&to=2026-11-23"
curl -s "http://127.0.0.1:8787/api/events/out-of-range?unacknowledged=true"
curl -s -X POST http://127.0.0.1:8787/api/ingest/readings \
  -H 'Content-Type: application/json' \
  -d '{"tag":"P-101","parameter":"flow","value":52.1,"ts":"2026-11-23T08:30:00Z"}'

# Work orders (also in docs/integrations.md)
curl -s -X POST http://127.0.0.1:8787/api/work-orders \
  -H 'Content-Type: application/json' \
  -d '{"title":"Replace seal","equipment_id":"eq_p101","priority":"high"}'
curl -s "http://127.0.0.1:8787/api/work-orders?status=open&page=1&page_size=50"

# Document approvals (also in docs/integrations.md)
curl -s -X POST http://127.0.0.1:8787/api/documents \
  -F 'file=@test-sheet.pdf;type=application/pdf' \
  -F 'title=P-101 commissioning test sheet' \
  -F 'document_type=commissioning_test_sheet' \
  -F 'equipment_id=eq_p101' \
  -F 'submit=true'
curl -s http://127.0.0.1:8787/api/documents/queue
curl -s -X POST http://127.0.0.1:8787/api/documents/DOC_ID/approve -H 'Content-Type: application/json' -d '{"comment":"Limits checked"}'
```

![Login](docs/images/login.png)

![Users and roles](docs/images/users.png)

Demo tags such as P-101 and the “Northbrook Works” sample plant are fictional.

Full setup, schema, and route list: **[docs/README.md](docs/README.md)**.  
Grafana, Node-RED, a Python client, and an AI-agent prompt: **[docs/integrations.md](docs/integrations.md)**.  
Copy-paste examples also live in [`examples/`](examples/), including a [sample backup](examples/sample-backup/logbook-backup.json) you can import to try Trends.  
Desktop packaging notes (PyInstaller, Tauri sidecar, Docker): **[docs/packaging.md](docs/packaging.md)**.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/test_api.py tests/test_ingest.py tests/test_oor.py tests/test_auth.py tests/test_workorders.py tests/test_pagination.py tests/test_migrations.py tests/test_sample_backup.py -q
```

The optional Playwright UI smoke test (`tests/test_ui.py`) needs a local Chrome/Chromium and is not run in the default GitHub Actions workflow.

## Roadmap

Where it's going: see the [roadmap](docs/ROADMAP.md).

This is an on-prem operations notebook and lightweight CMMS, not a certified process historian or a safety-rated control system. Hash user passwords; do not store other secrets or confidential site data in equipment records.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests are welcome at [github.com/rohitdev14/Logentra-OS/issues](https://github.com/rohitdev14/Logentra-OS/issues).

## License

[MIT](LICENSE). Copyright (c) 2026 Rohit Khurana.
