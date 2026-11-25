# Logentra OS v0.7.0 design: QR tags + shift log and handover

Status: **approved by Rohit for build**. The build brief `CODEX_BUILD_BRIEF.md` wins where the two differ. Base: `main` at `01b1f2a` (v0.6.1).
Decisions taken so far: `DECISIONS-shift-handover.md`, `DECISIONS-qr-tags.md`. Mockups: `mockups/*.png`.

Everything below reuses what v0.6.1 already has: SQLAlchemy models + Alembic migrations (`migrations/versions/000N_*.py`), the `audit()` helper writing to `audit_log`, the role/permission catalogue in `logentra_server/auth.py` (built-in admin / supervisor / technician / viewer, site- and area-scoped users), session tokens (`session_tokens`), `service.ingest_readings()` with its out-of-range check, the webhook outbox (`webhook_deliveries`), the attachment store under `data/attachments/`, and the single-file UI built by `ui/build.py` with the existing CSS tokens.

---

## 1. QR tags and phone quick entry

### User flows
1. **Print tags (supervisor):** Equipment > QR tags > pick site/area and items > start position on a part-used sheet > preview of the 21-up A4 sheet > Print / Save as PDF. Items without a code get one; "Reprint" gives a new code and retires the old one.
2. **Scan and log (technician):** phone camera scans label > opens `http://<public URL>/q/<code>` in the phone browser > if not signed in, sign in ("keep me signed in on this phone", 7 days) > quick-entry screen for that item (one form if ≤ 6 parameters, compact table if more) > values show OK / Out of range live > optional note and up to 3 photos > Save > confirmation with "Scan the next tag" and "Raise work order".
3. **Old label scanned:** "This tag was replaced" with a link to the right equipment.
4. **Sign a phone out:** user (profile) or admin (Users & roles) lists phone sessions and revokes one.

### Screens
| Screen | Mockup |
|---|---|
| Print QR tags (desktop) | `mockups/qr-print-tags.png` |
| 21-up A4 sheet (print output) | `mockups/qr-tags-21up.png` |
| Phone sign-in after scan | `mockups/qr-phone-login.png` |
| Phone quick entry, few readings (one form) | `mockups/qr-phone-form.png` |
| Phone quick entry, many readings (compact table) | `mockups/qr-phone-table.png` |
| Phone saved | `mockups/qr-phone-done.png` |
| Phone old label | `mockups/qr-phone-tag-replaced.png` |
| Settings: public server URL; Backup: storage meter; Profile/Users: phone sessions | small additions to existing screens |

### Data model (migration `0008_qr_tags`)
- `equipment` + `qr_code TEXT UNIQUE NULL` (8 chars, Crockford base32, no 0/O/1/I), `qr_code_issued_at TEXT NULL`.
- **`qr_retired_codes`**: `code PK`, `equipment_id FK equipment ON DELETE SET NULL`, `retired_at`, `retired_by`. Lets an old label say "replaced" instead of "unknown".
- **`reading_photos`**: `id PK`, `entry_id` (index; matches `readings.entry_id`), `equipment_id FK`, `filename`, `content_type`, `size`, `width`, `height`, `sha256`, `storage_path` (under `data/attachments/readings/YYYY/MM/`), `uploaded_by`, `user_id FK users`, `created_at`.
- `session_tokens` + `kind TEXT NOT NULL DEFAULT 'browser'` (`browser` | `phone`), `label TEXT NULL` (e.g. "Android · Chrome"), `revoked_at TEXT NULL`. Phone sessions last 7 days.
- `settings` rows (no schema change): `public_base_url`, `phone_session_days` = 7, `photo_max_bytes` = 2 MB, `photos_per_entry` = 3.
- Backfill: none required; codes are created on first print or by "Generate codes".

