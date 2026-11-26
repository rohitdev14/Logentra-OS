from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent


class Base(DeclarativeBase):
    pass


def make_engine(
    url: str,
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    pool_timeout: int = 30,
):
    kwargs = {"future": True, "hide_parameters": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 1800
        kwargs["pool_timeout"] = pool_timeout
        if pool_size is not None:
            kwargs["pool_size"] = pool_size
        if max_overflow is not None:
            kwargs["max_overflow"] = max_overflow
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _rec):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA temp_store=MEMORY")
            cur.execute("PRAGMA cache_size=-64000")
            cur.close()

    return engine


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def alembic_config(url: str | None = None):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    if url:
        # alembic.ini interpolation — keep a driver URL in attributes too
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        cfg.attributes["sqlalchemy_url"] = url
    return cfg


def run_migrations(engine) -> None:
    """Apply Alembic migrations to `engine`.

    Pre-Alembic v0.3 databases (tables exist, no alembic_version) are
    stamped at 0001_initial and then upgraded to head.
    """
    from alembic import command

    url = engine.url.render_as_string(hide_password=False)
    # SQLAlchemy may render sqlite without the triple-slash form; keep the original if possible
    if not url or url.startswith("sqlite:"):
        # prefer the raw URL string we created the engine with
        url = str(engine.url)

    lock_conn = None
    if engine.dialect.name == "postgresql":
        lock_conn = engine.connect()
        lock_conn.execute(text("SELECT pg_advisory_lock(73920146)"))
        lock_conn.commit()
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        cfg = alembic_config(url)
        if "alembic_version" not in tables and "equipment" in tables:
            with engine.begin() as conn:
                cfg.attributes["connection"] = conn
                command.stamp(cfg, "0001_initial")

        cfg = alembic_config(url)
        with engine.connect() as conn:
            if conn.dialect.name == "sqlite":
                # Alembic rebuilds the parent table during batch alters. Keep its children.
                conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            cfg.attributes["connection"] = conn
            try:
                command.upgrade(cfg, "head")
                if conn.dialect.name == "sqlite" and conn.exec_driver_sql("PRAGMA foreign_key_check").first():
                    raise RuntimeError("Migration left invalid SQLite foreign keys")
                conn.commit()
            finally:
                if conn.dialect.name == "sqlite":
                    conn.rollback()
                    conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    finally:
        if lock_conn is not None:
            try:
                lock_conn.execute(text("SELECT pg_advisory_unlock(73920146)"))
                lock_conn.commit()
            finally:
                lock_conn.close()


def init_db(engine) -> None:
    from logentra_server import models  # noqa: F401 — register models

    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(text("SELECT 1"))
