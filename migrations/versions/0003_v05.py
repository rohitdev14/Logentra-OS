"""v0.5 document approvals: routes, documents, versions, steps, comments.

Revision ID: 0003_v05
Revises: 0002_v04
Create Date: 2026-11-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_v05"
down_revision = "0002_v04"
branch_labels = None
depends_on = None


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_table(insp, "approval_routes"):
        op.create_table(
            "approval_routes",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("document_type", sa.Text(), nullable=True),
            sa.Column("site_id", sa.Text(), sa.ForeignKey("sites.id", ondelete="SET NULL"), nullable=True),
            sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_approval_routes_document_type", "approval_routes", ["document_type"])
        op.create_index("ix_approval_routes_site_id", "approval_routes", ["site_id"])

    if not _has_table(insp, "approval_route_steps"):
        op.create_table(
            "approval_route_steps",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("route_id", sa.Text(), sa.ForeignKey("approval_routes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("assignee_type", sa.Text(), nullable=False, server_default="role"),
            sa.Column("role_name", sa.Text(), nullable=True),
            sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("label", sa.Text(), nullable=True),
        )
        op.create_index("ix_approval_route_steps_route_id", "approval_route_steps", ["route_id"])

    if not _has_table(insp, "documents"):
        op.create_table(
            "documents",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("document_type", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
            sa.Column("equipment_id", sa.Text(), sa.ForeignKey("equipment.id", ondelete="SET NULL"), nullable=True),
            sa.Column("record_id", sa.Text(), sa.ForeignKey("records.id", ondelete="SET NULL"), nullable=True),
            sa.Column("work_order_id", sa.Text(), sa.ForeignKey("work_orders.id", ondelete="SET NULL"), nullable=True),
            sa.Column("site_id", sa.Text(), sa.ForeignKey("sites.id", ondelete="SET NULL"), nullable=True),
            sa.Column("route_id", sa.Text(), sa.ForeignKey("approval_routes.id", ondelete="SET NULL"), nullable=True),
            sa.Column("current_version_id", sa.Text(), nullable=True),
            sa.Column("current_step_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("locked", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("submitted_by", sa.Text(), nullable=True),
            sa.Column("submitted_by_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("submitted_at", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_documents_status", "documents", ["status"])
        op.create_index("ix_documents_equipment", "documents", ["equipment_id"])
        op.create_index("ix_documents_updated", "documents", ["updated_at"])
        op.create_index("ix_documents_document_type", "documents", ["document_type"])

    if not _has_table(insp, "document_versions"):
        op.create_table(
            "document_versions",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("document_id", sa.Text(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("mime", sa.Text(), nullable=True),
            sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sha256", sa.Text(), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=False),
            sa.Column("uploaded_by", sa.Text(), nullable=True),
            sa.Column("uploaded_by_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])

    if not _has_table(insp, "document_approval_steps"):
        op.create_table(
            "document_approval_steps",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("document_id", sa.Text(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version_id", sa.Text(), sa.ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("assignee_type", sa.Text(), nullable=False, server_default="role"),
            sa.Column("role_name", sa.Text(), nullable=True),
            sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("label", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="waiting"),
            sa.Column("actor_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("actor_name", sa.Text(), nullable=True),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("acted_at", sa.Text(), nullable=True),
        )
        op.create_index("ix_document_approval_steps_document_id", "document_approval_steps", ["document_id"])

    if not _has_table(insp, "document_comments"):
        op.create_table(
            "document_comments",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("document_id", sa.Text(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version_id", sa.Text(), sa.ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("kind", sa.Text(), nullable=False, server_default="comment"),
            sa.Column("event", sa.Text(), nullable=True),
            sa.Column("author_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("author", sa.Text(), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_document_comments_document_id", "document_comments", ["document_id"])


def downgrade() -> None:
    for table in (
        "document_comments",
        "document_approval_steps",
        "document_versions",
        "documents",
        "approval_route_steps",
        "approval_routes",
    ):
        op.drop_table(table)
