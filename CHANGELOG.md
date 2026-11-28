# Changelog

All notable changes to this project are documented in this file.

## Unreleased

### v0.8.0 Security

- Tightened work order edit and delete permissions, anonymized public QR sign-in pages, and added an API documentation switch for production.

- **Central authorisation.** Shared permission and object-scope checks now cover the API, with a generated route-policy reference and regression tests.
- Document uploads and link changes require read access to linked equipment, records, or work orders for every account, including accounts with global scope.

- New webhook readings use the `api` source by default. Existing readings retain legacy sources such as `webhook` and `load`.

### Security tooling

- Documented supported versions, private vulnerability reporting, and self-hosted deployment scope.
- Added a Bandit severity gate and a non-blocking Ruff baseline artifact to CI.
- Added Gitleaks secret scanning and Trivy vulnerability scanning of the built app image.
- Configured weekly Dependabot updates for Python dependencies, workflow actions, and Docker images.
- Added a minimum Python test coverage threshold and CodeQL analysis for Python and JavaScript.

## 0.7.1 – Site time and record approval

- Log book times are now stored in UTC and shown in the site's timezone. Out-of-range readings from the desktop log book now appear in the shift handover.
- Back up before upgrading. Set each site's timezone in Settings → Sites, or set `LOGENTRA_TIMEZONE` or the `timezone` field in `config.yaml` before upgrading. The upgrade converts existing desktop, import and demo readings to UTC using that timezone.
- API and webhook `ts` values use UTC with a `Z` suffix. The `date` and `time` fields use site-local time.
- JSON backup imports preserve `ts` values with an explicit offset and use site-local `date` and `time` for older logs. The `reading_timezone_backfill` table retains original wall times for downgrade.
- Records can be submitted for approval, approved or returned. Approved records are locked except for punch close-out by users with `records.write` in scope. Supervisors and admins have the new `records.approve` permission.
- Each record has an audit trail.
- Audit entries for punch close-out on approved records store the username as the actor.
- Alembic applies `0010_record_approval` and `0011_site_timezone` on startup.

## 0.7.0 – QR tags and shift handover

- Print equipment QR tags on A4 21-up sheets. Scanning a tag opens the equipment's phone entry form or compact table with live limit checks, notes, photo uploads, and revocable phone sign-in.
- Configure site shift patterns, crews, rosters, log categories, and handover timers. The append-only shift log supports amendments and attachments, with automatic handover items for out-of-range readings, priority work orders and open punch items.
- Sign off a frozen version of the handover, mark items seen, ask and answer questions, acknowledge with unseen items or return with a reason. In-app and webhook notifications cover reminders and escalation. History is searchable and handovers can be printed or exported to CSV.
- Alembic applies `0008_qr_tags` and `0009_shift_handover` on startup. Back up the database and attachments before upgrading. See [upgrade and setup](docs/v0.7/OPERATIONS.md).

