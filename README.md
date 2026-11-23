# Logentra OS

**v0.5 – early release.** Open-source logbook, engineering records & traceability for industrial plants — an on-prem equipment, records, work-order, and document-approval app with a local database and REST API.

**Logentra OS** is **Log**book + **Eng**ineering + **Tra**ceability. It is for engineers, vendors, and small plants who still keep commissioning checks, inspections, and daily readings in spreadsheets or paper books. You define your own equipment columns, record templates, and log parameters — so it can be used in any industry. The same data is available to other tools: IoT gateways can write readings in, and Grafana, Node-RED, scripts, or an AI agent can read them out. Users, roles, work orders, and a document approval workflow are built in so a site can run it as a self-hosted alternative to a paid CMMS/Cx cloud app. The UI has a light and a dark theme.

It runs on your machine. There is no hosted account.

![Dashboard](docs/images/dashboard-light.png)

![Dashboard (dark)](docs/images/dashboard-dark.png)

## Features

- **Equipment register** with user-defined columns (text, number, date, dropdown) and spreadsheet-like grid editing
- **Record templates** you design yourself (commissioning, inspection, maintenance, or anything else): custom rows and result columns, instantiate per asset, punches, sign-off, print
- **Log book and trends** with optional min/max limits, annotations (cause, action, initials, time), and unacknowledged out-of-range flags
- **Local database** — SQLite by default (a single file, zero setup); PostgreSQL via a connection string
- **Work orders** with a Kanban board and a status workflow (open → in progress → on hold → done → closed), assignment, due dates, comments, labour notes, attachment metadata, and optional auto-create from out-of-range readings
- **Document approvals** — upload a test sheet, report, or photos against equipment, a record, or a work order; route it through admin-defined sequential approvers (role or named user); approve / reject / return for changes with a comment; resubmit as a new version; lock the approved file; full audit trail
- **Light and dark themes** (indigo light, industrial cyan/amber dark), toggled in the top bar and remembered in the browser; the default follows the OS color scheme
- **Users, roles, and permissions** — built-in admin / supervisor / technician / viewer roles, custom roles from a permission list, optional site/area scope, session login, and API keys bound to a user. A dev / single-user mode (the default) still runs with no login.
- **REST API** with OpenAPI / Swagger docs, optional API key or user tokens, bulk ingest, time-range queries, server-side pagination, and CSV/JSON export
- **Inputs:** the UI, CSV/Excel import, a simple IoT webhook, and an optional MQTT subscriber
- **Outputs:** REST and CSV, direct SQL (for Grafana), and outbound webhooks on out-of-range events, work-order changes, and document approval events
- **Scales to 5,000+ equipment** with indexes, pagination, Alembic migrations, and a load-test script (see [docs/postgres.md](docs/postgres.md) and [docs/load-test-results.json](docs/load-test-results.json))
- **Optional starter packs** (generic plant equipment, plus a generic semiconductor-utilities pack) — not loaded unless you choose them
- **v0.2 offline app** included at [`standalone/Logbook.html`](standalone/Logbook.html) for a no-server, browser-only workflow; a v0.2 JSON backup can be imported into the database-backed app

![Trends](docs/images/trends-light.png)

![Work orders](docs/images/work-orders-light.png)

![Approvals](docs/images/approvals-light.png)

## Quick start

Python 3.11+ recommended.

```bash
git clone https://github.com/YOUR-USER/logentra-os.git
cd logentra-os
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

![API documentation](docs/images/api-docs.png)

```bash
curl -s http://127.0.0.1:8787/api/health
curl -s -X POST http://127.0.0.1:8787/api/demo
curl -s "http://127.0.0.1:8787/api/readings?tag=P-101&parameter=vibration&from=2026-11-01&to=2026-11-23"
curl -s "http://127.0.0.1:8787/api/events/out-of-range?unacknowledged=true"
curl -s -X POST http://127.0.0.1:8787/api/ingest/readings \
  -H 'Content-Type: application/json' \
  -d '{"tag":"P-101","parameter":"flow","value":52.1,"ts":"2026-11-23T08:30:00"}'

# Work orders (also in docs/integrations.md)
curl -s -X POST http://127.0.0.1:8787/api/work-orders \
  -H 'Content-Type: application/json' \
  -d '{"title":"Replace seal","equipment_id":"eq_p101","priority":"high"}'
curl -s "http://127.0.0.1:8787/api/work-orders?status=open&page=1&page_size=50"

# Document approvals (also in docs/integrations.md)
curl -s -X POST http://127.0.0.1:8787/api/documents \\
  -F 'file=@test-sheet.pdf;type=application/pdf' \\
  -F 'title=P-101 commissioning test sheet' \\
  -F 'document_type=commissioning_test_sheet' \\
  -F 'equipment_id=eq_p101' \\
  -F 'submit=true'
curl -s http://127.0.0.1:8787/api/documents/queue
curl -s -X POST http://127.0.0.1:8787/api/documents/DOC_ID/approve -H 'Content-Type: application/json' -d '{"comment":"Limits checked"}'
```

![Login](docs/images/login.png)

![Users and roles](docs/images/users.png)

Demo tags such as P-101 and the “Northbrook Works” sample plant are fictional.

Full setup, schema, and route list: **[docs/README.md](docs/README.md)**.  
Grafana, Node-RED, a Python client, and an AI-agent prompt: **[docs/integrations.md](docs/integrations.md)**.  
Copy-paste examples also live in [`examples/`](examples/).  
Desktop packaging notes (PyInstaller, Tauri sidecar, Docker): **[docs/packaging.md](docs/packaging.md)**.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/test_api.py tests/test_ingest.py tests/test_oor.py tests/test_auth.py tests/test_workorders.py tests/test_pagination.py tests/test_migrations.py -q
```

The optional Playwright UI smoke test (`tests/test_ui.py`) needs a local Chrome/Chromium and is not run in the default GitHub Actions workflow.

## Roadmap

Useful next steps, drawn from current limits:

- **Live UI updates** when MQTT or a webhook writes a reading (today, refresh the Logs or Trends page)
- **Webhook retry / dead-letter** instead of a single attempt (deliveries are already logged)
- **MQTT store-and-forward** if the broker is down
- **Richer Excel ingest** (formulas, merged cells)
- **Desktop installer** — PyInstaller first, then an optional Tauri (or similar) window around the same sidecar
- **Attachment file storage backends** beyond the local `data/attachments/` folder (the API already stores metadata)
- **SSO / LDAP** in front of the built-in username+password login

This is an on-prem operations notebook and lightweight CMMS, not a certified process historian or a safety-rated control system. Hash user passwords; do not store other secrets or confidential site data in equipment records.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests are welcome.

## License

[MIT](LICENSE). The copyright line currently reads **Logentra OS contributors** — the owner name will be added when it is confirmed.
