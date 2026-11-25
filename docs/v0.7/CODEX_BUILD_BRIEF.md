# Logentra OS v0.7.0 build brief (for Codex)

You are building **Logentra OS v0.7.0** for Rohit Khurana. The design is approved. This brief is self-contained. Read it fully before you write code. The design and decision records sit next to it in `docs/v0.7/`:

- `docs/v0.7/design.md`: full design (flows, data model, endpoints, edge cases, risks)
- `docs/v0.7/DECISIONS-shift-handover.md`, `docs/v0.7/DECISIONS-qr-tags.md`: Rohit's decisions
- `docs/v0.7/mockups/*.png`: the screens to build. Match them.

Where this brief and `design.md` differ, **this brief wins**.

---

## 0. HARD RULES (non-negotiable)

1. **Branch:** create `feature/v0.7` from `v0.7-design` (which is `main` at v0.6.1 `01b1f2a` plus these docs). Work only there. **Never commit to or push `main`.** Never create, move or delete tags or GitHub releases.
2. **Commit identity:** every commit must be authored **and** committed as `Rohit Khurana <rohitdev14@users.noreply.github.com>`. Set it in the repo before the first commit:
   ```
   git config user.name "Rohit Khurana"
   git config user.email "rohitdev14@users.noreply.github.com"
   ```
   and commit with `GIT_COMMITTER_NAME`/`GIT_COMMITTER_EMAIL` the same if your tool overrides them. **No `Co-authored-by`, no "Codex", no bot, agent or tool trailers or mentions in commit messages.** Never use any other e-mail address. Check before every push:
   `git log --format='%an <%ae> | %cn <%ce>' origin/v0.7-design..HEAD | sort -u` must print exactly one line: `Rohit Khurana <rohitdev14@users.noreply.github.com> | Rohit Khurana <rohitdev14@users.noreply.github.com>`, and `git log --format=%B origin/v0.7-design..HEAD | grep -iE 'co-authored|codex|bot|generated'` must print nothing.
3. **Small commits** with plain messages (e.g. "Add QR codes and retired codes tables"). One logical change each.
4. **No force push, ever.** No rewriting pushed history. Do not touch branches `checkpoint/*` or `backup/*`.
5. **Pull request:** open a PR from `feature/v0.7` to `main` for review by Rok. Do not merge it.
6. **Example data:** generic industrial only (the existing fictional "Northbrook Works", tags like P-101, AHU-12, TK-03, CT-2, B-1). **No GlobalFoundries / GF or any real company content.**
7. **No donations / sponsor links / funding files** anywhere.
8. Do not bump to a different version string than `0.7.0`. Do not edit release notes of v0.6.x.

---

## 1. Goal

Ship two complete features in v0.7.0, end to end (backend, migrations, API, UI, tests, docs). **No stubs, no TODO placeholders, no "coming soon" UI** except the items Rohit explicitly marked "later" (section 4.3).

1. **QR tags on equipment** with a phone quick-entry screen and printable label sheets.
2. **Full shift log and handover** (replaces the separate ShiftLog product).

Build order: **QR first**, then shift log and handover.

---

## 2. Codebase map (what exists in v0.6.1 and what to reuse)

