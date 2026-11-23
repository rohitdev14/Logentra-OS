"""v0.4 users, roles, work orders, sites, indexes, out_of_range flag.

Revision ID: 0002_v04
Revises: 0001_initial
Create Date: 2026-11-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "0002_v04"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _has_column(insp, table: str, column: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def _has_index(insp, table: str, name: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(i["name"] == name for i in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_table(insp, "sites"):
        op.create_table(
            "sites",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("code", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
        op.create_index("uq_sites_code", "sites", ["code"], unique=True)

    if not _has_table(insp, "areas"):
        op.create_table(
            "areas",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("site_id", sa.Text(), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("code", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_areas_site_id", "areas", ["site_id"])

    # Refresh inspector after creates
    insp = inspect(bind)
    if not _has_column(insp, "equipment", "site_id"):
        with op.batch_alter_table("equipment") as batch:
            batch.add_column(sa.Column("site_id", sa.Text(), nullable=True))
            batch.add_column(sa.Column("area_id", sa.Text(), nullable=True))
        op.create_index("ix_equipment_site_id", "equipment", ["site_id"])
        op.create_index("ix_equipment_area_id", "equipment", ["area_id"])

    if not _has_column(insp, "readings", "out_of_range"):
        with op.batch_alter_table("readings") as batch:
            batch.add_column(sa.Column("out_of_range", sa.Integer(), nullable=False, server_default="0"))
        bind.execute(text(
            """
            UPDATE readings SET out_of_range = CASE
                WHEN (limit_min IS NOT NULL AND value < limit_min)
                  OR (limit_max IS NOT NULL AND value > limit_max)
                THEN 1 ELSE 0 END
            """
        ))
    insp = inspect(bind)
    if not _has_index(insp, "readings", "ix_readings_eq_param_ts"):
        op.create_index("ix_readings_eq_param_ts", "readings", ["equipment_id", "parameter_key", "ts"])
    if not _has_index(insp, "readings", "ix_readings_source"):
        op.create_index("ix_readings_source", "readings", ["source"])
    if not _has_index(insp, "readings", "ix_readings_oor_ts"):
        op.create_index("ix_readings_oor_ts", "readings", ["out_of_range", "ts"])

    if not _has_table(insp, "roles"):
        op.create_table(
            "roles",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("built_in", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("permissions", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
        op.create_index("uq_roles_name", "roles", ["name"], unique=True)

    if not _has_table(insp, "users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("username", sa.Text(), nullable=False),
            sa.Column("display_name", sa.Text(), nullable=True),
            sa.Column("email", sa.Text(), nullable=True),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("role_id", sa.Text(), sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("site_id", sa.Text(), sa.ForeignKey("sites.id", ondelete="SET NULL"), nullable=True),
            sa.Column("area_id", sa.Text(), sa.ForeignKey("areas.id", ondelete="SET NULL"), nullable=True),
            sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.Column("last_login_at", sa.Text(), nullable=True),
        )
        op.create_index("uq_users_username", "users", ["username"], unique=True)
        op.create_index("ix_users_role_id", "users", ["role_id"])
        op.create_index("ix_users_site_id", "users", ["site_id"])

    if not _has_table(insp, "session_tokens"):
        op.create_table(
            "session_tokens",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.Text(), nullable=False),
            sa.Column("last_used_at", sa.Text(), nullable=True),
        )
        op.create_index("uq_session_tokens_hash", "session_tokens", ["token_hash"], unique=True)
        op.create_index("ix_session_tokens_user_id", "session_tokens", ["user_id"])
        op.create_index("ix_session_tokens_expires_at", "session_tokens", ["expires_at"])

    if not _has_table(insp, "api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("role_id", sa.Text(), sa.ForeignKey("roles.id", ondelete="SET NULL"), nullable=True),
            sa.Column("name", sa.Text(), nullable=False, server_default="API key"),
            sa.Column("key_hash", sa.Text(), nullable=False),
            sa.Column("prefix", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("last_used_at", sa.Text(), nullable=True),
            sa.Column("revoked", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_index("uq_api_keys_hash", "api_keys", ["key_hash"], unique=True)
        op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])

    insp = inspect(bind)
    if not _has_column(insp, "audit_log", "user_id"):
        with op.batch_alter_table("audit_log") as batch:
            batch.add_column(sa.Column("user_id", sa.Text(), nullable=True))
        op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])

    if not _has_table(insp, "work_orders"):
        op.create_table(
            "work_orders",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="open"),
            sa.Column("priority", sa.Text(), nullable=False, server_default="medium"),
            sa.Column("equipment_id", sa.Text(), sa.ForeignKey("equipment.id", ondelete="SET NULL"), nullable=True),
            sa.Column("site_id", sa.Text(), sa.ForeignKey("sites.id", ondelete="SET NULL"), nullable=True),
            sa.Column("area_id", sa.Text(), sa.ForeignKey("areas.id", ondelete="SET NULL"), nullable=True),
            sa.Column("assignee_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_by_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("due_at", sa.Text(), nullable=True),
            sa.Column("started_at", sa.Text(), nullable=True),
            sa.Column("completed_at", sa.Text(), nullable=True),
            sa.Column("closed_at", sa.Text(), nullable=True),
            sa.Column("linked_reading_id", sa.Text(), sa.ForeignKey("readings.id", ondelete="SET NULL"), nullable=True),
            sa.Column("linked_annotation_id", sa.Text(), sa.ForeignKey("annotations.id", ondelete="SET NULL"), nullable=True),
            sa.Column("linked_punch_id", sa.Text(), sa.ForeignKey("punches.id", ondelete="SET NULL"), nullable=True),
            sa.Column("linked_record_id", sa.Text(), sa.ForeignKey("records.id", ondelete="SET NULL"), nullable=True),
            sa.Column("parameter_key", sa.Text(), nullable=True),
            sa.Column("labour_minutes", sa.Integer(), nullable=True),
            sa.Column("labour_notes", sa.Text(), nullable=True),
            sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_work_orders_status", "work_orders", ["status"])
        op.create_index("ix_work_orders_status_due", "work_orders", ["status", "due_at"])
        op.create_index("ix_work_orders_equipment", "work_orders", ["equipment_id"])
        op.create_index("ix_work_orders_assignee", "work_orders", ["assignee_id"])
        op.create_index("ix_work_orders_created", "work_orders", ["created_at"])

    if not _has_table(insp, "work_order_comments"):
        op.create_table(
            "work_order_comments",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("work_order_id", sa.Text(), sa.ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("author", sa.Text(), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_work_order_comments_wo", "work_order_comments", ["work_order_id"])

    if not _has_table(insp, "work_order_attachments"):
        op.create_table(
            "work_order_attachments",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("work_order_id", sa.Text(), sa.ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("content_type", sa.Text(), nullable=True),
            sa.Column("size", sa.Integer(), nullable=True),
            sa.Column("storage_path", sa.Text(), nullable=True),
            sa.Column("uploaded_by", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_work_order_attachments_wo", "work_order_attachments", ["work_order_id"])

    if not _has_table(insp, "work_order_labour"):
        op.create_table(
            "work_order_labour",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("work_order_id", sa.Text(), sa.ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("author", sa.Text(), nullable=True),
            sa.Column("minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("worked_on", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_work_order_labour_wo", "work_order_labour", ["work_order_id"])

    if not _has_table(insp, "auto_work_order_rules"):
        op.create_table(
            "auto_work_order_rules",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("equipment_id", sa.Text(), sa.ForeignKey("equipment.id", ondelete="CASCADE"), nullable=True),
            sa.Column("parameter_key", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("priority", sa.Text(), nullable=False, server_default="medium"),
            sa.Column("title_template", sa.Text(), nullable=True),
            sa.Column("description_template", sa.Text(), nullable=True),
            sa.Column("assignee_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="1440"),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_auto_wo_rules_eq", "auto_work_order_rules", ["equipment_id"])


def downgrade() -> None:
    for table in (
        "auto_work_order_rules", "work_order_labour", "work_order_attachments",
        "work_order_comments", "work_orders", "api_keys", "session_tokens",
        "users", "roles", "areas", "sites",
    ):
        op.drop_table(table)
