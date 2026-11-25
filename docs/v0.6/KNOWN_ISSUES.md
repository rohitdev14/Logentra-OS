# v0.6 engineering notes and known issues

Engineering detail behind the 0.6.1 maintenance update. User-facing notes are in the [changelog](../../CHANGELOG.md).
Measurements were taken on 25 November 2026 (Asia/Singapore, UTC+8) on an 8-CPU, 15 GiB test host running the
production TLS + PostgreSQL overlay with 5,002 equipment, 500,004 readings and 2,002 work orders.

## Fixed in 0.6.1

### Restore needed root and extra container capabilities
- 0.6.0 symptom: restoring with `docker compose run` failed. The working helper was
  `docker run --user 0 --cap-add DAC_OVERRIDE,CHOWN,FOWNER`, because the app container uses `cap_drop: ALL`
  and the restore refused an existing attachment tree that root could neither remove nor chown.
- 0.6.1: a `recovery` Compose service runs `scripts/recovery.py` as UID 10001 with `cap_drop: ALL` and
  `no-new-privileges`. Files it copies are already owned by the app user, so no chown is needed.
  `restore --replace` stages and verifies the bundle, then renames the live attachments and SQLite file
  (with any `-wal`/`-shm`/`-journal`) to `*.pre-restore-<time>` before moving the restored copies in.
  PostgreSQL uses `pg_restore --single-transaction --clean --if-exists`.
  Unreadable bundles fail with exit code 2 and a plain instruction instead of a traceback.
- Verified on the box: SQLite and PostgreSQL round trips through `docker compose --profile recovery run --rm
  recovery ...` with no `--user` or `--cap-add`; a document uploaded before the backup downloaded byte-for-byte
  after the restore; restored tree `10001:10001`, directories 0750, files 0640. CI now runs the SQLite round trip.

### Duplicate creates returned 500
- 0.6.0 symptom: `POST /api/sites` with an existing `id` (primary key) or `code` (unique) raised
  `IntegrityError` → 500. The same unguarded caller-supplied `id` existed on areas (including nested
  `areas[]`), roles, users, work orders and work-order rules.
- 0.6.1: explicit pre-checks return 409 with a readable message, and an application-wide `IntegrityError`
  handler returns 409 `This item already exists or conflicts with another record.` (constraint detail is logged
  server-side only).

### Login throttling locked out normal users behind nginx
- 0.6.0 symptoms: nginx `limit_req` 10r/m burst 5 returned bare 429s under ordinary repeated sign-ins.
  The app's own limiter keyed on the TCP peer with `proxy_headers=False`, so behind nginx every user shared one
  bucket (10 attempts per minute per worker), and successful sign-ins counted too.
- 0.6.1: the app counts attempts per client and clears the counter on a successful sign-in, so only failures
  accumulate. `LOGENTRA_TRUSTED_PROXIES` (IPs/CIDRs) lets it key on the right-most `X-Forwarded-For` from a
  trusted peer; the production overlay trusts RFC 1918 ranges because the app port is not published and nginx
  overwrites `X-Forwarded-For`. `Retry-After` reports the real seconds left. `LOGENTRA_LOGIN_ATTEMPTS_PER_MINUTE`
  is now settable from the environment. nginx: 30r/m burst 10 with a JSON 429 body and `Retry-After: 60`.
  The UI shows a countdown and disables Sign in until it ends.
- Check through nginx on the box: 5 consecutive successful sign-ins → 200; rapid wrong passwords → 401 ×6 then
  JSON 429 with `Retry-After: 60` (nginx burst reached first).
- Remaining: limiter state is in memory per worker, so the effective per-client ceiling is up to
  limit × workers. If Docker's userland proxy is in use, nginx may see the bridge gateway instead of real client
  addresses; use host networking or `"userland-proxy": false` where that matters.

### 50-user latency slightly over the 300 ms target
- 0.6.0 (2 workers), re-measured 25 Nov: P95 equipment 307 ms, search 303 ms, work orders 296 ms, ~36 req/s.
- 0.6.1 automatic workers (3 on 4+ CPUs, else 2), same host and data, `scripts/http_load.py`, 30 s:

  | Workers | Users | P95 equipment / search / readings / events / work orders / ingest (ms) | req/s per endpoint |
  | --- | --- | --- | --- |
  | 2 | 50 | 305 / 296 / 286 / 290 / 295 / 394 | 37.7 |
  | 3 | 50 | 220 / 220 / 204 / 198 / 217 / 284 | 54.5 |
  | 4 | 50 | 252 / 248 / 249 / 241 / 256 / 313 | 62.4 |
  | 2 | 100 | 565 / 550 / 535 / 518 / 555 / 719 | 37.7 |
  | 3 | 100 | 403 / 405 / 387 / 378 / 406 / 524 | 54.0 |
  | 4 | 100 | 1694 / 1654 / 1886 / 1883 / 1711 / 1869 | 29.9 |

  Four workers left too little CPU for PostgreSQL and nginx on the shared host and produced a long tail at
  100 users, so the default is three. Zero errors in every run. Raw results:
  `qualification-logs/v061/*.json` on the qualification box (not in the repository).
- Remaining: 100-user P95 is ~400 ms; meeting 300 ms there needs a larger host, a separate database host,
  or read-path work (planned for v0.7).

## Open

### CVE-2025-68121 in the official PostgreSQL image
- Trivy reports 1 CRITICAL in `/usr/local/bin/gosu` (Go stdlib 1.24.6, `crypto/tls` session resumption) in
  `postgres:17.12-alpine3.24`, digest `sha256:f9cf3e1f0066f0eb0c48707d864f0ac5941e9ff454a072077d97a4d278d07d73`
  (rebuilt 21 Nov 2026 22:08 UTC). Upstream moved to gosu 1.20 on 18 Nov 2026 (docker-library/postgres #1430)
  but the published binary is still flagged, and there is no newer patched 17.x tag yet.
- Not reachable in Logentra OS: gosu only drops privileges and never uses TLS. Since 0.6.1 Compose runs
  PostgreSQL as UID 70, so gosu is not executed at all; PostgreSQL is on the internal `database` network only.
  The finding is listed in `.trivyignore` with this justification. With that file, the pinned postgres image and the
  0.6.1 app image both scan with 0 CRITICAL (trivy 0.76.2, local DB from 25 Nov 2026).
- Action: re-check monthly; re-pin to the first official tag whose gosu scans clean and drop the ignore entry.

### Other
- The app image still carries Debian HIGH findings with no fix in the pinned trixie base (util-linux family,
  sqlite, openssl, ncurses/perl/systemd/acl). Rebuilds pick up fixes via `apt-get upgrade`.
- CI keeps the `postgres:16` service because the runner's `pg_dump` is version 16; moving CI to 17 needs the
  PostgreSQL 17 client installed on the runner first.
- `ruff` reports pre-existing style findings (import order, line length) outside the CI selection.
