"""Persist delivery state alongside the existing webhook history."""
import sqlalchemy as sa
from alembic import op

revision = "0005_outbox"
down_revision = "0004_hardening"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("webhook_deliveries", sa.Column("state", sa.Text(), server_default="historical", nullable=False))
    op.add_column("webhook_deliveries", sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("webhook_deliveries", sa.Column("available_at", sa.Float()))
    op.add_column("webhook_deliveries", sa.Column("claim_token", sa.Text()))
    op.create_index("ix_webhook_deliveries_dispatch", "webhook_deliveries", ["state", "available_at"])


def downgrade():
    op.drop_index("ix_webhook_deliveries_dispatch", table_name="webhook_deliveries")
    for name in ("claim_token", "available_at", "attempts", "state"):
        op.drop_column("webhook_deliveries", name)
