# Disaster recovery rehearsal

**Isolated application restore passed. Container topology rehearsal blocked.**

The final regression runs migrated synthetic v0.5 SQLite and PostgreSQL databases and created an equipment asset, reading, work order, revision-two signed record, approved document, viewer account, webhook configuration and pending delivery. Writers were stopped for the snapshot. Native SQLite backup and PostgreSQL pg_dump were paired with the attachment tree and restored to separate destinations.

The restored system returned the approved document bytes and checksum, preserved the signed record and revision, reproduced the audit fingerprint, and retained the pending delivery and its destination. A restored viewer could read the approved document and could not approve it. A modified backup file was rejected. A separate failure-path test forced file publication to fail and verified staging cleanup. Reconciliation detected an unreferenced file without deleting it.

Measured restore-start to verified data and evidence was **0.213 seconds for SQLite** and **0.400 seconds for PostgreSQL** in the final run. These timings exclude backup creation, provisioning, DNS, certificate setup, and operator sign-off. The recovery point matched the offline snapshot, with no missing committed writes among the checked synthetic fixture. No continuously operating recovery-point objective was measured.

The requested deployment-sized recovery rehearsal, unexpected app termination, PostgreSQL restart under the container topology, attachment persistence through volume reattachment, and recovery of active worker claims remain open. Docker daemon access was denied, so no isolated Compose deployment or volume was created.

A separate host smoke test committed an authenticated synthetic reading, killed the application process with SIGKILL, and verified the same reading with the same session after restart in 0.770 seconds. Restarting the isolated PostgreSQL process and verifying the reading through the running API took 0.466 seconds. These are host process recovery observations. They do not cover Compose volumes or active worker claims.