**Upgrading the HTTPS profile.** Set `LOGENTRA_PUBLIC_HOST` to the hostnames or IPs users open before starting the production Compose overlay. Use spaces between hosts and omit ports. See the [HTTPS upgrade instructions](docs/v0.7/OPERATIONS.md#upgrading-the-https-profile).

## 0.6.1 – maintenance update

Upgrading from 0.6.0 needs no database changes: pull the new version, rebuild, and run `docker compose up -d` as before.

- **Backup and restore in one command.** A new `recovery` service in Docker Compose backs up and restores your data as the normal app user, with no special Docker permissions. Restoring over an existing installation keeps your previous files as a safety copy. See the [recovery guide](docs/v0.6/RECOVERY_GUIDE.md).
- **Friendlier sign-in limits.** Only wrong passwords count towards the limit, and a successful sign-in resets it. People signing in through the same server no longer share one limit. If someone does hit it, the sign-in screen says how many seconds to wait and lets them try again when the time is up.
- **Clear "already exists" messages.** Adding a site, area, role, user, work order or work-order rule that already exists now says so, instead of showing a server error.
- **Faster on larger servers.** Production installs on PostgreSQL use three app workers on machines with four or more CPUs, so lists and search stay quick with more people online.
- **Updated images and build tooling.** The database and web proxy images are pinned to exact builds, the database runs under its own user, and the automated checks use the latest GitHub Actions.

Engineering notes for this release are in [docs/v0.6/KNOWN_ISSUES.md](docs/v0.6/KNOWN_ISSUES.md).

## 0.6.0 – production hardening

Production Compose overlay, scoped authorization, recovery tooling, and performance and operations fixes for a first 0.6 stable candidate. This commit does not create a git tag.

- Production profiles, loopback standalone limits, CORS configuration, login throttling, API key expiry and scoped data guards.
- Catalog generations, record and document concurrency checks, checksum verification and signed evidence protections.
- Queued webhook delivery, retry and dead-letter states, MQTT reconnect and integration health.
- Non-root containers, native backup/restore, regression coverage and browser save-conflict feedback.
- Sample backup with an out-of-range reading and operator note, plus an import round-trip test (suggested by @jul1anodev on X).
- New logo and tagline.
- Restored attachment trees are chowned to UID 10001 (or `--owner` / `LOGENTRA_ATTACHMENTS_OWNER`) and left readable by the application user.
- Alembic 0007 adds PostgreSQL `pg_trgm` and btree indexes, cheaper list counts, exact-match search predicates, connection pooling and two production workers. Operators can profile with `pg_stat_statements` and `scripts/explain_hot_paths.sql`.
- Documents inherit `site_id` and `area_id` from linked equipment when those fields are omitted.
- Container bases are pinned to `python:3.12.16-slim-trixie`, `postgres:17.12-alpine3.24` and `nginx:1.30.8-alpine3.24`, with `apt-get upgrade` during the app image build.
- Health checks have a `start_period`, the app image includes `postgresql-client`, and the deployment guide documents iptables-legacy versus nft FORWARD issues.

See the [release report](docs/v0.6/RELEASE_REPORT.md) and [deployment guide](docs/v0.6/DEPLOYMENT_GUIDE.md).

## 0.5.0 – early release

The product is named **Logentra OS** (Logbook + Engineering + Traceability). Display name, short name, and tagline live in [`logentra_server/branding.py`](logentra_server/branding.py). The Python package directory is now `logentra_server` (a `logbook_server` import shim remains). User-facing environment variables are `LOGENTRA_*`; the older `LOGBOOK_*` names are still accepted as a deprecated fallback.

A rebuilt web UI (light and dark) and a document approval workflow so technicians can submit test sheets, reports, and photos for sequential sign-off.

- Modern sidebar UI with cards, status pills, a Kanban work-order board, and an annotated trend chart on the dashboard
- Light (indigo accent, Linear/Notion-like) and dark (industrial control-room, cyan/amber) themes via CSS variables, toggled in the top bar, remembered in `localStorage`, defaulting to the OS `prefers-color-scheme`
- Product name **Logentra OS** comes from `logentra_server/branding.py`
- Documents with versioned files under `data/attachments` (sha256, MIME, size), linked to equipment, a record, or a work order
- Admin-defined approval routes (ordered steps by role or named user, optional document-type / site rules)
- Submit → approve / reject (comment required) / return for changes; resubmit creates a new version and restarts the route; the approved version is locked read-only
- Timestamped comment / audit trail and webhook events `document.submitted`, `document.step_approved`, `document.approved`, `document.rejected`, `document.returned`
- Permissions `documents.read`, `documents.upload`, `documents.approve`, `routes.manage`
- “My approvals” queue and a dashboard KPI; REST API for upload, submit, decide, and query (so agents and IoT tools can participate)
- UI for document detail (PDF/image preview, stepper, comment thread, action buttons, submit panel) and route management
- Alembic migration `0003_v05`

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
- Product name centralised in `logentra_server/branding.py` (still the Logbook placeholder; likely PlantOps later)

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