### API endpoints
| Method and path | Purpose | Permission |
|---|---|---|
| `GET /q/{code}` | Server-rendered phone page (sign-in, entry, replaced, not found). Small standalone HTML, not the full SPA | none to load; data needs session |
| `POST /q/login` | Phone sign-in form; sets HttpOnly SameSite=Lax cookie (Secure when HTTPS), 7 days | public, same login throttle as `/api/auth/login` |
| `GET /api/qr/{code}` | Equipment summary, parameters with limits, last values, layout hint (`form`/`table`) | `readings.read` (scoped) |
| `POST /api/qr/{code}/entry` | Multipart: values, note, up to 3 photos, client `entry_id` (idempotency). Calls `service.ingest_readings(source="qr")` | `readings.write` (scoped) |
| `GET /api/readings/photos/{id}` | Serve a photo | `readings.read` (scoped) |
| `POST /api/equipment/qr-codes` | Generate codes for selected items (skip ones that have one) | `equipment.write` |
| `POST /api/equipment/{eid}/qr-code/rotate` | Reprint: new code, old one retired | `equipment.write` |
| `GET /tags/print?ids=…&start=N` | Print page, 21-up A4 HTML with inline SVG QR (browser Print / Save as PDF) | `equipment.read` |
| `GET/PUT /api/settings/public-url` | Public server URL used in QR codes | `settings.manage` |
| `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{id}` | List / sign out own phones | signed-in user |
| `GET /api/users/{uid}/sessions`, `DELETE …/{id}` | Admin signs out a user's phone | `users.manage` |
| `GET /api/storage` | Storage meter for Backup page | `settings.manage` |

New dependency: `segno` (pure Python, BSD, no sub-dependencies) to draw QR codes as SVG. No PDF library: sheets print from the browser.

### Permissions
No new permissions. Quick entry reuses `readings.read/write` with the existing site/area scope check; printing and reprinting use `equipment.write`; public URL uses `settings.manage`.

### Audit events
`qr.codes_generated`, `qr.code_rotated`, `qr.sheet_printed` (ids, count), `qr.entry_saved` (equipment, entry_id, count, out-of-range count, photos), `reading.photo_added`, `auth.phone_login`, `auth.phone_session_revoked` (by self/admin), `settings.public_url_changed`. Existing reading rows carry `source="qr"` and `entered_by`.

### Edge cases
- Equipment with no parameters: note and photo only, with a hint to the supervisor.
- Double tap / resend on poor Wi-Fi: client `entry_id` makes the save idempotent (existing duplicate check).
- Values: decimal comma accepted, blanks skipped, non-numbers refused per field; server recomputes out-of-range from current limits.
- User out of scope for that area: friendly "not allowed for this area" page.
- Code unknown: "not found" page; code retired: "replaced" page.
- Public URL changed after printing: old labels point at the old host. Settings warns before saving; recommend a hostname, not an IP.
- Open mode (no users): phone page works without sign-in, entered_by asked once and remembered on the phone.
- Photos: HEIC or large originals are re-encoded on the phone; server checks type by file signature and the 2 MB cap.
- Equipment deleted: label shows "not found"; photos stay with their readings.

### Risks
- **Plain HTTP on the LAN**: passwords and cookies cross the network unencrypted. Mitigation: clear note on sign-in, HTTPS doc for the nginx profile, Secure cookie when HTTPS.
- **Cookie sessions on POST**: add Origin/Referer check and a per-page CSRF token on `/q` forms.
- **Storage growth**: about 0.3 MB × 3 photos; 50 entries with photos a day is about 16 GB a year in the worst case. The meter on Backup makes this visible; backups include photos.
- **Label durability**: paper labels fade outdoors; recommend laminated or polyester L7160-type stock in docs.

---

## 2. Shift log and handover (full scope, replaces ShiftLog)

### User flows
1. **Setup (supervisor/admin):** Shifts & rosters > shift pattern (e.g. Day 07:00–19:00, Night 19:00–07:00, site time zone) > crews and leads > roster (which crew works which shift; repeat a pattern) > Handover & escalation settings (acknowledge within 30 min, first escalation to area supervisor, sign-off reminder 15 min) > log categories.
2. **During the shift (crew):** Shift log feed for the current shift > add entries (text, category, severity, equipment / work order links with `#P-101` or `WO-1042`, attachments, "Add to handover") > comment or amend (amend makes a new version, original kept).
3. **Handover (outgoing lead):** side card fills itself with open out-of-range readings, urgent/high open work orders and open punch items for the area, plus ticked log entries > remove with ✕ > write summary > **Sign off and hand over** (one click). Package is frozen (JSON snapshot + SHA-256).
4. **Acknowledge (incoming lead):** banner with due time > mark items Seen, ask questions (outgoing answers inline) > **Acknowledge** (one click, optional comment; allowed with unseen items, which are listed in the record) or **Return** with a required reason (goes back to outgoing as draft; re-sign creates a new version).
5. **Escalation:** reminder to outgoing lead 15 min before shift end; if not acknowledged N min after incoming shift start, notify first escalation role (in-app + webhook).
6. **History:** search by date, area, crew, category, severity, equipment, text; open any handover and its printable report (browser Print / Save as PDF).

