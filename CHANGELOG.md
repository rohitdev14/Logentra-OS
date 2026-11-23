# Changelog

All notable changes to this project are documented in this file.

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

Offline single-file HTML app (no server): user-defined equipment columns and record templates, log book, SVG trends, annotations, CSV/Excel import, JSON backup, optional starter packs. Included here for people who want a no-install logbook or a path to migrate into v0.3.
