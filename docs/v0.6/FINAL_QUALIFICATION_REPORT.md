# Logentra OS v0.6 final qualification

## Executive summary

Release acceptance remains open. The hardening branch now supports area-scoped operational APIs, database-level audit guards, verification of evidence files, bounded dead-letter replay and a locally verified TLS ingress example. The final suite passed 62 tests with 80% backend line coverage. PostgreSQL met the interactive latency target at 10 users and missed it at 50 and 100. Docker execution and target production deployment remain unverified. The application version stays at 0.5.0.

## Original acceptance criteria and disposition

| Gate | Status | Evidence |
| --- | --- | --- |
| Recoverable checkpoint and reproducible baseline | Verified | c9278a6 retained as checkpoint/v0.6-before-final-qualification. The 56-test baseline and SQLite scale run were reproduced. |
| Endpoint permission review and scoped writes | Partial | Endpoint matrix and positive and negative cross-scope API tests. Legacy cross-linked rows, scoped browser catalog editing and route-by-route negative coverage remain open. |
| PostgreSQL and SQLite benchmarks with 5,000 assets, 500,000 readings and 2,000 work orders | Measured | Separate before and after benchmarks at 10, 50 and 100 in-process users. PostgreSQL target missed at 50 and 100. |
| Tamper-resistant audit and evidence recovery | Verified within tested boundaries | Triggers on both databases, PostgreSQL truncate denial, staged uploads, checksum and orphan reconciliation. Database-owner resistance and full production restore drills remain open. |
| Webhook replay and MQTT recovery | Verified at local test scale | Replay preserves event ID and payload and does not rerun business writes. 100 local HTTP deliveries succeeded on their first attempt. Live MQTT reconnect and ingestion were verified. |
| Production container and TLS qualification | Partial | Local nginx HTTPS, HSTS, anonymous denial and login throttle verified. No local container runtime and no target ingress certificate or environment. |
| Full regression suite and coverage expansion | Verified, coverage short of objective | 62 passed, 80% backend line coverage. Repeat advisory scan found no known vulnerabilities in its inventory. |

## Before-and-after performance

| PostgreSQL interactive endpoints | Checkpoint P95 range | Qualified P95 range |
| --- | ---: | ---: |
| 10 users | 153–558 ms | 123–160 ms |
| 50 users | 869–3,082 ms | 788–967 ms |
| 100 users | 1,379–5,218 ms | 1,456–2,186 ms |

At 100 users, work-order P95 fell from 5,218.19 to 1,455.54 ms. Indexed out-of-range sorting preserved the legacy fallback predicate. At 10 users, the candidate removed six PostgreSQL ingestion failures seen at the checkpoint. Both qualified backend runs had zero request errors. SQLite changed unevenly under concurrent load. The full report has P50, P95, P99, per-endpoint throughput and errors, CPU, memory and authentication profile results. [Performance report](PERFORMANCE_REPORT.md)

## Security verification matrix

| Boundary | Verification |
| --- | --- |
| Equipment, records and work orders | Scoped allowed actions and rejected foreign-resource writes. Asset classification changes remain unscoped. |
| Readings, exports and imports | Scoped CSV rows, rejected cross-site file ingestion and transactional rollback for a mixed-scope batch. Global backup exports and catalog synchronization remain unscoped. |
| Document approvals | Same-area approval and cross-site denial, with step permission and checksum checks retained. Older documents have no inferred area. |
| Integration control | Scoped access denied for delivery administration. Replay requires a reason, dead state and an enabled destination with a bounded replay count. Concurrent requests permit one replay. |
| Audit and filesystem | Both databases deny direct audit rewrites and deletion. PostgreSQL denies truncate. Failed file publication removes staging. Reconciliation identifies unreferenced files without deleting them. |
| TLS boundary | HTTPS readiness, HSTS, anonymous denial and login throttling tested against an unprivileged local nginx process. |

The [endpoint matrix](AUTHORIZATION_MATRIX.md) lists permissions and scope rules. External webhook consumers must deduplicate by delivery ID to prevent repeated consumer-side effects after transport failure.

## Test and deployment verification

The local SQLite and PostgreSQL suites exercised migrations, native backup and restoration, and data preservation. Chromium verified the existing workflows and save-conflict handling. Live Mosquitto verified reconnection and ingestion after restart. The suite retained the original regression tests and added qualification cases. Four warnings remained. See the [test report](TEST_REPORT.md).

No Docker, Podman or Buildah executable was available. A PostgreSQL benchmark retry initially ran out of temporary storage. Completed synthetic datasets were removed and a fresh run finished. nginx, PostgreSQL, Chromium and Mosquitto ran from isolated local paths. The SQLite and PostgreSQL recovery checks verified restored records and attachment bytes. A deployment-sized paired restore with operator-managed certificates, mounts and broker credentials remains unverified. See the [deployment guide](DEPLOYMENT_GUIDE.md) and [recovery guide](RECOVERY_GUIDE.md).

## Outstanding limitations and decision

The release candidate remains at 0.5.0. Before 0.6.0, verify PostgreSQL P95 under 50 and 100 users on the production HTTP topology, run both container profiles and exercise persistence and restart, complete scoped browser and historical cross-link verification, and rehearse full-system restoration on the target deployment. Extend critical-path coverage and isolate pool wait and serialization overhead during further profiling.

## Git references

- c9278a6 is the pre-qualification checkpoint.
- de68861 adds scoped APIs, audit protections, reconciliation and replay.
- 57fede4 adds the PostgreSQL benchmark path, nginx configuration and qualification tests.
- This report and the updated engineering reports are committed locally on release/v0.6-production-hardening.
