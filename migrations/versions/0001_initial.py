"""Initial v0.3 schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-23
"""
from __future__ import annotations

from pathlib import Path

from alembic import op
from sqlalchemy import inspect, text

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_name("0001_initial.sql")


def _statements(sql: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for raw in sql.splitlines():
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        buf.append(raw)
        if line.endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";")
            if stmt:
                out.append(stmt)
            buf = []
    if buf:
        stmt = "\n".join(buf).strip().rstrip(";")
        if stmt:
            out.append(stmt)
    return out


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if insp.has_table("equipment") and insp.has_table("readings"):
        # Existing v0.3 database being stamped / upgraded in place.
        return
    sql = SQL_FILE.read_text(encoding="utf-8")
    for stmt in _statements(sql):
        bind.execute(text(stmt))


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "settings", "audit_log", "webhook_deliveries", "outbound_webhooks",
        "annotations", "readings", "punches", "signoffs", "record_values",
        "record_items", "record_columns", "records", "template_items",
        "template_columns", "templates", "parameters", "param_presets",
        "equipment_field_values", "equipment", "field_definitions",
    ):
        bind.execute(text(f"DROP TABLE IF EXISTS {table}"))
