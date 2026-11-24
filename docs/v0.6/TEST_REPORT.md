# Test report

| Run | Environment | Result |
| --- | --- | --- |
| Original v0.5 suite | Python 3.14.4, initial local environment | 38 passed, 2 browser launch failures |
| Full candidate suite with coverage | Python 3.14.4, SQLite, PostgreSQL 18.6, Chromium and Mosquitto | 56 passed in 42.95 s, 79% backend line coverage |
| Candidate non-browser suite | Python 3.11.18, SQLite | 51 passed, 2 skipped in 24.48 s. PostgreSQL and live MQTT were enabled in the full run. |
| Dependency audit | pip-audit 2.10.1, resolved local dependency inventory | No known vulnerabilities reported by the advisory service |
| Static and build checks | Ruff import and undefined-name checks, Python bytecode compilation, YAML parsing and generated UI comparison | Passed |

The full suite exercised anonymous and scoped access, authorization, login limits, API key expiry, concurrent catalog writes, stable reading IDs, document approval and checksum rejection, outbox rollback and dead-letter status, real HTTP retries, MQTT reconnect, and catalog conflict behavior in Chromium. SQLite and PostgreSQL each upgraded a database at Alembic revision 0003_v05 and preserved existing equipment. Both exercised native database backup, restore to a fresh database, recovered attachment content, and manifest tamper rejection. A production CLI test created the first administrator and verified anonymous denial and authenticated bootstrap on a fresh database.

The initial machine lacked pip, Playwright browsers, several Chromium shared libraries, PostgreSQL and Mosquitto. The release checks used an isolated Python environment, downloaded browser binaries, and runtime packages extracted to a temporary directory. PostgreSQL used a local Unix socket. No host services were changed. The production CLI check exposed an expired ORM instance after commit. Capturing identity inside the session resolved the failure and the regression now has a test.

The final run emitted the Starlette TestClient HTTPX deprecation and four resource warnings for SQLite connections in test helpers. The runtime dependency scan covers known advisories for its input inventory. The text diff was also reviewed for credential-like values, private keys and provider-token signatures.

Docker, Podman and Buildah were unavailable locally. Container smoke jobs are defined in GitHub Actions, with no remote pipeline run. Coverage remains limited on the older authentication management, work-order and import API branches. Full-scale PostgreSQL load and production ingress qualification remain open.

## Final qualification

| Check | Result |
| --- | --- |
| c9278a6 checkpoint suite | 56 passed in 45.10 s with 79% backend line coverage |
| Final SQLite, PostgreSQL, Chromium, Mosquitto and nginx suite | 62 passed in 44.24 s with 80% backend line coverage |
| Sustained local HTTP delivery | 100 distinct IDs, each delivered on its first attempt, in 0.585 s |
| Repeat dependency audit | No known vulnerabilities reported for the resolved inventory |
| Ruff, Python compilation and diff whitespace check | Passed |

The final suite includes cross-site and cross-area denial, scoped imports and exports, approval progression, concurrent replay, SQL audit guards, file publication failure, reconciliation, and nginx TLS ingress. Recovery tests used native SQLite and PostgreSQL backups and verified migration from 0003_v05 through 0006_qualification. Four warnings remained in the final run, including Starlette's HTTPX test client deprecation. Local container execution remains unavailable. The coverage objective remains open, especially on administration and import branches.

## Release completion update

The release completion suite passed 63 tests in 57.44 seconds with four warnings and 80% backend line coverage. It included live Chromium, PostgreSQL, TLS ingress, MQTT restart, scoped evidence and paired recovery. Critical-business-rule coverage was not measured separately. The refreshed pip-audit inventory contained no known advisory matches.
