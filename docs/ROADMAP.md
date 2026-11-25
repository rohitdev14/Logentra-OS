# Roadmap

## Close the v0.6 release gates

- Complete and test site- and area-scoped workflows with a role-by-endpoint permission matrix.
- Qualify HTTPS ingress, shared rate limiting, deployment containers and production migration orchestration.
- Profile PostgreSQL and real HTTP at 10, 50 and 100 users, then tune interactive latency.
- Rehearse recovery for a full synthetic evidence store and test faults between database and filesystem operations.

## v0.7.0

- Equipment QR tags, 21-up A4 printing, phone readings and photos, and phone session management.
- Shift patterns, crews, roster, append-only shift log, and versioned handover with acknowledgement, return, printable reports and escalation.

## After v0.7

- Add offline phone entry.
- Add second-level escalation, overdue sign-off escalation and area-specific timers.

- Define site-scoped asset identity and a backward-compatible tag lookup before changing uniqueness.
- Add dead-letter replay with authorization and audit, delivery metrics, MQTT QoS configuration and broker outage drills.
- Extend engineering revision history and conflict resolution to remaining catalog and work-order changes.
- Type major remaining API payloads and expand keyboard and mobile workflow coverage.
