# Deployment guide

This branch packages Logentra OS 0.6.0. Review the release report before choosing a target environment.

## Python

    python -m venv .venv
    . .venv/bin/activate
    python -m pip install -r requirements.txt
    python run.py --no-browser

The default serves SQLite on 127.0.0.1:8787. Open standalone access requires a loopback bind and client. The offline single-file HTML remains available.

For production, set LOGENTRA_PROFILE=production and run python run.py create-admin --username admin, entering the password at the prompt. Production defaults to a one-day session and requires user authentication. Deploy behind trusted HTTPS ingress. The production PostgreSQL overlay defaults to two application workers; set `LOGENTRA_WORKERS=1` to fall back to a single process. Rate-limit by client at the ingress. Provide a specific browser origin list when access is cross-origin.

For PostgreSQL, install requirements-postgres.txt and supply the postgresql+psycopg connection URL through LOGENTRA_DATABASE_URL. Keep PostgreSQL on a private network. Enable MQTT TLS when required by your broker.

## Compose

    docker compose build
    docker compose run --rm app python run.py create-admin --username admin
    docker compose up -d app

The application runs as UID 10001 and persists /app/data in the logentra-data volume. The published host port binds to loopback. For PostgreSQL, copy .env.example to .env, provide the password and matching URL, and start docker compose --profile postgres up -d postgres. Wait for the database health check before running the administrator and app commands. PostgreSQL data has a separate volume.

GET /api/health reports liveness. GET /api/ready checks database accessibility. Authenticated integration health reports MQTT connectivity, worker status and counts by webhook delivery state. Docker execution is unverified on the local host.

## Container images

The application image is built from `python:3.12.16-slim-trixie` and runs `apt-get upgrade` during the build so Debian security fixes are included. The production overlay pins `postgres:17.12-alpine3.24` and `nginx:1.30.8-alpine3.24`. Rebuild and retag after upstream patch releases; do not float on unversioned `latest` or unpatched major tags.

## Upgrade from v0.5

1. Stop application and ingestion writers. Create a paired backup according to the recovery guide.
2. Rehearse the upgrade against a restore. Apply migrations with one process via python -m alembic upgrade head or normal startup.
3. Verify migration head 0007_performance and check equipment, readings, records, document downloads and work orders.
4. Reissue API keys without expiry when moving to production. Verify webhook and broker configuration.
5. Resume ingestion after verification. For rollback, use the paired pre-upgrade backup and v0.5 checkpoint.

Production catalog sync sends the bootstrap meta.catalogRevision value in If-Match. Existing record edits supply revision. On a 409, reload and reconcile. Integration API responses can show queued delivery before an outcome is available. New offset-bearing readings normalize to UTC while existing data remains unchanged.

## Qualified TLS ingress

The example in deployment/nginx.conf terminates TLS 1.2 or 1.3, adds HSTS, throttles login and proxies to a loopback application port. Replace the host and certificate paths with deployment values. Run nginx -t before starting it. A local nginx smoke test exercised the example with a trusted temporary certificate and an unprivileged port. Production certificates, routing and container behavior still require environment-specific verification.

## Release completion update

For the nginx, app and PostgreSQL topology use compose.production.yaml together with compose.yaml and the postgres profile. Docker CLI and Compose are installed, but runtime verification still depends on authorized daemon access. See [container qualification](CONTAINER_QUALIFICATION.md).

## Host firewall and container networking

Docker publishes rules in the nftables (iptables-nft) FORWARD chain. Some Debian and Ubuntu hosts still use iptables-legacy as the default `iptables` command, often with a FORWARD policy of DROP. When the two backends disagree, containers cannot open TCP connections on user-defined bridges: the application stays in SYN_SENT to PostgreSQL and Compose never becomes healthy.

If `docker compose up --wait` hangs on the app while `docker logs` shows no database errors, check both backends:

    iptables-nft -L FORWARD -n -v
    iptables-legacy -L FORWARD -n -v
    nft list ruleset | sed -n '/hook forward/,/^}/p'

The nft/Docker rules should accept traffic for the Compose bridge. If the legacy table is the policy that is actually dropping packets, set `iptables-legacy -P FORWARD ACCEPT` on that host (or add matching ACCEPT rules for the Docker bridges) and confirm with `update-alternatives --display iptables` whether the host should be switched fully to nft. This is a host-firewall issue; do not add NET_ADMIN or extra capabilities to the application container.

The application image includes `postgresql-client` so backup and restore can run `pg_dump` and `pg_restore` without a second image. Use the `recovery` Compose service for backup and restore; it joins the internal `database` network in the production overlay and runs as the app user with no extra capabilities. See the [recovery guide](RECOVERY_GUIDE.md).