| Area | Where | Reuse for v0.7 |
|---|---|---|
| App factory, routers, lifespan, background worker start | `logentra_server/app.py` (`create_app`, `lifespan`, `app.include_router(...)`) | Register new routers; start escalation worker next to `events.DeliveryWorker` |
| Routers | `logentra_server/routers/{auth_users,readings,work_orders,documents}.py` (`build_router(...)` pattern) | Add `routers/qr.py`, `routers/shifts.py`, `routers/handovers.py` in the same pattern |
| Models | `logentra_server/models.py` (SQLAlchemy 2, Text ids via `uid()`, ISO text timestamps via `utcnow_iso()`) | Add new tables here |
| Migrations | `migrations/versions/0001…0007_performance.py` (Alembic) | Add `0008_qr_tags.py`, `0009_shift_handover.py`; update `tests/test_migrations.py` (asserts head is `0007_performance` today) and `docs/schema.sql` |
| Auth, roles, permissions, sessions | `logentra_server/auth.py` (`PERMISSIONS`, `BUILTIN_ROLES`, `Principal`, `create_session`, `revoke_session`, `require_permissions`, scope checks) | Add new permissions; phone sessions; keep the existing Bearer token flow untouched |
| Login routes | `logentra_server/routers/auth_users.py` (`/api/auth/login`, throttling) | Reuse the throttle for phone login |
| Readings ingest + out-of-range | `logentra_server/service.py` (`ingest_readings`, `audit`), `logentra_server/utils.py` (`out_of_range`, `uid`, `utcnow_iso`) | QR entry must go through `service.ingest_readings(..., source="qr")` |
| Audit log | `service.audit(db, action, actor=, entity_type=, entity_id=, detail=, user_id=)` → `audit_log` | Every new action writes an audit row in the same transaction |
| Webhooks / outbox | `logentra_server/events.py` (`enqueue`, `DeliveryWorker`) | New events `handover.*` |
| Attachments on disk | `logentra_server/documents.py` (`ATTACH_DIR`, safe path helpers), `WorkOrderAttachment` | Same safe-path approach for `data/attachments/readings/` and `data/attachments/shiftlog/` |
| Settings table | `models.Setting`, `service.py` around line 486 | `public_base_url`, photo limits |
| Config | `logentra_server/config.py` (`Settings`, `upload_limit_bytes`, `session_days`, profiles) | Add `phone_session_days: int = 7`, `photo_max_bytes = 2 MB`, `photos_per_entry = 3`, `public_base_url = ""` |
| Backup / restore | `scripts/recovery.py` (copies `attachments`) | Must include new photo/attachment folders (they live under the same attachments root; verify) |
| UI | `ui/src/{shell.html,styles.css,app.js,api.js,v4.js,v5.js,theme.js}` built into `ui/Logentra.html`, `ui/index.html`, `ui/Logbook.html` by `python ui/build.py` | Add views via the `views` map (`app.js` ~line 1056, `v4.views`), nav buttons in `shell.html`, styles in `styles.css`. Rebuild and commit the generated HTML |
| Version | `logentra_server/branding.py` `APP_VERSION = "0.6.1"` | Set to `0.7.0` at the end; update `CHANGELOG.md`, `README.md`, `docs/ROADMAP.md` |
| Tests | `tests/` (pytest; `test_ui.py` uses Playwright Chromium; `conftest.py` fixtures; PostgreSQL via `LOGENTRA_TEST_POSTGRES`) | Add `tests/test_qr.py`, `tests/test_shiftlog.py`, `tests/test_handover.py`, UI tests in `tests/test_ui.py` |
| CI | `.github/workflows/tests.yml` | Must pass unchanged (you may add steps, not remove) |

---

## 3. Feature 1: QR tags and phone quick entry

### 3.1 Rohit's decisions
- QR encodes `<public server URL>/q/<code>`. **Code = random 8 characters** (Crockford base32 alphabet, no 0/O/1/I/L), unique, stored per equipment. Renaming a tag never breaks a label. **Reprint = new code; old code is retired** and scanning it shows "This tag was replaced" with a link to the right equipment.
- **Public server URL is configurable** (Settings, `settings.manage`). Warn when changing it after labels were printed. Recommend a hostname over an IP.
- **The phone's own camera app opens the link.** No in-app camera scanning (needs HTTPS). Must work over plain HTTP on the LAN.
- **Phone entry layout chosen automatically:** equipment with **≤ 6 parameters → one form** (mockup `mockups/qr-phone-form.png`); **> 6 → compact table** (`mockups/qr-phone-table.png`). Threshold is a constant `QUICK_FORM_MAX_PARAMS = 6`.
- Live OK / Out of range flag on the phone from the parameter limits; **server re-checks** via `ingest_readings`.
- Note (optional) and **up to 3 photos per entry**, **shrunk on the phone** (canvas re-encode, long edge ~1600 px, JPEG, ~0.3 MB, strips EXIF/GPS), **2 MB server cap per photo**, server validates type by file signature (JPEG/PNG/WebP).
- **Storage meter on the Backup page** (bytes and file counts for reading photos, shift-log attachments, documents).
- **Phone sign-in lasts 7 days** ("Keep me signed in on this phone"), **revocable by the user** (profile) **and by an admin** (Users & roles). Not signed in → sign-in screen showing which tag was scanned → straight to that equipment after sign-in (`mockups/qr-phone-login.png`).
- **Tag sheets: only A4, 21 labels per sheet, 63.5 × 38.1 mm (L7160-type)**: margins left 7.2 mm, top 15.15 mm, column gap 2.5 mm, no row gap. Label: QR (~28 mm incl. quiet zone), tag, name, area, "Scan to log readings", fallback text `host/q/` + code. "Start at position N" for part-used sheets. Print / Save as PDF via the browser (print CSS, `@page { size: A4; margin: 0 }`, 100% scale). See `mockups/qr-print-tags.png`, `mockups/qr-tags-21up.png`.
- **No offline entry** in v0.7 (v0.8).
- Open mode (`auth_mode: open`): phone page works without sign-in; ask the name once and remember it on the phone.