### Screens
| Screen | Mockup |
|---|---|
| Shift log and handover, outgoing (timeline feed, In handover card, sign-off) | `mockups/handover-outgoing-timeline.png` |
| Acknowledge handover, incoming | `mockups/handover-incoming-acknowledge.png` |
| Shifts & rosters > Handover & escalation | `mockups/handover-site-settings-escalation.png` |
| Shift pattern, crews, roster, categories tabs | same screen, other tabs (built from existing table/form styles) |
| Handover history and search; printable report | list + A4 print layout (existing print CSS) |
| Dashboard card "Handover waiting / overdue" and nav badge | small addition |

### Data model (migration `0009_shift_handover`)
- **`shift_patterns`**: `id`, `site_id FK`, `name`, `timezone`, `created_at`, `updated_at`.
- **`shift_definitions`**: `id`, `pattern_id FK`, `code` (Day/Night/A/B/C), `name`, `start_time` (HH:MM), `duration_minutes`, `sort_order`.
- **`crews`**: `id`, `site_id FK`, `name`; **`crew_members`**: `crew_id FK`, `user_id FK`, `is_lead`.
- **`roster_entries`**: `id`, `shift_definition_id FK`, `date`, `area_id FK NULL`, `crew_id FK`, `lead_user_id FK NULL` (override). Unique (definition, date, area).
- **`shifts`** (instances): `id`, `site_id`, `area_id`, `shift_definition_id`, `date`, `starts_at`, `ends_at` (UTC), `crew_id`, `lead_user_id`, `status` (`planned` | `active` | `handover_draft` | `signed_off` | `acknowledged` | `returned`), `opened_at`, `closed_at`. Created on demand from the roster.
- **`shift_log_categories`**: `id`, `site_id NULL`, `name`, `sort_order`, `active`. Seeded: Operations, Safety, Environmental, Maintenance, Quality, Permit/LOTO, Visitors and contractors, General.
- **`shift_log_entries`** (append-only): `id`, `shift_id FK`, `event_at`, `recorded_at`, `category_id`, `severity` (info/low/medium/high/critical), `title`, `body`, `equipment_id`, `work_order_id`, `reading_id`, `punch_id` (all FK SET NULL), `in_handover` bool, `author_id`, `author_name`, `version`, `supersedes_id FK self NULL`, `voided_at`, `void_reason`.
- **`shift_log_attachments`**: same shape as `reading_photos`, `entry_id FK`, stored under `data/attachments/shiftlog/`.
- **`handovers`**: `id`, `outgoing_shift_id FK`, `incoming_shift_id FK NULL`, `site_id`, `area_id`, `version`, `status` (`draft` | `signed_off` | `acknowledged` | `returned`), `summary`, `snapshot_json`, `snapshot_sha256`, `signed_off_by/at`, `acknowledged_by/at`, `ack_comment`, `unseen_count`, `returned_by/at`, `return_reason`, `ack_due_at`, `escalated_at`, `escalated_to`, `reminder_sent_at`.
- **`handover_items`**: `id`, `handover_id FK`, `kind` (`oor_reading` | `work_order` | `punch` | `log_entry` | `note`), `ref_id`, `title`, `detail`, `severity`, `auto` bool, `removed_at`, `removed_by`, `seen_by`, `seen_at`, `sort_order`.
- **`handover_comments`**: `id`, `handover_id`, `item_id NULL`, `kind` (`question` | `answer` | `comment` | `system`), `author_id`, `author_name`, `body`, `created_at`.
- **`site_handover_settings`**: `site_id PK`, `ack_within_minutes` (30), `escalate_to_role` (supervisor), `signoff_reminder_minutes` (15). Later columns: second level, not-signed-off escalation, per-area overrides.
- Immutability: the app never UPDATEs or DELETEs entries, signed snapshots or comments; on PostgreSQL a trigger rejects UPDATE/DELETE on `shift_log_entries` and on `handovers.snapshot_json` once signed. Snapshot hash is printed on the report.

