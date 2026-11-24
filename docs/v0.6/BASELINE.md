# v0.5 baseline

Checkpoint `111d2c2`. Python 3.14.4 in an isolated environment. AMD Ryzen AI 7 350, 16 logical CPUs, 7.3 GiB RAM. SQLite, in-process HTTP, 30 sequential samples per endpoint, full synthetic dataset (5,000 assets, 500,000 readings, 2,000 work orders).

| Endpoint | P50 ms | P95 ms |
| --- | ---: | ---: |
| Equipment page | 8.00 | 9.88 |
| Equipment search | 17.06 | 21.40 |
| Readings query | 8.16 | 9.01 |
| Out-of-range events | 11.19 | 11.98 |
| Work orders page | 11.78 | 12.36 |

`python -m pytest -q` yielded 38 passed and 2 browser failures in 10.97 seconds. PostgreSQL was unavailable for the original benchmark. The baseline test and benchmark processes overlapped.

FastAPI composes modular routers with a large remaining catalog/readings API. SQLAlchemy serves both database backends, Alembic owns schema history, and the browser bundle is generated from vanilla JavaScript. Audit findings include unauthenticated bind risk, unrestricted CORS, inconsistent scope checks, privileged mixed writes, in-band webhook I/O, blind catalog upserts, inline untrusted attachments and incomplete CI coverage.

## Final qualification checkpoint

The checkpoint at c9278a6 reproduced all 56 tests with 79% coverage in 45.10 s. The prior SQLite scale experiment was rerun from a detached worktree. PostgreSQL received a new baseline using the same harness and seed size. The reproducible before-and-after results are in [Performance report](PERFORMANCE_REPORT.md).
