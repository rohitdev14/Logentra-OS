from __future__ import annotations

from pathlib import Path

from alembic import command
from sqlalchemy import inspect, text

from logentra_server.database import alembic_config, make_engine, run_migrations

ROOT = Path(__file__).resolve().parents[1]
V03_SQL = ROOT / "migrations" / "versions" / "0001_initial.sql"


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
    return out


def test_alembic_upgrade_from_empty(tmp_path):
    url = f"sqlite:///{tmp_path}/fresh.db"
    engine = make_engine(url)
    run_migrations(engine)
    tables = set(inspect(engine).get_table_names())
    for needed in ("equipment", "readings", "users", "roles", "work_orders", "session_tokens", "api_keys", "sites", "documents", "approval_routes"):
        assert needed in tables, needed
    cols = {c["name"] for c in inspect(engine).get_columns("readings")}
    assert "out_of_range" in cols
    cols = {c["name"] for c in inspect(engine).get_columns("equipment")}
    assert "site_id" in cols and "area_id" in cols
    cols = {c["name"] for c in inspect(engine).get_columns("audit_log")}
    assert "user_id" in cols


def test_alembic_upgrade_from_v03(tmp_path):
    url = f"sqlite:///{tmp_path}/v03.db"
    engine = make_engine(url)
    cfg = alembic_config(url)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001_initial")
    tables = set(inspect(engine).get_table_names())
    assert "equipment" in tables
    assert "users" not in tables
    with engine.begin() as conn:
        cfg = alembic_config(url)
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    tables = set(inspect(engine).get_table_names())
    assert "users" in tables and "work_orders" in tables and "documents" in tables
    cols = {c["name"] for c in inspect(engine).get_columns("readings")}
    assert "out_of_range" in cols


def test_alembic_stamp_legacy_v03_file(tmp_path):
    """A pre-Alembic v0.3 database (tables exist, no alembic_version) is stamped then upgraded."""
    url = f"sqlite:///{tmp_path}/legacy.db"
    engine = make_engine(url)
    sql = V03_SQL.read_text(encoding="utf-8")
    with engine.begin() as conn:
        for stmt in _statements(sql):
            conn.execute(text(stmt))
        conn.execute(text(
            "INSERT INTO equipment (id, tag, created_at, updated_at) VALUES ('eq_1', 'LEG-1', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        ))
    assert "alembic_version" not in inspect(engine).get_table_names()
    run_migrations(engine)
    tables = set(inspect(engine).get_table_names())
    assert "users" in tables and "work_orders" in tables and "documents" in tables
    with engine.begin() as conn:
        tag = conn.execute(text("SELECT tag FROM equipment WHERE id='eq_1'")).scalar()
        assert tag == "LEG-1"
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == "0005_outbox"
