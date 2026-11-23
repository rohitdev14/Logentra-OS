# Changelog

All notable changes to this project are documented in this file.

## 0.4.0 – early release

On-prem users, roles, work orders, and scale provisions so a site can run Logbook as an open-source alternative to a paid CMMS/Cx cloud app.

- Users, built-in roles (admin, supervisor, technician, viewer), and custom roles built from a permission list (`equipment.read/write`, `workorders.create/assign/close`, `users.manage`, `signoff`, …)
- Optional site/area scoping on users and equipment
- Password hashing (PBKDF2-SHA256), opaque session tokens, and API keys bound to a user or role
- First-run bootstrap admin via `LOGBOOK_BOOTSTRAP_ADMIN_*` or `python run.py create-admin`
- Backward-compatible **dev / single-user mode** (`auth_mode: open`, the default) so the standalone experience still works with no login
- Audit log records the acting user
- Work orders: REST CRUD, status workflow open → in_progress → on_hold → done → closed, priority, due date, linked equipment / reading / annotation / punch, comments, attachment metadata, labour/time notes
- Optional auto-create from out-of-range readings (rule per equipment/parameter, off by default)
- Webhook events `workorder.created`, `workorder.updated`, `workorder.closed`
- UI screens for login, work-order list/detail/create, and users/roles, with permission-aware controls
- Server-side pagination, filtering, and search on list endpoints; bulk equipment endpoint
- Alembic migrations (initial v0.3 schema + v0.4 upgrade) so the schema can be appended as more users and equipment are onboarded
- Indexes on equipment tag/site and `readings(equipment_id, parameter_key, ts)`, plus an `out_of_range` flag for event queries
- Load-test script (`scripts/load_test.py`) for 5,000 equipment and a realistic volume of readings
- Product name centralised in `logbook_server/branding.py` (still the Logbook placeholder; likely PlantOps later)

## 0.3.0 – early release

First public tree of the database-backed Logbook.

- Local FastAPI server with a SQLite default (PostgreSQL via connection string)
- REST API (OpenAPI / Swagger) for equipment, templates, records, readings, ingest, export, audit, and outbound webhooks
- Optional API key authentication
- Time-series readings indexed by equipment, parameter, and timestamp, with limit snapshots, annotations, and unacknowledged out-of-range events
- IoT webhook ingest and optional MQTT subscriber
- Outbound webhooks when a new reading is out of range
- UI from v0.2, talking to the API instead of `localStorage`
- Import a v0.2 JSON backup (`POST /api/migrate/v2` or Backup in the UI)
- Optional starter packs: generic plant equipment, and a generic semiconductor-utilities pack
- This repository also ships the v0.2 offline single-file app at `standalone/Logbook.html`

## 0.2.0

Offline single-file HTML app (no server): user-defined equipment columns and record templates, log book, SVG trends, annotations, CSV/Excel import, JSON backup, optional starter packs. Included here for people who want a no-install logbook or a path to migrate into later versions.