### 3.2 Migration `0008_qr_tags` (down_revision `0007_performance`)
- `equipment`: add `qr_code TEXT NULL UNIQUE`, `qr_code_issued_at TEXT NULL`.
- `qr_retired_codes`: `code TEXT PK`, `equipment_id TEXT FK equipment ON DELETE SET NULL`, `retired_at TEXT NOT NULL`, `retired_by TEXT`.
- `reading_photos`: `id TEXT PK`, `entry_id TEXT NOT NULL` (index), `equipment_id TEXT FK equipment ON DELETE CASCADE` (index), `filename`, `content_type`, `size INT`, `width INT`, `height INT`, `sha256`, `storage_path`, `uploaded_by`, `user_id FK users SET NULL`, `created_at`.
- `session_tokens`: add `kind TEXT NOT NULL DEFAULT 'browser'` (`browser` | `phone`), `label TEXT NULL`, `revoked_at TEXT NULL`. Auth must reject revoked tokens.
- Works on SQLite and PostgreSQL; downgrade implemented. Mirror in `models.py` and `docs/schema.sql`.

### 3.3 Endpoints (new `routers/qr.py` unless noted)
| Method, path | Purpose | Permission |
|---|---|---|
| `GET /q/{code}` | Small server-rendered phone page (sign-in / entry / replaced / not found / not allowed). Standalone HTML + inline CSS/JS using the Logentra tokens; not the full SPA | none to load; data needs session |
| `POST /q/login` | Phone sign-in; sets HttpOnly, SameSite=Lax cookie (Secure when request is HTTPS), 7-day `phone` session; same throttle as `/api/auth/login` | public |
| `POST /q/logout` | Sign this phone out | session |
| `GET /api/qr/{code}` | Equipment summary, parameters with limits and last value, `layout: "form"|"table"` | `readings.read` (scope) |
| `POST /api/qr/{code}/entry` | Multipart: `entry_id` (client UUID, idempotent), values JSON, `note`, up to 3 `photos`. Calls `service.ingest_readings(source="qr")`; returns saved values and flags | `readings.write` (scope) |
| `GET /api/readings/photos/{id}` | Serve photo | `readings.read` (scope) |
| `POST /api/equipment/qr-codes` | Generate codes for given ids (skip existing) | `equipment.write` |
| `POST /api/equipment/{eid}/qr-code/rotate` | New code, retire old | `equipment.write` |
| `GET /tags/print?ids=…&start=N` | 21-up A4 print page with inline SVG QR | `equipment.read` |
| `GET/PUT /api/settings/public-url` | Public URL for QR | `settings.manage` |
| `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{id}` | User lists/revokes own phone sessions | signed in |
| `GET /api/users/{uid}/sessions`, `DELETE /api/users/{uid}/sessions/{sid}` | Admin revoke | `users.manage` |
| `GET /api/storage` | Storage meter | `settings.manage` |

Cookie-authenticated POSTs (`/q/*`, `/api/qr/*/entry` when using the cookie) must check `Origin`/`Referer` against the host and carry a per-page CSRF token. Bearer-token API behaviour is unchanged.
QR drawing: add **`segno`** to `requirements.txt` (pure Python, BSD, no deps), output SVG.

### 3.4 UI
- Equipment view: "QR tags" action → **Print QR tags** screen (`mockups/qr-print-tags.png`): site/area filter, search, selectable list (shows "New" / "Reprint"), start position, live A4 preview, Print / Save as PDF.
- Settings: public server URL field with warning; Backup page: storage meter; Profile and Users & roles: phone sessions with "Sign out".
- Phone pages per mockups `qr-phone-login`, `qr-phone-form`, `qr-phone-table`, `qr-phone-done` (Scan next tag / Raise work order), `qr-phone-tag-replaced`.

