"""Add shift rosters, append-only logs and versioned handovers."""
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0009_shift_handover"
down_revision = "0008_qr_tags"
branch_labels = None
depends_on = None

CATEGORIES = ("Operations", "Safety", "Environmental", "Maintenance", "Quality",
              "Permit/LOTO", "Visitors and contractors", "General")

TABLES = (
    "shift_patterns", "shift_definitions", "crews", "crew_members", "roster_entries",
    "shifts", "shift_log_categories", "shift_log_entries", "shift_log_attachments",
    "handovers", "handover_items", "handover_comments", "shift_log_comments",
    "site_handover_settings",
)


def update_custom_roles(add):
    connection = op.get_bind()
    roles = connection.execute(sa.text("SELECT id, permissions FROM roles WHERE built_in = 0")).all()
    for role_id, stored in roles:
        permissions = json.loads(stored or "[]")
        if add and "shiftlog.read" not in permissions:
            permissions.append("shiftlog.read")
        elif not add:
            permissions = [perm for perm in permissions if perm not in
                           {"shiftlog.read", "shiftlog.write", "handover.act", "shifts.manage"}]
        connection.execute(sa.text("UPDATE roles SET permissions = :permissions WHERE id = :id"),
                           {"id": role_id, "permissions": json.dumps(permissions)})


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split(";"):
        if statement.strip():
            op.execute(statement)
    categories = sa.table(
        "shift_log_categories", sa.column("id", sa.Text), sa.column("name", sa.Text),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(categories, [
        {"id": f"shift_cat_{index}", "name": name, "sort_order": index}
        for index, name in enumerate(CATEGORIES)
    ])
    update_custom_roles(add=True)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
        CREATE FUNCTION protect_shift_log_entry() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND pg_trigger_depth() > 1 THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'Shift log entries are append-only';
        END;
        $$
        """)
        op.execute("""
        CREATE TRIGGER shift_log_entries_append_only BEFORE UPDATE OR DELETE ON shift_log_entries
        FOR EACH ROW EXECUTE FUNCTION protect_shift_log_entry()
        """)
        op.execute("""
        CREATE FUNCTION protect_signed_handover() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.signed_off_at IS NOT NULL THEN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Signed handovers must be retained';
                END IF;
                IF NEW.snapshot_json IS DISTINCT FROM OLD.snapshot_json
                    OR NEW.snapshot_sha256 IS DISTINCT FROM OLD.snapshot_sha256
                    OR NEW.signed_off_at IS DISTINCT FROM OLD.signed_off_at THEN
                    RAISE EXCEPTION 'Signed handover snapshots are immutable';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$
        """)
        op.execute("""
        CREATE TRIGGER handovers_snapshot_immutable BEFORE UPDATE OR DELETE ON handovers
        FOR EACH ROW EXECUTE FUNCTION protect_signed_handover()
        """)


def downgrade():
    update_custom_roles(add=False)
    for table in reversed(TABLES):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_signed_handover()")
        op.execute("DROP FUNCTION protect_shift_log_entry()")
