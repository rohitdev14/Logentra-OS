# v0.6 hardening release report

## Executive summary

The product version is 0.6.0. This update packages the production-hardening work with restore ownership, PostgreSQL list/search/ingest indexes and pooling, document location inheritance, pinned patched container images, and Compose operational fixes. A git tag is not created by this change; tag only after the requalification gates are accepted. The recoverable v0.5 checkpoint remains 111d2c2, referenced by checkpoint/v0.5-before-production-hardening.

## Implementation and architecture

- The v0.5 FastAPI, SQLAlchemy and Alembic monolith remains. Reading and ingestion routes now have a domain router. The browser retains its existing light and dark themes.
- Production configuration defaults to user authentication. Scoped reads, narrower permissions, login throttling, key expiry, upload limits, path containment, restricted webhook destinations and redacted diagnostics reduce exposure.
- Catalog updates use a generation token. Engineering records and document approval state use version checks. Signed evidence and approved files are protected in checked paths. Reading clients can use stable IDs for idempotent replay.
- Webhook network calls moved from domain requests to a transactional database queue. MQTT lifecycle and integration health are observable. Events have at-least-once delivery semantics.
- A non-root container definition and Compose profiles are included, along with native backup and restore for SQLite, PostgreSQL and the attachment tree.

The [architecture](ARCHITECTURE.md) and [security review](SECURITY_REVIEW.md) describe the before-and-after design and specific control boundaries.

## Verification

The full local suite passed 56 tests with 79% backend line coverage, including SQLite and PostgreSQL recovery, Chromium workflows, and MQTT reconnection after a live broker restart. The dependency audit reported no known vulnerabilities in the resolved test environment. See the [test report](TEST_REPORT.md). On SQLite, the candidate met the provisional sub-300 ms sequential P95 across all five measured read endpoints. Concurrent P95 exceeded the target on all measured interactive scenarios. Both comparison runs had zero request errors. See the [performance report](PERFORMANCE_REPORT.md).

## Compatibility and known limitations

- Standalone no-login clients require loopback. Production needs user authentication and expired or undated keys require rotation. CORS requires explicit origins.
- Production catalog sync requires If-Match and edits to existing records supply a revision. Signed record deletes return conflict. Imports accept CSV or XLSX, and generic document files download as attachments.
- Global equipment tags remain unique. Duplicate tags now return conflict, which avoids silently changing the identifier used by legacy integrations.
- Scoped operational APIs now support same-location writes and approvals. Global catalog synchronization and unclassified historical workflows remain restricted.
- Webhook consumers may receive duplicates across failures and should deduplicate by delivery ID. Bounded replay is available. High-volume delivery qualification remains open.
- Existing timestamp strings were preserved. Newly ingested explicit offsets normalize to UTC. Legacy imports and historical timestamps need a review before an across-the-board format migration.
- Audit is append-only at the API, without database-level tamper resistance. Filestore operations and database transactions remain distinct. Existing legacy JSON/CSV exports are not a substitute for native paired recovery.

## Files changed and local commits

- Backend API, authentication, services, integration workers and Alembic in logentra_server and migrations, particularly eeb2f5f.
- Packaging, Compose profiles and recovery utilities in Dockerfile, compose.yaml, requirements-postgres.txt and scripts/recovery.py with 9b1f9d4.
- Browser source and generated bundles in ui with cbbca60, 48ac2ab and eebc7e4.
- Regression tests, pipeline and load harness in tests, .github/workflows/tests.yml and scripts/load_test.py with a74d5d0 and d6bd3a8.
- Baseline and planning in 7e3e7dd, with release documentation, README, CHANGELOG and roadmap on the release branch.

## Acceptance and next steps

The major open gates are concurrent latency, complete scope-aware read/write workflows, production ingress and container qualification, and full-scale PostgreSQL verification. Local Docker execution was blocked because no Docker, Podman or Buildah executable was available. YAML and the underlying Python/PostgreSQL paths were checked. GitHub Actions was updated locally and has not run remotely.

Before tagging 0.6.0, qualify production access and recovery on the target topology and complete the scoped permission matrix. The [roadmap](../ROADMAP.md) covers the v0.7 priorities of site-scoped identity, audit-ready replay, extensible revision history and broader UI and integration coverage.

## Final qualification update

Final qualification adds scoped operational workflows, protective audit triggers, evidence reconciliation, replay and a tested nginx example. PostgreSQL meets the provisional target at 10 users and misses it at 50 and 100. Container execution remains blocked locally. The updated decision and commit references are in the [final qualification report](FINAL_QUALIFICATION_REPORT.md).

## Release completion update

The application version is 0.6.0. See [the final qualification report](FINAL_QUALIFICATION_REPORT.md) for the earlier candidate measurements and [CHANGELOG](../../CHANGELOG.md) for the 0.6.0 contents.
