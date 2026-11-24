# Architecture

Logentra OS remains a modular monolith. Uvicorn runs FastAPI, serving the generated vanilla JavaScript client and the REST API. The API composes authentication, work-order, reading/ingestion, and document routers. SQLAlchemy and Alembic support SQLite and PostgreSQL. Evidence files remain in the attachment tree.

| Area | v0.5 | Hardening branch |
| --- | --- | --- |
| Reading API | Embedded in application factory | Domain router for queries, annotations and ingestion |
| Configuration | Open mode could bind to a network interface | Explicit profiles, loopback guards, CORS allowlist and production user authentication |
| Catalog | Blind synchronization of full browser state | Transactional catalog generation, section-level browser saves and versioned records |
| Documents | Versioned files and sequential approvals | Verified checksums, safer preview and optimistic approval-state updates |
| Webhooks | Synchronous HTTP after domain commit | Domain writes and delivery rows commit together, with an independent sender |
| MQTT | Manually managed daemon thread | Paho reconnect lifecycle, TLS option and connection health |
| Recovery | Legacy JSON export | Paired native database backup and complete attachment tree |

The worker claims one due delivery with a lease and performs HTTP outside the transaction. Failures return to the queue with exponential backoff or move to dead-letter state. A crash after receipt can repeat a delivery with the same ID. Consumers use the X-Logentra-Event-ID header to deduplicate. One application process is the supported topology for this candidate.

A catalog generation orders browser writes against service-side writes. Clients supply revision numbers when editing existing engineering records. Prior snapshots are stored in audit entries. Scope-aware reads bind to the underlying equipment or work-order location. Global catalog mutations and unclassified legacy mutations require unscoped access. Documents have sites but no area model.

Equipment tags remain globally unique because existing REST and MQTT tag lookups have no site namespace. Duplicate tags need a sender-facing identity protocol and a migration plan.
