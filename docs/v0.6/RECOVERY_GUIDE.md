# Recovery guide

Stop application and ingestion writers so database and attachments reflect one point in time. Keep deployment configuration and secret references in their existing configuration store. Restrict access to the backup bundle.

## With Docker Compose (recommended)

The `recovery` service uses the application image, runs as the app user (UID 10001) and keeps `cap_drop: ALL`. No `--user 0` or `--cap-add` is needed. Bundles live in the `logentra-backups` volume, mounted at `/backups`. Add `-f compose.production.yaml` (and the `postgres` profile) for the production overlay.

    docker compose --profile recovery run --rm recovery backup /backups/2026-11-25
    docker compose stop app
    docker compose --profile recovery run --rm recovery restore /backups/2026-11-25 --replace
    docker compose start app

`--replace` stages and verifies the backup first. Only then are the current attachments (and a SQLite file) renamed to `*.pre-restore-<time>` and the restored copies moved into place. PostgreSQL is restored in one transaction with `--clean`, so a failed restore leaves the existing database as it was. Delete the `.pre-restore` copies after you have verified the restore.

To copy a bundle off the host, or bring one in from another host:

    # copy out
    docker run --rm -v <project>_logentra-backups:/backups -v "$PWD:/host" alpine cp -a /backups/2026-11-25 /host/
    # copy in, owned by the app user
    docker run --rm -v <project>_logentra-backups:/backups -v "$PWD/2026-11-25:/in:ro" alpine \
      sh -c 'cp -a /in /backups/2026-11-25 && chown -R 10001:10001 /backups/2026-11-25'

A bundle on a host folder must be readable by UID 10001. If it is not, restore stops with a plain message telling you to run `sudo chown -R 10001:10001 <folder>` (or `chmod -R a+rX <folder>`).

## Create a bundle without Compose

Set LOGENTRA_DATABASE_URL explicitly to the source. Run from the repository root.

    python scripts/recovery.py backup /path/to/new-bundle --attachments data/attachments

SQLite uses its backup API and integrity check. PostgreSQL uses pg_dump in custom format. The application image includes postgresql-client, so Compose one-off jobs can run pg_dump and pg_restore without a second image; stand-alone hosts still need compatible PostgreSQL client tools. Pick a destination outside the attachment tree. The bundle includes a database backup, attachments and a SHA-256 manifest.

## Restore and verify

1. Set LOGENTRA_DATABASE_URL to a fresh SQLite filename or an empty PostgreSQL database, or stop the app and add `--replace` to restore over the current installation.
2. Restore to a fresh attachment directory (or the live one with `--replace`).

       python scripts/recovery.py restore /path/to/bundle --attachments /path/to/fresh-attachments

   Restore always chmods the attachment tree so the application can read it
   (directories 0755 or 0750, files 0644 or 0640). When the restore process
   runs as root, it also chowns the tree to UID/GID 10001, the UID used by the
   container image. Override with ``--owner UID[:GID]`` or
   ``LOGENTRA_ATTACHMENTS_OWNER``. A non-root restore that cannot chown still
   makes files world-readable so the app user can open them; do not leave the
   tree as root-owned mode 0600. If you pass ``--owner`` and chown is denied,
   the command fails rather than publishing an unreadable tree. The running
   application container drops capabilities, so it cannot repair ownership
   itself.

3. Place the restored attachment tree at data/attachments in the recovery instance. Preserve the old database and attachment tree until verification passes.
4. Verify readiness, equipment, readings, record revisions and document downloads. Review pending outbound deliveries before restarting integrations. A restored pending event may be sent again.
5. Resume application and ingestion writers with the recovered pair.

A tampered bundle is rejected, and an existing SQLite target or attachment tree is rejected unless `--replace` is given. PostgreSQL database changes are transactional, while publication of the attachment directory is a separate operation. If filesystem publication fails, preserve the bundle and retry into a fresh target. For rollback, restore the paired v0.5 bundle and use the checkpoint.

## Evidence reconciliation

With writers stopped, run the following from the application root against the recovered database and matching file tree.

    python -m scripts.verify_evidence

The JSON output reports the audit fingerprint, corrupt or missing referenced document versions, and unreferenced published or staged files. Compare a known snapshot with --expected-audit-sha256. Store the trusted fingerprint separately and compare matching snapshots because new audit entries change it. Preserve unreferenced files for investigation. Restore corrupt evidence only from a paired verified backup. Automated cleanup is deliberately absent so a live or historical file cannot be discarded by this check.

## Release completion update

The follow-up restored an actual approved document and its bytes, a signed record and its revision, a viewer account, a pending delivery and the audit fingerprint. See [the disaster recovery report](DISASTER_RECOVERY_REPORT.md).
