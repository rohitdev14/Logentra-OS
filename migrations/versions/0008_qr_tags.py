"""Add equipment QR codes, quick entries and phone sessions."""
import sqlalchemy as sa
from alembic import op

revision = "0008_qr_tags"
down_revision = "0007_performance"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("equipment", sa.Column("qr_code", sa.Text()))
    op.add_column("equipment", sa.Column("qr_code_issued_at", sa.Text()))
    op.create_index("ix_equipment_qr_code", "equipment", ["qr_code"], unique=True)
    op.create_table(
        "qr_retired_codes",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("equipment_id", sa.Text(), sa.ForeignKey("equipment.id", ondelete="SET NULL")),
        sa.Column("retired_at", sa.Text(), nullable=False),
        sa.Column("retired_by", sa.Text()),
    )
    op.create_table(
        "qr_entries",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("equipment_id", sa.Text(), sa.ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("entered_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "reading_photos",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("entry_id", sa.Text(), nullable=False),
        sa.Column("equipment_id", sa.Text(), sa.ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("uploaded_by", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    for column in ("entry_id", "equipment_id"):
        op.create_index(f"ix_reading_photos_{column}", "reading_photos", [column])
    op.add_column("session_tokens", sa.Column("kind", sa.Text(), server_default="browser", nullable=False))
    op.add_column("session_tokens", sa.Column("label", sa.Text()))
    op.add_column("session_tokens", sa.Column("revoked_at", sa.Text()))


def downgrade():
    for column in ("revoked_at", "label", "kind"):
        op.drop_column("session_tokens", column)
    op.drop_table("reading_photos")
    op.drop_table("qr_entries")
    op.drop_table("qr_retired_codes")
    op.drop_index("ix_equipment_qr_code", table_name="equipment")
    op.drop_column("equipment", "qr_code_issued_at")
    op.drop_column("equipment", "qr_code")