### API endpoints (all under `/api`)
| Method and path | Purpose | Permission |
|---|---|---|
| `GET/POST/PATCH /shift-patterns`, `/shift-patterns/{id}/definitions` | Patterns and shift times | `shifts.manage` |
| `GET/POST/PATCH /crews`, `/crews/{id}/members` | Crews and leads | `shifts.manage` |
| `GET /roster?from&to&area`, `PUT /roster` (bulk), `POST /roster/repeat` | Roster grid and repeat-pattern helper | read: `shiftlog.read`; write: `shifts.manage` |
| `GET/PUT /sites/{id}/handover-settings` | Escalation timer, first escalation role, reminder | `shifts.manage` |
| `GET/POST/PATCH /shift-log-categories` | Categories | `shifts.manage` |
| `GET /shifts/current?area` | Current shift with lead, crew, due times | `shiftlog.read` |
| `GET /shifts/{id}/entries`, `POST /shifts/{id}/entries` | Feed and new entry | `shiftlog.read` / `shiftlog.write` |
| `POST /shift-log-entries/{id}/amend`, `/void`, `/comments`, `/attachments` | Append-only changes | `shiftlog.write` |
| `GET /shifts/{id}/handover` | Draft with live auto-items | `shiftlog.read` |
| `POST /handovers/{id}/items`, `DELETE /handovers/{id}/items/{item}` | Pin / remove (removal recorded, not deleted) | outgoing lead |
| `POST /handovers/{id}/sign-off` | Freeze snapshot, notify incoming | outgoing lead or `shifts.manage` |
| `POST /handovers/{id}/items/{item}/seen`, `/questions`, `/answers` | Incoming review | incoming / outgoing lead |
| `POST /handovers/{id}/acknowledge` | One click, optional comment | incoming lead or `shifts.manage` |
| `POST /handovers/{id}/return` | Required reason | incoming lead |
| `GET /handovers?from&to&area&crew&status&q`, `GET /shift-log-entries?…` | History and search (pg_trgm on PostgreSQL, LIKE on SQLite) | `shiftlog.read` |
| `GET /handovers/{id}/report` | Printable A4 HTML report | `shiftlog.read` |

Escalation runs in the existing background worker loop (same process as the webhook outbox), once a minute: sends the sign-off reminder, then the escalation when `now > ack_due_at` and not acknowledged. Webhook events: `handover.signed_off`, `handover.acknowledged`, `handover.returned`, `handover.reminder`, `handover.escalated`.

### Permissions
New: `shiftlog.read`, `shiftlog.write`, `handover.act` (sign off / acknowledge / return when you are the rostered lead), `shifts.manage`.
Built-in roles: viewer gets `shiftlog.read`; technician adds `shiftlog.write`, `handover.act`; supervisor adds `shifts.manage` (can act for any shift in scope); admin gets all. Site/area scope applies as for work orders. Existing custom roles get `shiftlog.read` only (admin can extend).

### Audit events
`shift.started`, `shift.closed`, `shiftlog.entry_added`, `shiftlog.entry_amended`, `shiftlog.entry_voided`, `shiftlog.attachment_added`, `handover.item_added`, `handover.item_removed`, `handover.summary_changed`, `handover.signed_off` (hash), `handover.opened`, `handover.item_seen`, `handover.question`, `handover.answer`, `handover.acknowledged` (unseen count), `handover.returned` (reason), `handover.reminder_sent`, `handover.escalated`, `roster.changed`, `shifts.settings_changed`. All with user and timestamp, written in the same transaction as the change.

