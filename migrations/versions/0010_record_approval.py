"""Add the record approval workflow and retain legacy signatures."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0010_record_approval"
down_revision = "0009_shift_handover"
branch_labels = None
depends_on = None

STEPS = ("submitted", "approved", "returned")
STATUS_CHECK = "status IN ('draft', 'submitted', 'returned', 'approved')"


def update_custom_roles(add):
    db = op.get_bind()
    for role_id, stored in db.execute(sa.text("SELECT id, permissions FROM roles WHERE built_in = 0")):
        permissions = json.loads(stored or "[]")
        if add and "documents.approve" in permissions and "records.approve" not in permissions:
            permissions.append("records.approve")
        elif not add and "records.approve" in permissions:
            permissions.remove("records.approve")
        else:
            continue
        db.execute(sa.text("UPDATE roles SET permissions = :permissions WHERE id = :id"),
                   {"id": role_id, "permissions": json.dumps(permissions)})


def upgrade():
    with op.batch_alter_table("records") as batch:
        batch.add_column(sa.Column("status", sa.Text(), nullable=False, server_default="draft"))
        batch.create_check_constraint("ck_records_status", STATUS_CHECK)
        for step in STEPS:
            batch.add_column(sa.Column(f"{step}_at", sa.Text()))
            batch.add_column(sa.Column(f"{step}_by", sa.Text()))
            batch.add_column(sa.Column(f"{step}_by_id", sa.Text()))
            batch.create_foreign_key(f"fk_records_{step}_by_id", "users", [f"{step}_by_id"], ["id"],
                                     ondelete="SET NULL")
        batch.add_column(sa.Column("return_comment", sa.Text()))
        batch.add_column(sa.Column("approval_comment", sa.Text()))
        batch.create_index("ix_records_status", ["status"])
    op.execute("""
        UPDATE records SET status = 'approved', approved_by = signoffs.name, approved_at = signoffs.signed_at
        FROM signoffs WHERE signoffs.record_id = records.id
    """)
    update_custom_roles(add=True)


def downgrade():
    update_custom_roles(add=False)
    with op.batch_alter_table("records") as batch:
        batch.drop_index("ix_records_status")
        batch.drop_constraint("ck_records_status", type_="check")
        for step in STEPS:
            batch.drop_constraint(f"fk_records_{step}_by_id", type_="foreignkey")
            for suffix in ("at", "by", "by_id"):
                batch.drop_column(f"{step}_{suffix}")
        batch.drop_column("return_comment")
        batch.drop_column("approval_comment")
        batch.drop_column("status")
