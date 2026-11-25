# Logentra OS v0.7.0: shift log and handover decisions

Decided by Rohit on 25 Nov 2026. These are design rules for the v0.7.0 build. Mockups are in `docs/v0.7/mockups/`.

## Layout
- **Option C (timeline feed) is chosen.** The shift log is the handover: entries are logged as a running feed, and items are added to the handover as they happen. Options A (checklist) and B (cards) are archived (files kept, not built).

## Sign-off and acknowledge
- **One click, no password re-entry**, for both the outgoing sign-off and the incoming acknowledge.
- Every sign-off, acknowledge, return, question and answer is still written to the immutable audit trail with the user and timestamp. The package is frozen (snapshot + hash) at sign-off; later changes become new log entries.
- **The incoming lead can return the handover** to the outgoing lead. A reason is required (always on, not configurable).
- **The incoming lead can acknowledge with unseen items.** Items not marked "Seen" are listed as unseen in the handover record and the printable/PDF report.

## What goes into the handover automatically
- Open out-of-range readings in the area.
- Open work orders with urgent or high priority in the area.
- Open punch items in the area.
- Plain shift-log entries are added **only when ticked** ("Add to handover").
- The outgoing lead can remove any item with ✕ before sign-off (the removal is audited).

## Escalation (per-site setting)
Shipping in v0.7.0, configured per site under Shifts & rosters > Handover & escalation:
- **Acknowledge within** N minutes of the incoming shift start (default 30, editable).
- **First escalation** to a chosen role (default: area supervisor), in the app and by webhook.
- **Sign-off reminder** to the outgoing lead N minutes before the shift ends (default 15).

Later (not in v0.7.0; shown greyed as "coming later" in the settings mockup):
- Second escalation level (e.g. to site admin).
- Escalation when the outgoing lead has not signed off after the shift ends.
- Per-area overrides of the site timer.

## Mockups
- `mockups/handover-outgoing-timeline.png`: outgoing shift log and handover (main screen)
- `mockups/handover-incoming-acknowledge.png`: incoming acknowledge and return
- `mockups/handover-site-settings-escalation.png`: per-site escalation settings
