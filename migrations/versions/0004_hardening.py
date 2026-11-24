"""Add API key lifetimes and engineering record revisions."""
import sqlalchemy as sa
from alembic import op

revision = "0004_hardening"
down_revision = "0003_v05"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("api_keys", sa.Column("expires_at", sa.Text()))
    op.add_column("documents", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("records", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    op.drop_column("records", "revision")
    op.drop_column("documents", "revision")
    op.drop_column("api_keys", "expires_at")
