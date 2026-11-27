from __future__ import annotations

from pathlib import Path

import pytest
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
    wo_indexes = {ix["name"] for ix in inspect(engine).get_indexes("work_orders")}
    assert "ix_work_orders_site_area" in wo_indexes
    reading_indexes = {ix["name"] for ix in inspect(engine).get_indexes("readings")}
    assert "ix_readings_eq_ts" in reading_indexes
    with engine.begin() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == "0011_site_timezone"


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
        assert version == "0011_site_timezone"


def test_qr_migration_round_trip(tmp_path):
    url = f"sqlite:///{tmp_path}/qr.db"
    engine = make_engine(url)
    run_migrations(engine)
    schema = inspect(engine)
    assert {"qr_entries", "qr_retired_codes", "reading_photos"} <= set(schema.get_table_names())
    assert {"qr_code", "qr_code_issued_at"} <= {col["name"] for col in schema.get_columns("equipment")}
    with engine.begin() as conn:
        cfg = alembic_config(url)
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0007_performance")
    schema = inspect(engine)
    assert "reading_photos" not in schema.get_table_names()
    assert "qr_code" not in {col["name"] for col in schema.get_columns("equipment")}
    run_migrations(engine)
    assert "reading_photos" in inspect(engine).get_table_names()


def test_postgres_shift_constraints_and_downgrade():
    import os
    from uuid import uuid4

    import pytest
    from sqlalchemy import create_engine, make_url
    from sqlalchemy.exc import DBAPIError

    source = os.getenv("LOGENTRA_TEST_POSTGRES")
    if not source:
        pytest.skip("Set LOGENTRA_TEST_POSTGRES for database trigger verification")
    name = "shift_" + uuid4().hex
    admin = create_engine(source, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as conn:
        conn.exec_driver_sql(f"CREATE DATABASE {name}")
    engine = make_engine(make_url(source).set(database=name).render_as_string(hide_password=False))
    try:
        run_migrations(engine)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO sites (id,name,created_at) VALUES ('site','Northbrook Works','2026')"))
            conn.execute(text("INSERT INTO shift_patterns VALUES ('pattern','site','Utilities','UTC','2026','2026')"))
            conn.execute(text("INSERT INTO shift_definitions VALUES ('definition','pattern','A','Day','07:00',720,0)"))
            conn.execute(text("INSERT INTO crews VALUES ('crew','site','Crew A')"))
            conn.execute(text("""INSERT INTO shifts (id,site_id,shift_definition_id,date,starts_at,ends_at,crew_id,status)
                              VALUES ('shift','site','definition','2026-11-25','2026-11-25T07:00:00Z',
                                      '2026-11-25T19:00:00Z','crew','active')"""))
            conn.execute(text("""INSERT INTO shift_log_entries
                              (id,shift_id,event_at,recorded_at,category_id,severity,title,body,author_name,version)
                              VALUES ('entry','shift','2026','2026','shift_cat_1','info','Pump inspection','','Operator',1)"""))
            conn.execute(text("""INSERT INTO handovers
                              (id,outgoing_shift_id,site_id,version,status,summary,signed_off_at,snapshot_json,snapshot_sha256)
                              VALUES ('handover','shift','site',1,'signed_off','','2026','{}','hash')"""))
        for statement, message in (
            ("UPDATE shift_log_entries SET title='Changed' WHERE id='entry'", "append-only"),
            ("DELETE FROM shift_log_entries WHERE id='entry'", "append-only"),
            ("UPDATE handovers SET snapshot_json='changed' WHERE id='handover'", "immutable"),
            ("DELETE FROM handovers WHERE id='handover'", "retained"),
        ):
            with pytest.raises(DBAPIError, match=message), engine.begin() as conn:
                conn.execute(text(statement))
        with engine.begin() as conn:
            cfg = alembic_config(engine.url.render_as_string(hide_password=False))
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0007_performance")
        assert "shift_log_entries" not in inspect(engine).get_table_names()
        assert "reading_photos" not in inspect(engine).get_table_names()
        run_migrations(engine)
        assert "shift_log_entries" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql(f"DROP DATABASE {name} WITH (FORCE)")
        admin.dispose()


def _assert_v071_migration(engine, config):
    from logentra_server.utils import loads

    command.upgrade(config, "0009_shift_handover")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO sites (id, name, created_at) VALUES
            ('northbrook', 'Northbrook Works', '2026'), ('utc', 'Training site', '2026'), ('london', 'Workshop', '2026')
        """))
        conn.execute(text("""
            INSERT INTO shift_patterns (id, site_id, name, timezone, created_at, updated_at) VALUES
            ('sg_day', 'northbrook', 'Day', 'Asia/Singapore', '2026', '2026'),
            ('sg_night', 'northbrook', 'Night', 'Asia/Singapore', '2026', '2026'),
            ('utc_day', 'utc', 'Day', 'UTC', '2026', '2026'),
            ('uk_day', 'london', 'Day', 'Europe/London', '2026', '2026')
        """))
        conn.execute(text("""
            INSERT INTO equipment (id, tag, site_id, created_at, updated_at) VALUES
            ('pump', 'P-101', 'northbrook', '2026', '2026'), ('tank', 'TK-03', 'utc', '2026', '2026'),
            ('ct', 'CT-2', 'london', '2026', '2026')
        """))
        conn.execute(text("""
            INSERT INTO readings (id, entry_id, equipment_id, parameter_key, ts, value, source, created_at) VALUES
            ('manual', 'm', 'pump', 'vibration', '2026-11-26T10:30:00', 8, 'manual', '2026'),
            ('qr', 'q', 'pump', 'vibration', '2026-11-26T02:30:00', 8, 'qr', '2026'),
            ('api', 'a', 'pump', 'vibration', '2026-11-26T10:30:00', 8, 'api', '2026'),
            ('utc', 'u', 'tank', 'level', '2026-11-26T10:30:00', 2, 'demo', '2026'),
            ('fold', 'f', 'ct', 'flow', '2026-10-25T01:30:00', 2, 'import', '2026'),
            ('gap', 'g', 'ct', 'flow', '2026-03-29T01:30:00', 2, 'import', '2026')
        """))
        conn.execute(text("""
            INSERT INTO records (id, equipment_id, created_at, updated_at) VALUES ('signed', 'pump', '2026', '2026')
        """))
        conn.execute(text("""
            INSERT INTO signoffs (id, record_id, name, signed_at) VALUES ('sign', 'signed', 'Amir Rahman', '2026-11-26')
        """))
        conn.execute(text("""
            INSERT INTO record_items (id, record_id, label) VALUES ('item', 'signed', 'Inspect coupling guard')
        """))
        conn.execute(text("""
            INSERT INTO roles (id, name, permissions, created_at, updated_at)
            VALUES ('custom', 'Engineering review', '["documents.approve"]', '2026', '2026')
        """))
    run_migrations(engine, timezone="America/Chicago")
    with engine.begin() as conn:
        assert dict(conn.execute(text("SELECT id, ts FROM readings")).all()) == {
            "manual": "2026-11-26T02:30:00", "qr": "2026-11-26T02:30:00", "api": "2026-11-26T10:30:00",
            "utc": "2026-11-26T10:30:00", "fold": "2026-10-25T00:30:00", "gap": "2026-03-29T01:30:00",
        }
        assert conn.execute(text("SELECT timezone FROM sites WHERE id='northbrook'")).scalar() == "Asia/Singapore"
        assert conn.execute(text("SELECT timezone FROM sites WHERE id='utc'")).scalar() == "UTC"
        assert conn.execute(text("SELECT status, approved_by, approved_at FROM records")).one() == (
            "approved", "Amir Rahman", "2026-11-26",
        )
        assert conn.execute(text("SELECT id FROM record_items")).scalar() == "item"
        permissions = conn.execute(text("SELECT permissions FROM roles WHERE id='custom'")).scalar()
        assert loads(permissions) == ["documents.approve", "records.approve"]
        actor, entity, detail = conn.execute(text("""
            SELECT actor, entity_type, detail FROM audit_log WHERE action='readings.timezone_backfill'
        """)).one()
        assert (actor, entity, loads(detail)) == ("migration", "reading", {"Asia/Singapore": 1, "Europe/London": 2})
        conn.execute(text("UPDATE sites SET timezone='UTC' WHERE id='northbrook'"))
        conn.execute(text("""
            INSERT INTO readings (id, entry_id, equipment_id, parameter_key, ts, value, source, created_at)
            VALUES ('new', 'n', 'pump', 'vibration', '2026-11-26T02:30:00', 8, 'manual', '2026')
        """))
    command.downgrade(config, "0009_shift_handover")
    with engine.connect() as conn:
        assert dict(conn.execute(text("SELECT id, ts FROM readings")).all()) == {
            "manual": "2026-11-26T10:30:00", "qr": "2026-11-26T02:30:00", "api": "2026-11-26T10:30:00",
            "utc": "2026-11-26T10:30:00", "new": "2026-11-26T02:30:00",
            "fold": "2026-10-25T01:30:00", "gap": "2026-03-29T01:30:00",
        }
        assert conn.execute(text("SELECT name FROM signoffs")).scalar() == "Amir Rahman"
        assert conn.execute(text("SELECT id FROM record_items")).scalar() == "item"
        assert loads(conn.execute(text("SELECT permissions FROM roles WHERE id='custom'")).scalar()) == ["documents.approve"]
    assert "status" not in {col["name"] for col in inspect(engine).get_columns("records")}
    assert "timezone" not in {col["name"] for col in inspect(engine).get_columns("sites")}


def test_v071_migration_preserves_legacy_evidence_on_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("LOGENTRA_TIMEZONE", raising=False)
    url = f"sqlite:///{tmp_path}/v071.db"
    engine = make_engine(url)
    try:
        _assert_v071_migration(engine, alembic_config(url))
    finally:
        engine.dispose()


def test_v071_migration_preserves_legacy_evidence_on_postgres(monkeypatch):
    import os
    from uuid import uuid4

    import pytest
    from sqlalchemy import create_engine, make_url

    source = os.getenv("LOGENTRA_TEST_POSTGRES")
    if not source:
        pytest.skip("Set LOGENTRA_TEST_POSTGRES for record approval and timezone migration verification")
    monkeypatch.delenv("LOGENTRA_TIMEZONE", raising=False)
    name = "v071_" + uuid4().hex
    admin = create_engine(source, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as conn:
        conn.exec_driver_sql(f"CREATE DATABASE {name}")
    url = make_url(source).set(database=name).render_as_string(hide_password=False)
    engine = make_engine(url)
    try:
        _assert_v071_migration(engine, alembic_config(url))
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql(f"DROP DATABASE {name} WITH (FORCE)")
        admin.dispose()


def _seed_site_without_shift_pattern(engine):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO sites (id, name, created_at) VALUES ('site_northbrook', 'Northbrook Works', '2026')"))
        conn.execute(text("""
            INSERT INTO equipment (id, tag, site_id, created_at, updated_at)
            VALUES ('pump', 'P-101', 'site_northbrook', '2026', '2026')
        """))
        conn.execute(text("""
            INSERT INTO readings (id, entry_id, equipment_id, parameter_key, ts, value, source, created_at)
            VALUES ('manual', 'entry', 'pump', 'vibration', '2026-11-26T16:30:00', 8, 'manual', '2026')
        """))


@pytest.mark.parametrize("timezone", ["UTC", "Etc/UTC", "GMT"])
def test_site_timezone_backfill_leaves_utc_install_site_unconfigured(tmp_path, monkeypatch, timezone):
    from fastapi.testclient import TestClient

    from logentra_server.app import create_app
    from logentra_server.config import Settings

    monkeypatch.delenv("LOGENTRA_TIMEZONE", raising=False)
    url = f"sqlite:///{tmp_path / 'unconfigured.db'}"
    engine = make_engine(url)
    try:
        command.upgrade(alembic_config(url), "0009_shift_handover")
        _seed_site_without_shift_pattern(engine)
        app = create_app(Settings(database_url=url, timezone=timezone, auth_mode="users",
                                  bootstrap_admin_username="admin", bootstrap_admin_password="synthetic-migration"))
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": "admin", "password": "synthetic-migration"})
            assert login.status_code == 200, login.text
            bootstrap = client.get("/api/bootstrap", headers={"Authorization": "Bearer " + login.json()["token"]})
            assert bootstrap.status_code == 200, bootstrap.text
            assert bootstrap.json()["meta"]["sitesMissingTimezone"] == ["site_northbrook"]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT timezone FROM sites")).scalar_one() is None
            assert conn.execute(text("SELECT ts FROM readings")).scalar_one() == "2026-11-26T16:30:00"
            assert conn.execute(text("""
                SELECT detail FROM audit_log WHERE action = 'readings.timezone_backfill'
            """)).scalar_one() == "{}"
    finally:
        engine.dispose()


def test_site_timezone_backfill_uses_settings_without_environment(tmp_path, monkeypatch):
    from logentra_server.app import create_app
    from logentra_server.config import load_settings

    monkeypatch.delenv("LOGENTRA_TIMEZONE", raising=False)
    for use_cli in (False, True):
        url = f"sqlite:///{tmp_path / f'no_shift_{use_cli}.db'}"
        config_path = tmp_path / "config.yaml"
        config_path.write_text(f"database_url: {url}\ntimezone: Asia/Singapore\n")
        monkeypatch.setenv("LOGENTRA_CONFIG", str(config_path))
        monkeypatch.delenv("LOGENTRA_DATABASE_URL", raising=False)
        monkeypatch.delenv("LOGBOOK_DATABASE_URL", raising=False)
        engine = make_engine(url)
        try:
            command.upgrade(alembic_config(url), "0010_record_approval")
            _seed_site_without_shift_pattern(engine)
            if use_cli:
                command.upgrade(alembic_config(), "head")
            else:
                app = create_app(load_settings())
                app.state.engine.dispose()
            with engine.connect() as conn:
                assert conn.execute(text("SELECT timezone FROM sites")).scalar_one() == "Asia/Singapore"
                assert conn.execute(text("SELECT ts FROM readings")).scalar_one() == "2026-11-26T08:30:00"
        finally:
            engine.dispose()