### 3.5 Audit events
`qr.codes_generated`, `qr.code_rotated`, `qr.sheet_printed`, `qr.entry_saved` (equipment, entry_id, count, out-of-range count, photos), `reading.photo_added`, `auth.phone_login`, `auth.phone_session_revoked` (self/admin), `settings.public_url_changed`.

### 3.6 Edge cases to handle and test
No parameters (note/photo only); double submit (same `entry_id` → no duplicates); decimal comma; blank fields skipped; non-numeric rejected per field; user out of area scope → friendly 403 page; unknown code → not found page; retired code → replaced page; >3 photos or >2 MB or wrong type → 4xx with clear message; revoked or expired phone session → back to sign-in; open mode.

---

## 4. Feature 2: Shift log and handover (full scope)

### 4.1 Rohit's decisions
- **Layout = option C, timeline feed.** The shift log is the handover (`mockups/handover-outgoing-timeline.png`). Options A/B are not built.
- **Sign-off and acknowledge are one click. No password re-entry.** Each is audited with user and timestamp. At sign-off the package is **frozen** (`snapshot_json` + SHA-256 `snapshot_sha256`); later changes are new log entries.
- **Incoming lead can return** the handover to the outgoing lead; **reason required** (always, not configurable). Return puts it back to draft; re-sign creates version 2; all versions kept.
- **Incoming can acknowledge with unseen items.** Unseen items are recorded (`unseen_count` + per-item) and listed in the handover record and printable report.
- **Auto-added to the handover:** open out-of-range readings in the area (OOR with no annotation), open work orders with priority urgent or high in the area, open punch items in the area. **Plain log entries only when ticked** ("Add to handover"). **Outgoing lead can remove any item with ✕** (removal audited, item kept with `removed_at`).
- **Escalation is a per-site setting** (Shifts & rosters → Handover & escalation, `mockups/handover-site-settings-escalation.png`):
  - **Acknowledge within N minutes** of incoming shift start (default **30**, editable).
  - **First escalation** to a chosen role (default area supervisor): in-app (nav badge + dashboard card) and webhook.
  - **Sign-off reminder** to the outgoing lead N minutes before shift end (default **15**).
  - **Later (not in v0.7.0; show greyed "Coming later"):** second escalation level, escalation when not signed off, per-area overrides.
- Notifications are in-app + webhook only (no e-mail/SMS in v0.7).
- Printable handover report: A4 HTML with print CSS (browser Print / Save as PDF); shows items, seen/unseen, questions/answers, sign-off and acknowledge (who/when), return reasons, snapshot hash.

### 4.2 Migration `0009_shift_handover` (down_revision `0008_qr_tags`)
Tables (Text ids, ISO timestamps, FKs with sensible ON DELETE; see `design.md` §2 for every column):
`shift_patterns`, `shift_definitions`, `crews`, `crew_members`, `roster_entries` (unique definition+date+area), `shifts` (status `planned|active|handover_draft|signed_off|acknowledged|returned`, times stored UTC, site timezone on pattern), `shift_log_categories` (seed: Operations, Safety, Environmental, Maintenance, Quality, Permit/LOTO, Visitors and contractors, General), `shift_log_entries` (append-only; `version`, `supersedes_id`, `voided_at`, `void_reason`, `in_handover`, links to equipment/work order/reading/punch), `shift_log_attachments`, `handovers` (`version`, `status draft|signed_off|acknowledged|returned`, `summary`, `snapshot_json`, `snapshot_sha256`, signed/ack/returned by+at, `ack_comment`, `unseen_count`, `return_reason`, `ack_due_at`, `escalated_at`, `escalated_to`, `reminder_sent_at`), `handover_items` (`kind oor_reading|work_order|punch|log_entry|note`, `ref_id`, `auto`, `removed_at/by`, `seen_by/at`), `handover_comments` (`question|answer|comment|system`), `site_handover_settings` (`ack_within_minutes` 30, `escalate_to_role` supervisor, `signoff_reminder_minutes` 15).
Immutability: app never UPDATEs/DELETEs log entries, comments or signed snapshots. On PostgreSQL add triggers rejecting UPDATE/DELETE on `shift_log_entries` and changes to `handovers.snapshot_json` once signed. Mirror in `models.py`, `docs/schema.sql`.

