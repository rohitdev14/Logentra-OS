"""Expand readings with a recorded unit, quality and optional retry key."""
import sqlalchemy as sa
from alembic import op

revision = "0012_reading_contract"
down_revision = "0011_site_timezone"
branch_labels = None
depends_on = None

COLUMNS = ("unit", "quality", "idempotency_key")
INDEX = "uq_readings_equipment_idempotency"
BATCH_SIZE = 5000


def backfill(db):
    selection = """
        SELECT r.id, NULLIF(p.unit, '') AS unit,
            CASE
                WHEN lower(trim(r.source)) IN ('manual', 'qr', 'import', 'api', 'mqtt', 'modbus')
                    THEN lower(trim(r.source))
                WHEN lower(trim(r.source)) = 'webhook' THEN 'api'
                ELSE 'manual'
            END AS source
        FROM readings r
        LEFT JOIN parameters p ON p.equipment_id = r.equipment_id AND p.key = r.parameter_key
        WHERE r.quality IS NULL
    """
    order = " ORDER BY r.id LIMIT :size"
    first_batch = sa.text(selection + order)
    next_batch = sa.text(selection + " AND r.id > :after" + order)
    update = sa.text("""
        UPDATE readings SET unit = COALESCE(unit, :unit), quality = 'good', source = :source
        WHERE id = :id AND quality IS NULL
    """)
    after = None
    while True:
        if after is None:
            rows = db.execute(first_batch, {"size": BATCH_SIZE}).mappings().all()
        else:
            rows = db.execute(next_batch, {"after": after, "size": BATCH_SIZE}).mappings().all()
        if not rows:
            # Old SQLite writers may have inserted a row behind the cursor while a batch
            # was committed. Check once more under the final write lock before stamping.
            if after is None:
                return
            after = None
            continue
        db.execute(update, [dict(row) for row in rows])
        after = rows[-1]["id"]
        if len(rows) == BATCH_SIZE and db.dialect.name == "sqlite":
            # Alembic has a surrounding SQLAlchemy transaction. Keep its bookkeeping
            # intact while releasing the native SQLite writer lock between batches.
            db.connection.driver_connection.commit()
            db.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade():
    db = op.get_bind()
    inspector = sa.inspect(db)
    existing = {column["name"] for column in inspector.get_columns("readings")}
    for name in COLUMNS:
        if name not in existing:
            op.add_column("readings", sa.Column(name, sa.Text(), nullable=True))
    if INDEX not in {index["name"] for index in inspector.get_indexes("readings")}:
        predicate = sa.text("idempotency_key IS NOT NULL")
        op.create_index(INDEX, "readings", ["equipment_id", "idempotency_key"], unique=True,
                        sqlite_where=predicate, postgresql_where=predicate)
    backfill(db)


def downgrade():
    op.drop_index(INDEX, table_name="readings")
    # Both supported database families have native DROP COLUMN. Preserve existing
    # reading indexes and dependent tables instead of rebuilding their parent.
    for name in reversed(COLUMNS):
        op.drop_column("readings", name)
