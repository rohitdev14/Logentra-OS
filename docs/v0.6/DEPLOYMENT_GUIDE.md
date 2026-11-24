# Deployment guide

This branch is a local release candidate. Review the release report before choosing a target environment.

## Python

    python -m venv .venv
    . .venv/bin/activate
    python -m pip install -r requirements.txt
    python run.py --no-browser

The default serves SQLite on 127.0.0.1:8787. Open standalone access requires a loopback bind and client. The offline single-file HTML remains available.

For production, set LOGENTRA_PROFILE=production and run python run.py create-admin --username admin, entering the password at the prompt. Production defaults to a one-day session and requires user authentication. Deploy behind trusted HTTPS ingress and keep one application process while qualifying the release. Rate-limit by client at the ingress. Provide a specific browser origin list when access is cross-origin.

For PostgreSQL, install requirements-postgres.txt and supply the postgresql+psycopg connection URL through LOGENTRA_DATABASE_URL. Keep PostgreSQL on a private network. Enable MQTT TLS when required by your broker.

## Compose

    docker compose build
    docker compose run --rm app python run.py create-admin --username admin
    docker compose up -d app

The application runs as UID 10001 and persists /app/data in the logentra-data volume. The published host port binds to loopback. For PostgreSQL, copy .env.example to .env, provide the password and matching URL, and start docker compose --profile postgres up -d postgres. Wait for the database health check before running the administrator and app commands. PostgreSQL data has a separate volume.

GET /api/health reports liveness. GET /api/ready checks database accessibility. Authenticated integration health reports MQTT connectivity, worker status and counts by webhook delivery state. Docker execution is unverified on the local host.

## Upgrade from v0.5

1. Stop application and ingestion writers. Create a paired backup according to the recovery guide.
2. Rehearse the upgrade against a restore. Apply migrations with one process via python -m alembic upgrade head or normal startup.
3. Verify migration head 0006_qualification and check equipment, readings, records, document downloads and work orders.
4. Reissue API keys without expiry when moving to production. Verify webhook and broker configuration.
5. Resume ingestion after verification. For rollback, use the paired pre-upgrade backup and v0.5 checkpoint.

Production catalog sync sends the bootstrap meta.catalogRevision value in If-Match. Existing record edits supply revision. On a 409, reload and reconcile. Integration API responses can show queued delivery before an outcome is available. New offset-bearing readings normalize to UTC while existing data remains unchanged.

## Qualified TLS ingress

The example in deployment/nginx.conf terminates TLS 1.2 or 1.3, adds HSTS, throttles login and proxies to a loopback application port. Replace the host and certificate paths with deployment values. Run nginx -t before starting it. A local nginx smoke test exercised the example with a trusted temporary certificate and an unprivileged port. Production certificates, routing and container behavior still require environment-specific verification.
