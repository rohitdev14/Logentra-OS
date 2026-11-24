# v0.6 implementation plan

1. Record and reproduce v0.5 tests and synthetic baseline.
2. Fail closed on network/auth configuration, unscoped access, untrusted uploads and webhook destinations. Verify negative cases.
3. Preserve signed evidence, require safe write preconditions, and establish transactional event delivery. Verify rollback, retries and stable identifiers.
4. Add lifecycle monitoring, recovery and deployment tooling, and regression coverage.
5. Re-run the benchmark and verify upgrade, existing workflows and release gates. Keep 0.5.0 if any critical gate remains open.

## Qualification outcome

Scoped operational APIs, replay, audit guards, nginx ingress and reconciliation were implemented and tested. Container execution remains blocked locally. PostgreSQL met the interactive target at 10 users and missed it at 50 and 100. The application stays at 0.5.0 and the remaining release gates are in the [final qualification report](FINAL_QUALIFICATION_REPORT.md).

## Release completion update

The additional host rehearsal closed covered historical-link regressions and the area-restricted record editing path. Container deployment and 50- and 100-user performance remain open. See [the acceptance matrix](RELEASE_ACCEPTANCE_MATRIX.md).
