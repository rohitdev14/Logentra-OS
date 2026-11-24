# v0.6 development progress

- Created `checkpoint/v0.5-before-production-hardening` at `111d2c2` and switched to `release/v0.6-production-hardening`.
- System Python has no pip or ensurepip. Created an isolated environment in /tmp using the uv wheel and installed the repository's declared dependencies.
- Started baseline tests and full-size synthetic SQLite benchmark.
- Implemented profile controls, permission and scope guards, credential expiry and rotation behavior, request and upload limits, filesystem checks, record revisions, catalog preconditions, and protected evidence.
- Moved readings and ingestion into a domain router and replaced in-request webhook calls with transactional queue entries.
- Ran the 40 original tests successfully using Chromium and shared libraries extracted to /tmp. The first expanded run passed PostgreSQL migration and restore, and exposed two issues in equipment location persistence and duplicate ingestion handling. Both implementation issues were corrected.
- Added Docker/Compose definitions and offline recovery utilities. The local host has no Docker engine. PostgreSQL 18 runs in an isolated /tmp directory on a Unix socket.
- Reproduced a full-size SQLite benchmark from the v0.5 checkout and the hardening branch with the same measurement script at 1, 10, 50 and 100 client threads. No request errors occurred. The concurrent latency target remains unmet.
- Verified real webhook retry against a local receiver and MQTT reconnection using a Mosquitto process extracted under /tmp. No host services or packages were installed.
- Reviewed the local diff and deployment assets. Release acceptance remains open. The application version stays at 0.5.0.
- Fresh production CLI verification found and fixed a detached ORM instance after administrator creation. The final suite passed 56 tests with PostgreSQL, Chromium and live MQTT enabled. The separate Python 3.11 non-browser run passed 51 tests and skipped the two optional service cases.

## Final qualification progress

- Created checkpoint/v0.6-before-final-qualification at c9278a6 and reproduced the 56-test baseline and SQLite scale benchmark.
- Implemented scoped engineering routes, document area classification, audit protection, file reconciliation and controlled webhook replay.
- Added instrumented SQLite and PostgreSQL benchmark runs. Fixed an observed concurrent PostgreSQL metadata insertion race.
- Verified nginx TLS ingress locally and reran the recovery, Chromium and MQTT checks. Docker remains unavailable locally.
