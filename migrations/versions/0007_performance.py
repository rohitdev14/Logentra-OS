"""Add list/search/ingest indexes and optional PostgreSQL query tooling."""
from alembic import op

revision = "0007_performance"
down_revision = "0006_qualification"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_work_orders_site_area", "work_orders", ["site_id", "area_id"])
    op.create_index("ix_readings_eq_ts", "readings", ["equipment_id", "ts"])
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'pg_stat_statements unavailable; set shared_preload_libraries to profile queries';
            END;
        END
        $$;
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_equipment_tag_trgm ON equipment USING gin (tag gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_equipment_id_trgm ON equipment USING gin (id gin_trgm_ops)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_equipment_field_value_trgm "
        "ON equipment_field_values USING gin (value gin_trgm_ops)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_work_orders_title_trgm ON work_orders USING gin (title gin_trgm_ops)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_work_orders_description_trgm "
        "ON work_orders USING gin (description gin_trgm_ops)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_title_trgm ON documents USING gin (title gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_readings_remarks_trgm ON readings USING gin (remarks gin_trgm_ops)")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_readings_remarks_trgm")
        op.execute("DROP INDEX IF EXISTS ix_documents_title_trgm")
        op.execute("DROP INDEX IF EXISTS ix_work_orders_description_trgm")
        op.execute("DROP INDEX IF EXISTS ix_work_orders_title_trgm")
        op.execute("DROP INDEX IF EXISTS ix_equipment_field_value_trgm")
        op.execute("DROP INDEX IF EXISTS ix_equipment_id_trgm")
        op.execute("DROP INDEX IF EXISTS ix_equipment_tag_trgm")
    op.drop_index("ix_readings_eq_ts", table_name="readings")
    op.drop_index("ix_work_orders_site_area", table_name="work_orders")
