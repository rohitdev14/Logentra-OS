# Logentra OS v0.6 final qualification

## Executive summary

Logentra OS remains a release candidate at 0.5.0. The completion work added a production Compose overlay, historical linked-evidence protections, scoped browser record editing, authenticated HTTPS benchmarking and a richer backup and recovery rehearsal. The Docker client is installed, but this WSL session still cannot access the daemon socket. The full local suite passed 63 tests with 80% backend line coverage. The 50- and 100-user PostgreSQL latency target remains unmet. No v0.6.0 tag was created.

## Release gates

| Criterion | Status | Evidence |
| --- | --- | --- |
| Production container deployment verified | BLOCKED | Docker socket access denied. Combined production Compose configuration parsed successfully. |
| Container persistence and restart recovery | BLOCKED | Local process recovery passed. Container lifetime and volume behavior require daemon access. |
| PostgreSQL production performance qualified | FAIL | 50- and 100-user P95 exceeded 300 ms through host HTTPS ingress. Qualified container measurements are unavailable. |
| Scoped browser workflows verified | BLOCKED | Local Chromium verified area-restricted record creation and update. Full containerized role coverage remains open. |
| Historical cross-link authorization verified | PASS | Parent and linked-data scope checks cover documents and work orders. Regressions verify list, detail, download and mutation denial. |
| Complete deployment recovery rehearsal | BLOCKED | SQLite and PostgreSQL paired restore passed on the host. Full container rehearsal remains open. |
| Critical automated tests | PASS | 63 passed, including migration, browser, recovery, audit and integration checks. |
| No unresolved critical security defects | BLOCKED | No critical defects were observed in tested paths. Image and target topology verification remain open. |
| Existing functionality preserved | PASS | Existing automated coverage retained. |
| Database migration compatibility | PASS | v0.5 migrations and native restore passed on SQLite and PostgreSQL. |
| Release documentation | PASS | Completion reports and measurements committed. |

## Previous versus current evidence

The previous suite had 62 passing tests and 80% backend line coverage. The current suite had 63 passing tests and 80% backend line coverage. This percentage measures backend lines. A critical-business-rule coverage metric was not captured, and the 90% objective remains open.

The previous PostgreSQL runs were in-process. The new runs use real authenticated HTTP through host nginx TLS ingress with 5,000 assets, 500,000 readings and 2,000 work orders. All five interactive endpoints passed 300 ms P95 at 10 users, and all missed it at 50 and 100. At 100 users the single-process interactive P95 ranged from 1,476 to 1,507 ms, with zero errors. The sustained run verified 597 acknowledged writes. Four processes consumed more memory and connections and produced worse observed tail latency. [Production benchmark](PRODUCTION_BENCHMARK.md)

## Security, deployment and recovery

The historical scope guard covers direct equipment links, records, readings, annotations and punches in work orders and transitive document links. Scoped catalog writes validate equipment and records inside the catalog transaction. The local Chromium workflow verified creation and revision of a record by an area-restricted technician and exclusion of the out-of-scope asset from selection. The dependency scan reported no known vulnerabilities in the resolved inventory. [Security verification](SECURITY_VERIFICATION.md)

Docker Engine 29.1.3 and Compose 2.40.3 are installed, but docker ps and project build both failed on socket permissions. Non-interactive sudo requires authentication. No host permission change was made. Local nginx verified TLS, HSTS, anonymous denial and throttling. [Container qualification](CONTAINER_QUALIFICATION.md)

Separate SQLite and PostgreSQL restores preserved readings, work orders, a signed record, approved document bytes, audit fingerprints, viewer permissions and queued webhook delivery. Restoration to verified evidence took 0.213 seconds on SQLite and 0.400 seconds on PostgreSQL for the small fixture. Host process restart tests retained the committed proof reading. Those measurements exclude container provisioning and production operational recovery. [Disaster recovery report](DISASTER_RECOVERY_REPORT.md)

## Acceptance decision and local commits

Retain 0.5.0. Before release, obtain authorized Docker daemon access, qualify the two container profiles and storage recovery, repeat authenticated ingress load on the production topology, and verify the complete scoped-browser role matrix there.

- a83562b is the pre-completion checkpoint and remains referenced by checkpoint/v0.6-before-release-completion.
- 8db073e adds historical evidence scope and the local scoped browser workflow.
- a604a3d prepares the TLS Compose topology and exercises paired recovery.
- The acceptance matrix records the exact remaining requirements. [Release acceptance matrix](RELEASE_ACCEPTANCE_MATRIX.md)
