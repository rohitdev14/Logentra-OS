# Logentra OS v0.7.0: QR tags and phone quick entry decisions

Decided by Rohit on 25 Nov 2026. Mockups are in `docs/v0.7/mockups/`.

## Phone quick entry
- **Layout is chosen automatically per equipment:**
  - **One form (option A)** when the equipment has about 6 readings or fewer: one large input per reading with limit, last value and a live OK / Out of range flag.
  - **Compact table (option C)** when it has more: Parameter / Last / Now rows, with a warning box for out-of-range values and "Also raise a work order".
  - Threshold: 6 parameters (a constant in code; can become a setting later).
- Option B (one reading per screen with number pad) is archived, not built.
- Out-of-range is shown live on the phone from the equipment's limits, and re-checked on the server when saved.
- No offline entry in v0.7 (planned for v0.8).

## Phone sign-in
- The phone camera app opens the QR link. No in-app camera scanning, so it works over plain HTTP on the plant LAN.
- Not signed in: a sign-in screen that shows which tag was scanned, then goes straight to that equipment.
- **"Keep me signed in on this phone" lasts 7 days.** The user can sign the phone out from their profile, and an admin can sign it out from Users & roles. Sign-in, sign-out and revocation are audited.

## QR code content
- **Each item gets a random 8-character code**; the QR encodes `<public server URL>/q/<code>`, e.g. `http://logbook.plant.local/q/A479AABD`.
- Renaming a tag does not break printed labels. Reprinting gives the item a new code; scanning the old label shows "This tag was replaced" with a link to the right equipment.
- The public server URL is a setting (Settings), so the QR works from phones on the LAN.

## Tag sheets
- **Only one stock in v0.7.0: A4, 21 labels per sheet, 63.5 × 38.1 mm (L7160-type).**
- Label shows: QR (about 28 mm), tag, name, area, "Scan to log readings", and the typed fallback address and code.
- Print screen: pick site/area and equipment, choose the start position on a part-used sheet, live A4 preview, Print / Save as PDF.
- The 8-per-sheet large plate (option B) is archived, not built.

## Photos
- **Up to 3 photos per entry.**
- **Shrunk on the phone to about 0.3 MB each** (long edge about 1600 px, JPEG) before upload; this also strips location data.
- **2 MB server cap per photo**; larger or non-image files are refused.
- Photos are kept (no auto-delete). A **storage meter on the Backup page** shows space used by photos and attachments.

## Mockups
- `mockups/qr-phone-form.png`: one form (few readings)
- `mockups/qr-phone-table.png`: compact table (many readings)
- `mockups/qr-phone-login.png`: sign-in after scanning (7 days)
- `mockups/qr-phone-done.png`: saved confirmation
- `mockups/qr-phone-tag-replaced.png`: old label scanned
- `mockups/qr-print-tags.png`: print QR tags screen
- `mockups/qr-tags-21up.png`: the 21-up A4 sheet
