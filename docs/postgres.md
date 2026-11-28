# PostgreSQL recommended configuration

SQLite is the default and is enough for a single workstation or a small plant. For several concurrent users, or when the equipment register grows toward **5,000+ assets** with a dense reading history, run Logentra OS on PostgreSQL.

## Connect

```bash
pip install "psycopg[binary]"
export LOGENTRA_DATABASE_URL="postgresql+psycopg://logentra:logentra@127.0.0.1:5432/logentra"
python run.py
```

`python run.py` applies pending Alembic migrations to the configured database at startup by default. To apply them before startup, run `python run.py migrate` and set `LOGENTRA_AUTO_MIGRATE=off` for the app. `python run.py migrate --check` prints the installed and required revisions and exits with status 1 if an upgrade is needed.

With `compose.production.yaml`, the one-shot `migrate` service runs `python run.py migrate` after PostgreSQL becomes healthy. The app starts after the command succeeds and checks the revision on startup with `LOGENTRA_AUTO_MIGRATE=off`.

To move data from an existing SQLite database to PostgreSQL, export JSON from the old instance and re-import, or use a one-off ETL.

## Server settings (starting point)

These are conservative values for a dedicated 4–8 GB database host, not a full PostgreSQL tuning guide.

```ini
# postgresql.conf
shared_buffers = 1GB
effective_cache_size = 3GB
work_mem = 16MB
maintenance_work_mem = 256MB
wal_buffers = 16MB
checkpoint_completion_target = 0.9
random_page_cost = 1.1          # if the data is on SSD
max_connections = 100
```

Use a connection pooler (PgBouncer, transaction mode) if many UI tabs and agents share one database. The SQLAlchemy engine is created with `pool_pre_ping=True` on non-SQLite URLs.

## Indexes Logentra OS already creates

- `equipment(tag)` unique, `equipment(site_id)`, `equipment(area_id)`
- `readings(equipment_id, parameter_key, ts)` — trend queries
- `readings(ts)`, `readings(entry_id)`, `readings(out_of_range, ts)` — events
- `work_orders(status, due_at)`, `work_orders(equipment_id)`, `work_orders(assignee_id)`

After a large bulk load, run `ANALYZE`. VACUUM is only needed after mass deletes.

## Load test

```bash
python scripts/load_test.py --equipment 5000 --readings-per 100 --work-orders 2000
# optional:
#   --postgres-url 'postgresql+psycopg://logentra:logentra@127.0.0.1:5432/logentra_load'
```

Results from the last run on this tree are in [load-test-results.json](load-test-results.json).
