# v0.6 release acceptance matrix

| Mandatory criterion | Status | Evidence or exact remaining condition |
| --- | --- | --- |
| Production container deployment verified | BLOCKED | Docker socket access denied. Static Compose validation passed. |
| Container persistence and restart recovery | BLOCKED | No application, PostgreSQL, nginx or worker containers could be started. |
| PostgreSQL production performance qualified | FAIL | Authenticated host HTTP missed 300 ms P95 at 50 and 100 users. Container measurements remain blocked. |
| Scoped browser workflows verified | BLOCKED | Local Chromium passed an area-restricted engineering-record workflow. Complete all requested roles and workflows against Compose ingress. |
| Historical cross-link authorization verified | PASS | Covered historical conflicting links are checked by a common query guard and verified through scoped API listing, detail, download and mutation tests. |
| Complete deployment recovery rehearsal | BLOCKED | Rich paired restoration passed on standalone SQLite and PostgreSQL. Container interruption and volume-based recovery are outstanding. |
| Critical automated tests | PASS | Full local suite completed against SQLite, PostgreSQL, Chromium, nginx and Mosquitto. |
| No unresolved critical security defects | BLOCKED | No critical defects were observed in the tested scope. Image scanning and production-topology checks are outstanding. |
| Existing application functionality preserved | PASS | The existing regression suite was retained and the scoped browser record flow was extended. |
| Database migration compatibility verified | PASS | SQLite and PostgreSQL upgraded from the v0.5 schema and restored native backups. |
| Release documentation completed | PASS | Measurement reports, security and recovery evidence and blocked gates are documented. |

**Decision (earlier candidate):** Those measurements retained release-candidate status at 0.5.0. The product version is now 0.6.0; a git tag is still reserved until requalification of the 0.6.0 fixes is accepted.
