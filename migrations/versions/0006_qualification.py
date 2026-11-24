"""Classify documents by area and guard audit history against rewrites."""
import sqlalchemy as sa
from alembic import op

revision = "0006_qualification"
down_revision = "0005_outbox"
branch_labels = None
depends_on = None

AUDIT_FIELDS = ("id", "ts", "actor", "action", "entity_type", "entity_id", "detail")


def upgrade():
    op.execute("ALTER TABLE documents ADD COLUMN area_id TEXT REFERENCES areas(id) ON DELETE SET NULL")
    op.create_index("ix_documents_site_area", "documents", ["site_id", "area_id"])
    op.execute("""CREATE INDEX ix_readings_oor_recent ON readings (ts DESC, id DESC)
        WHERE out_of_range = 1 OR (limit_min IS NOT NULL AND value < limit_min)
          OR (limit_max IS NOT NULL AND value > limit_max)""")
    op.add_column("webhook_deliveries", sa.Column("replays", sa.Integer(), nullable=False, server_default="0"))
    if op.get_bind().dialect.name == "postgresql":
        same = " AND ".join(f"NEW.{field} IS NOT DISTINCT FROM OLD.{field}" for field in AUDIT_FIELDS)
        op.execute(f"""
            CREATE FUNCTION protect_audit_history() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'UPDATE' AND pg_trigger_depth() > 1 AND NEW.user_id IS NULL
                   AND OLD.user_id IS NOT NULL AND {same} THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'audit history is append only';
            END;
            $$;
        """)
        op.execute("CREATE TRIGGER audit_protect_row BEFORE UPDATE OR DELETE ON audit_log "
                   "FOR EACH ROW EXECUTE FUNCTION protect_audit_history()")
        op.execute("CREATE TRIGGER audit_protect_truncate BEFORE TRUNCATE ON audit_log "
                   "FOR EACH STATEMENT EXECUTE FUNCTION protect_audit_history()")
    else:
        same = " AND ".join(f"NEW.{field} IS OLD.{field}" for field in AUDIT_FIELDS)
        op.execute("CREATE TRIGGER audit_protect_delete BEFORE DELETE ON audit_log "
                   "BEGIN SELECT RAISE(ABORT, 'audit history is append only'); END")
        op.execute(f"""CREATE TRIGGER audit_protect_row BEFORE UPDATE ON audit_log
            WHEN NOT (NEW.user_id IS NULL AND OLD.user_id IS NOT NULL AND {same}
                      AND NOT EXISTS (SELECT 1 FROM users WHERE id = OLD.user_id))
            BEGIN SELECT RAISE(ABORT, 'audit history is append only'); END""")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER audit_protect_truncate ON audit_log")
        op.execute("DROP TRIGGER audit_protect_row ON audit_log")
        op.execute("DROP FUNCTION protect_audit_history()")
    else:
        op.execute("DROP TRIGGER audit_protect_row")
        op.execute("DROP TRIGGER audit_protect_delete")
    op.drop_index("ix_readings_oor_recent", table_name="readings")
    op.drop_column("webhook_deliveries", "replays")
    op.drop_index("ix_documents_site_area", table_name="documents")
    op.drop_column("documents", "area_id")
