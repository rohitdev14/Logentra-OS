# Logentra OS 0.7.0 operations

## Upgrade

Back up the database and attachment tree together before starting 0.7.0. In Docker, use the recovery service from the [recovery guide](../v0.6/RECOVERY_GUIDE.md). Upgrade the app with the usual startup command. Alembic applies these revisions in order.

| Revision | Changes |
| --- | --- |
| `0008_qr_tags` | Equipment gains nullable codes. Existing browser sessions keep their type. Quick entries, retired codes, and reading photos have separate tables. |
| `0009_shift_handover` | Adds site shift patterns, roster, shift log, versioned handovers, and escalation settings. Seeds log categories. Existing custom roles gain shift-log read access. PostgreSQL guards append-only entries and signed snapshot data with triggers. |

The migrations support SQLite and PostgreSQL. Existing equipment needs no QR backfill. The first print issues a code. Review custom roles before opening the shift log to staff. Downgrading drops the new feature tables and removes QR codes and phone-session metadata. Use the pre-upgrade backup to recover previous application state. Keep backups with both the database and attachments since uploaded files are stored alongside the database.

## QR labels and phone entry

1. In **Settings**, set the public origin to a hostname the phones can reach. Use user-authenticated mode for a shared LAN or the HTTPS production proxy. Standalone open mode binds to loopback.
2. Open **Equipment → QR tags**, select the equipment and set the first unused position on the A4 sheet.
3. Select **Print / Save as PDF** and print at 100% on 21-up, 63.5 × 38.1 mm L7160-type stock. Reprinting rotates the code and displays a replacement notice when the old code is scanned.
4. Scan with the phone's camera, enter readings and optionally add a note and up to three photos. The phone re-encodes photos before uploading them. Seven-day phone sessions can be revoked from **Profile** or **Users and roles**.
5. From the save confirmation, use **Raise work order** to open a work order linked to the equipment and one of the saved readings.

For the nginx HTTPS profile, set `server_name` in `deployment/nginx.conf` to the hostname in the public URL. nginx rejects unknown hosts with status 444 and forwards the accepted Host with its port intact. Keep the proxy inside the configured trusted proxy range so phone sign-in can validate the HTTPS origin.

The **Backup** page reports files and bytes in each attachment category. Full recovery backups include `data/attachments/readings/` and `data/attachments/shiftlog/`.

## Shift log and handover

Create a site and areas, then open **Shifts & rosters**. Define shifts in the site's IANA time zone, add crews, choose leads, and assign them to dates and areas. Set acknowledgement and reminder timers and the out-of-range reading lookback under **Handover & escalation**. The reading lookback defaults to seven days.

Use **Shift log** to record entries and check **Add to handover** to include an entry. Open unannotated out-of-range readings, high or urgent open work orders and open punch items enter the draft automatically. Leads can remove items, add a summary, and sign off with one click. Sign-off saves a snapshot and SHA-256 hash.

Incoming leads can mark items Seen, ask questions and either acknowledge or return the handover with a reason. Unseen items count towards the acknowledgement record. A return preserves the signed version and opens a new draft. Open the **Handover** history to print a report or export filtered results.

Webhook subscribers can select `handover.signed_off`, `handover.acknowledged`, `handover.returned`, `handover.reminder`, and `handover.escalated`.