### 4.3 Endpoints (new `routers/shifts.py`, `routers/handovers.py`; all under `/api`)
| Method, path | Permission |
|---|---|
| `GET/POST/PATCH /shift-patterns`, `/shift-patterns/{id}/definitions` | `shifts.manage` (GET: `shiftlog.read`) |
| `GET/POST/PATCH /crews`, `/crews/{id}/members` | `shifts.manage` |
| `GET /roster?from&to&area`, `PUT /roster`, `POST /roster/repeat` | read `shiftlog.read`; write `shifts.manage` |
| `GET/PUT /sites/{id}/handover-settings` | `shifts.manage` |
| `GET/POST/PATCH /shift-log-categories` | `shifts.manage` |
| `GET /shifts/current?area` | `shiftlog.read` |
| `GET/POST /shifts/{id}/entries` | `shiftlog.read` / `shiftlog.write` |
| `POST /shift-log-entries/{id}/amend`, `/void`, `/comments`, `/attachments` | `shiftlog.write` |
| `GET /shifts/{id}/handover` (draft with live auto-items) | `shiftlog.read` |
| `POST /handovers/{id}/items`, `DELETE /handovers/{id}/items/{item}` | outgoing lead (`handover.act`) or `shifts.manage` |
| `PATCH /handovers/{id}` (summary) | outgoing lead |
| `POST /handovers/{id}/sign-off` | outgoing lead or `shifts.manage` |
| `POST /handovers/{id}/items/{item}/seen`, `/questions`; `POST /handover-comments/{id}/answer` | incoming / outgoing lead |
| `POST /handovers/{id}/acknowledge` (optional comment) | incoming lead or `shifts.manage` |
| `POST /handovers/{id}/return` (reason required, 422 if blank) | incoming lead |
| `GET /handovers?from&to&area&crew&status&q`, `GET /shift-log-entries?…` | `shiftlog.read` |
| `GET /handovers/{id}/report` (print page) | `shiftlog.read` |

Escalation worker: a small thread like `events.DeliveryWorker`, started in `lifespan`, runs every 60 s: sends the sign-off reminder, then escalates when `now > ack_due_at` and not acknowledged (once per handover). Webhook events via `events.enqueue`: `handover.signed_off`, `handover.acknowledged`, `handover.returned`, `handover.reminder`, `handover.escalated`. Make the clock injectable for tests.

### 4.4 Permissions (`auth.py`)
Add `shiftlog.read`, `shiftlog.write`, `handover.act`, `shifts.manage` to `PERMISSIONS`. Built-in roles: viewer + `shiftlog.read`; technician + `shiftlog.write`, `handover.act`; supervisor + `shifts.manage`; admin all. `handover.act` only works for the rostered lead of that shift (outgoing or incoming); `shifts.manage` may act for any shift in scope. Site/area scope enforced like work orders. Existing custom roles get `shiftlog.read` in the migration.

### 4.5 UI (per mockups)
- Nav: **Shift log** and **Handover** (Operations, with badge for waiting handovers), **Shifts & rosters** (Admin).
- Outgoing (`mockups/handover-outgoing-timeline.png`): composer (text with `#TAG` / `WO-####` linking, category, severity, attach, "Add to handover"), feed newest first with filter chips, per-entry "In handover / Comment / Amend", **In handover** side card grouped by kind with "auto" badge and ✕, outgoing summary, **Sign off and hand over** (one click) with stepper Draft → Signed off → Acknowledged.
- Incoming (`mockups/handover-incoming-acknowledge.png`): due banner, summary, items as timeline cards with Seen / Ask a question and inline Q&A, **Acknowledge** (one click, optional comment, allowed with unseen items), **Return** with required reason, read-only audit trail with snapshot hash.
- Shifts & rosters tabs: Shift pattern, Crews, Roster (grid; repeat pattern), Handover & escalation (`mockups/handover-site-settings-escalation.png`, later items greyed), Log categories.
- History/search list and printable report. Dashboard card "Handover waiting / overdue".

### 4.6 Audit events
`shift.started`, `shift.closed`, `shiftlog.entry_added|entry_amended|entry_voided|attachment_added`, `handover.item_added|item_removed|summary_changed|signed_off (hash)|opened|item_seen|question|answer|acknowledged (unseen count)|returned (reason)|reminder_sent|escalated`, `roster.changed`, `shifts.settings_changed`.