### Edge cases
- No incoming shift rostered: handover goes to "any lead in the area" and the escalation role is told immediately.
- Same person on back-to-back shifts: allowed; acknowledge is still a separate audited step.
- Shift spans midnight; time stored in UTC, shown in the site time zone.
- Late entries after sign-off: allowed in the feed, marked "after handover", and shown to the next shift; snapshot unchanged.
- Returned handover: outgoing edits and re-signs; version 2 snapshot; both versions kept.
- Acknowledge after the next shift has started: allowed, flagged late in the record.
- Linked work order closed or equipment deleted after sign-off: snapshot keeps the text as it was.
- Large shifts (hundreds of entries): feed paginates; handover card capped with "show all".
- Open mode (single user): handover works, names typed once and remembered.
- Roster changed mid-shift: current shift keeps its lead; change audited.

### Risks
- **Scope vs 5 build days**: full scope is about 7 days of work (estimate below). Mitigation: weekend buffer 12–13 Dec and a pre-agreed cut-line.
- **Escalation reliability**: depends on the background worker running; covered by a health check and a test with a frozen clock.
- **Immutability on SQLite**: no trigger; enforced in code only. PostgreSQL gets the trigger.
- **Notifications**: in-app and webhook only in v0.7; no e-mail or SMS yet (needs SMTP setup).

---

## 3. Build estimate vs timeline

| Dates | Work | Estimate |
|---|---|---|
| 26–28 Nov | Designs (this package) | done |
| **Sun 29 Nov** | **Rohit approves design = "start"** | gate |
| Mon 30 Nov | QR: migration 0008, settings (public URL), codes + rotate, QR SVG | 1 day |
| Tue 1 Dec | QR: `/q` page, phone sign-in cookie (7 days), CSRF/origin check, session revoke | 1 day |
| Wed 2 Dec | QR: quick entry (form/table chosen automatically), live flag, idempotent save | 1 day |
| Thu 3 Dec | QR: photos (shrink on phone, 3 per entry, 2 MB cap), storage meter, backup/restore incl. photos | 1 day |
| Fri 4 Dec | QR: print-tags screen + 21-up sheet, tests, docs | 1 day |
| 5–6 Dec | Buffer | |
| **Mon 7 Dec** | **QR demo + scan video for Vikram's X post**; start shift log: migration 0009, permissions | 0.5 day |
| Mon 7–Tue 8 Dec | Shifts & rosters (pattern, crews, roster, settings, categories) | 1.5 days |
| Tue 8–Wed 9 Dec | Shift log feed, entries, links, attachments, amend/void | 1.5 days |
| Thu 10 Dec | Handover auto-fill, pin/remove, summary, one-click sign-off, snapshot | 1 day |
| Fri 11 Dec | Acknowledge, seen, questions, return with reason; escalation worker | 1.5 days |
| 12–13 Dec | Buffer: history/search, printable report, tests | 1 day |
| **Mon 14 Dec** | **Shift log demo** | |
| Tue 15 Dec | Fixes from demo, upgrade test from v0.6.1 on PostgreSQL and SQLite | |
| **Wed 16 Dec** | **Release candidate**: full CI, Docker images, recovery round-trip | |
| Thu 17 Dec | Soak, release notes | |
| **Fri 18 Dec** | **v0.7.0 release + website** | |

QR totals about 5 days and fits its week. Shift log and handover totals about 7 days against 5 days plus the weekend buffer; it fits only if the buffer is used.
**Cut-line if late (move to v0.7.1):** roster "repeat pattern" helper (manual roster only), history filters beyond date/area/text, handover CSV export. Core flow (log, auto-filled handover, sign-off, acknowledge, return, first escalation, printable report) stays in v0.7.0.

## QR setup and verification

Under **Settings**, set the public URL to the server hostname that phones can reach. In **Equipment → QR tags**, select equipment, choose the first unused label position and preview the layout. Select **Print / Save as PDF** on A4 stock at 100% scale. Confirm before reprinting existing labels, since a reprint retires the previous codes.

Scan the label with the phone's camera. In a user-authenticated installation, sign in from the scan page. A phone can stay signed in for seven days, and active phone sessions are visible under **Profile** or **Users and roles**. On a shared phone, sign out when finished. Standalone open mode continues to use loopback. For shared LAN use, run the server in user-authenticated mode.

The **Backup** page shows attachment storage. Use the recovery utility for a full restorable backup with attachments, including quick-entry photos.
