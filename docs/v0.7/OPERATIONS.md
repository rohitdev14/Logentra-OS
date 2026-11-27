# Logentra OS 0.7 operations

## Upgrade

Back up the database and attachment tree together before starting 0.7.1. In Docker, use the recovery service from the [recovery guide](../v0.6/RECOVERY_GUIDE.md). Set the installation timezone before starting the upgrade. Use an IANA name such as `Asia/Singapore` in the `timezone` field of `config.yaml` or in `LOGENTRA_TIMEZONE`, including in the Compose environment or `.env`. Upgrade the app with the usual startup command. Alembic applies these revisions in order.

| Revision | Changes |
| --- | --- |
| `0008_qr_tags` | Equipment gains nullable codes. Existing browser sessions keep their type. Quick entries, retired codes, and reading photos have separate tables. |
| `0009_shift_handover` | Adds site shift patterns, roster, shift log, versioned handovers, and escalation settings. Seeds log categories. Existing custom roles gain shift-log read access. PostgreSQL guards append-only entries and signed snapshot data with triggers. |
| `0010_record_approval` | Adds record workflow fields and the `records.approve` permission. Existing signed records become approved. Custom roles with `documents.approve` also gain `records.approve`. |
| `0011_site_timezone` | Sets each site's timezone when its shift patterns agree. Otherwise it uses the configured installation timezone. Converts existing manual, import and demo readings to UTC, and records counts by timezone in the audit log. |

The migrations support SQLite and PostgreSQL. Existing equipment needs no QR backfill. The first print issues a code. Review custom roles before opening the shift log to staff. Downgrading drops the new feature tables and removes QR codes and phone-session metadata. Use the pre-upgrade backup to recover previous application state. Keep backups with both the database and attachments since uploaded files are stored alongside the database.

## Site timezone and reading timestamps

Set each site's IANA timezone under **Settings → Sites**. The app falls back to the installation timezone, which defaults to UTC. Set that default with `LOGENTRA_TIMEZONE` or the `timezone` field in `config.yaml`. The migration uses the installation timezone when shift patterns for the site lack a unanimous timezone.

`readings.ts` always stores UTC text in `YYYY-MM-DDTHH:MM:SS` format, with no suffix. The API, CSV and out-of-range webhooks expose `ts` as UTC with a `Z` suffix. Reading `date` and `time` fields show the equipment's local time. Ingest treats `date` and `time` as site-local wall time, uses the current UTC time when both are missing, and treats an explicit `ts` without an offset as UTC. A repeated local clock time uses the first occurrence, and a skipped local clock time moves forward by the size of the gap.

Date-only reading filters use the selected equipment's site timezone, the user's scoped site timezone, or the installation default. Offset-bearing filter timestamps identify UTC instants. A later edit to a site's timezone changes the display of readings while preserving their stored instants.

The upgrade leaves phone QR, API, MQTT and webhook reading timestamps intact. The migration retains the original time of each converted reading so the downgrade can restore it even if the site's timezone was edited afterward.

## Record approvals

Complete every checklist result and select **Submit for approval**. A supervisor or admin with `records.approve` can approve the record or return it with a comment. In user-authenticated mode a different approver must approve it. Submitted and approved records are locked. Returned records can be edited and submitted again. Open punches are shown to the approver.

The **Audit trail** and **Print / PDF** views show record edits, punch changes and approval events with the user's display name when available. Webhook subscribers can select `record.submitted`, `record.returned` and `record.approved`.

## QR labels and phone entry

1. In **Settings**, set the public origin to a hostname the phones can reach. Use user-authenticated mode for a shared LAN or the HTTPS production proxy. Standalone open mode binds to loopback.
2. Open **Equipment → QR tags**, select the equipment and set the first unused position on the A4 sheet.
3. Select **Print / Save as PDF** and print at 100% on 21-up, 63.5 × 38.1 mm L7160-type stock. Reprinting rotates the code and displays a replacement notice when the old code is scanned.
4. Scan with the phone's camera, enter readings and optionally add a note and up to three photos. The phone re-encodes photos before uploading them. Seven-day phone sessions can be revoked from **Profile** or **Users and roles**.
5. From the save confirmation, use **Raise work order** to open a work order linked to the equipment and one of the saved readings.

### Upgrading the HTTPS profile

Before running `docker compose up` with `compose.production.yaml`, set `LOGENTRA_PUBLIC_HOST` in your Compose environment or `.env`. List every hostname or IP users open, separated by spaces. Omit schemes and ports and bracket IPv6 addresses. For example:

```dotenv
LOGENTRA_PUBLIC_HOST="logbook.plant.local 192.168.1.50 [2001:db8::50]"
```

Compose requires this setting. nginx accepts the listed hosts case-insensitively and forwards the original Host, including the external port, for HTTPS origin checks. Unknown hosts receive status 444. Keep the proxy inside the configured trusted proxy range.

The **Backup** page reports files and bytes in each attachment category. Full recovery backups include `data/attachments/readings/` and `data/attachments/shiftlog/`.

## Shift log and handover

Create a site and areas, then open **Shifts & rosters**. Define shifts in the site's IANA time zone, add crews, choose leads, and assign them to dates and areas. Set acknowledgement and reminder timers and the out-of-range reading lookback under **Handover & escalation**. The reading lookback defaults to seven days.

Use **Shift log** to record entries and check **Add to handover** to include an entry. Open unannotated out-of-range readings, high or urgent open work orders and open punch items enter the draft automatically. Leads can remove items, add a summary, and sign off with one click. Sign-off saves a snapshot and SHA-256 hash.

Incoming leads can mark items Seen, ask questions and either acknowledge or return the handover with a reason. Unseen items count towards the acknowledgement record. A return preserves the signed version and opens a new draft. Open the **Handover** history to print a report or export filtered results.

Webhook subscribers can select `handover.signed_off`, `handover.acknowledged`, `handover.returned`, `handover.reminder`, and `handover.escalated`.