### 4.7 Edge cases to handle and test
No incoming shift rostered (notify escalation role immediately); back-to-back shifts by the same person; shifts across midnight and site time zone; late entries after sign-off (marked, shown to next shift, snapshot unchanged); return then re-sign (v2, both kept); acknowledge after next shift started (flagged late); linked WO closed or equipment deleted after sign-off (snapshot keeps text); large feeds (pagination); open mode; roster change mid-shift.

---

## 5. Build order

1. QR: migration 0008 + models + config + permissions untouched → codes/rotate + segno SVG → `/q` page + phone sessions + CSRF/origin → quick entry (form/table) + idempotent save → photos + storage meter + backup check → print tags screen + 21-up sheet → tests → docs.
2. Shift log and handover: migration 0009 + permissions → shifts & rosters + settings + categories → shift log feed/entries/attachments/amend/void → handover auto-fill/pin/remove/summary/sign-off/snapshot → incoming seen/Q&A/acknowledge/return → escalation worker + webhooks + in-app badge/dashboard card → history/search + printable report → tests → docs.
3. Release prep on the branch: `APP_VERSION = "0.7.0"`, `CHANGELOG.md`, `README.md` feature list and screenshots, `docs/ROADMAP.md`, rebuild UI.

---

## 6. Definition of Done (whole release)

**Build v0.7.0 completely: both features end to end, no stubs.** Use the slip list (section 7) **only if truly blocked**, and if you do, say exactly what slipped and why in the PR description.

Per feature, done means: migration (up and down) on SQLite and PostgreSQL; models; endpoints with permission and scope checks; audit rows for every action; UI screens matching the mockups in light and dark themes; edge cases in 3.6 / 4.7 handled; docs updated; tests below written and passing.

Tests to add (minimum): QR code generation/uniqueness/rotate/retired page; phone login cookie, 7-day expiry, user and admin revoke, CSRF/origin rejection; entry save via form and table layouts, idempotency, OOR flag server-side, scope 403; photo limits (count, size, type) and storage meter; print page renders 21 labels and start offset. Shift log: entries append-only, amend/void keep originals; handover auto-fill rules (only OOR / urgent+high WOs / open punch, ticked entries only), ✕ removal audited, one-click sign-off freezes snapshot + hash, acknowledge with unseen items recorded, return requires reason and creates v2, escalation and reminder with a frozen clock, permissions per role, history search, report renders. Migration test head moves to `0009_shift_handover`. Playwright UI tests for: phone quick entry (390 px viewport), print tags screen, outgoing sign-off, incoming acknowledge and return.

**Run ALL tests and checks and report results in the PR description:**
1. Full test suite including Playwright UI tests: install browsers and deps first (`python -m playwright install --with-deps chromium`, or `sudo npx playwright install-deps` if system libraries such as libnspr4 are missing), then `python -m pytest -q` (with `LOGENTRA_TEST_POSTGRES` set if a PostgreSQL is available; say whether PostgreSQL tests ran).
2. `ruff check --select F401,F821,F822,F823 logentra_server scripts tests`
3. `python ui/build.py` then `git diff --exit-code -- ui/Logentra.html ui/Logbook.html ui/index.html` (commit the rebuilt HTML first; the check must be clean).
4. **If Docker is available:** `docker compose build app`, `docker compose up -d --wait app`, check non-root UID 10001 and `/api/bootstrap` → 401, then the backup/restore round trip exactly as in `.github/workflows/tests.yml` job `containers` (`docker compose --profile recovery run --rm recovery backup /backups/ci` and `restore … --replace`), **plus** verify a QR reading photo survives the round trip, then the PostgreSQL profile startup and `/api/ready`. If Docker is not available, say so in the PR.
5. GitHub Actions on the PR must be green.

PR description must include: summary of both features, screenshots of the built screens, the commands above with pass/fail and counts, the identity check output from rule 2, migration notes, and anything that slipped (should be nothing).

---

## 7. Slip list for v0.7.1 (only if truly blocked; explain in the PR)
1. Roster "repeat pattern" helper (manual roster entry only).
2. History filters beyond date, area and text.
3. Handover CSV export.
The core flow (log, auto-filled handover, one-click sign-off, acknowledge, return with reason, first escalation and reminder, printable report) and the whole QR feature must not slip.
