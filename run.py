#!/usr/bin/env python3
"""Start the Logentra OS local server:  python run.py

Create the first admin (when LOGENTRA_AUTH_MODE=users):
    python run.py create-admin --username admin --password '...'
Apply or check database migrations:
    python run.py migrate [--check]
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logentra_server.branding import PRODUCT_NAME, RELEASE_LABEL  # noqa: E402
from logentra_server.config import Settings, load_settings  # noqa: E402
from logentra_server.database import (  # noqa: E402
    MigrationRequired, make_engine, make_session_factory, migration_revisions,
    prepare_database, require_current_revision, run_migrations,
)


def _migrate(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run.py migrate", description="Apply or check database migrations")
    parser.add_argument("--check", action="store_true", help="Check the database revision without upgrading")
    args = parser.parse_args(argv)
    settings = load_settings()
    url = settings.sqlalchemy_url
    # Inspection can use a plain connection and leave SQLite's journal mode alone.
    engine = create_engine(url, hide_parameters=True) if args.check else make_engine(url)
    try:
        if not args.check:
            run_migrations(engine, timezone=settings.timezone)
        current, head = migration_revisions(engine)
    finally:
        engine.dispose()
    print(f"Current revision: {', '.join(current) or 'uninitialized'}")
    if args.check:
        print(f"Head revision: {head}")
    return int(current != (head,))


def _create_admin(argv: list[str]) -> int:
    from logentra_server import seed

    parser = argparse.ArgumentParser(prog="run.py create-admin", description="Create a bootstrap admin user")
    from logentra_server.config import env_value  # noqa: PLC0415
    parser.add_argument("--username", default=env_value("LOGENTRA_BOOTSTRAP_ADMIN_USER", "LOGENTRA_BOOTSTRAP_ADMIN_USERNAME", "LOGBOOK_BOOTSTRAP_ADMIN_USER", "LOGBOOK_BOOTSTRAP_ADMIN_USERNAME") or "admin")
    parser.add_argument("--password", default=env_value("LOGENTRA_BOOTSTRAP_ADMIN_PASSWORD", "LOGBOOK_BOOTSTRAP_ADMIN_PASSWORD") or "")
    parser.add_argument("--display-name", default="")
    parser.add_argument("--email", default="")
    args = parser.parse_args(argv)
    if not args.password:
        import getpass
        args.password = getpass.getpass("Admin password: ")
    if not args.password:
        print("A password is required.", file=sys.stderr)
        return 2
    settings = load_settings()
    engine = make_engine(settings.sqlalchemy_url)
    try:
        prepare_database(engine, settings)
        SessionLocal = make_session_factory(engine, timezone=settings.timezone)
        with SessionLocal() as db:
            try:
                user = seed.create_admin_user(
                    db, username=args.username, password=args.password,
                    display_name=args.display_name or args.username, email=args.email or None,
                )
                username, user_id = user.username, user.id
                db.commit()
            except ValueError as exc:
                db.rollback()
                print(str(exc), file=sys.stderr)
                return 1
    finally:
        engine.dispose()
    print(f"Created admin user {username!r} ({user_id})")
    print(f"Log in at the UI or POST /api/auth/login  (auth_mode={settings.auth_mode})")
    return 0


def default_workers(settings) -> int:
    """Production on PostgreSQL: 3 workers with 4+ CPUs, otherwise 2.

    Measured on an 8-CPU host running PostgreSQL and nginx alongside the app:
    3 workers gave the best tail latency; 4 left too little CPU for the database.
    """
    if settings.profile != "production" or settings.sqlalchemy_url.startswith("sqlite"):
        return 1
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 2
    return 3 if cpus >= 4 else 2


def _run_worker(settings: Settings) -> None:
    from logentra_server.app import background_jobs

    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    engine = make_engine(settings.sqlalchemy_url)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        prepare_database(engine, settings)
        session_factory = make_session_factory(engine, timezone=settings.timezone)
        with background_jobs(settings, session_factory):
            while not stopping:
                time.sleep(0.25)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
        engine.dispose()


def _serve() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description=f"{PRODUCT_NAME} {RELEASE_LABEL} local server")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")
    args = parser.parse_args()
    if settings.role == "worker":
        _run_worker(settings)
        return

    settings.validate_bind(args.host)
    workers = settings.workers or default_workers(settings)
    if args.reload or workers > 1:
        # The Uvicorn supervisor binds before starting its child processes.
        if settings.auto_migrate == "off":
            engine = make_engine(settings.sqlalchemy_url)
            try:
                require_current_revision(engine)
            finally:
                engine.dispose()
    else:
        from logentra_server.app import create_app

        app = create_app(settings)

    from logentra_server.config import env_value
    flag = env_value("LOGENTRA_OPEN_BROWSER", "LOGBOOK_OPEN_BROWSER", default="1")
    open_browser = settings.open_browser and not args.no_browser and flag.lower() not in ("0", "false", "no")
    url = f"http://{args.host}:{args.port}/"
    docs = f"http://{args.host}:{args.port}/docs"
    print(f"{PRODUCT_NAME} {RELEASE_LABEL}  {url}")
    print(f"API docs       {docs}")
    backend = settings.database_url.split(":", 1)[0]
    print(f"Database       {backend}")
    print(f"Auth mode      {settings.auth_mode}")

    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    import uvicorn

    common = {"host": args.host, "port": args.port, "proxy_headers": False, "access_log": False, "timeout_keep_alive": 30}
    if args.reload:
        uvicorn.run("logentra_server.app:create_app", factory=True, reload=True, **common)
    elif workers > 1:
        print(f"Workers        {workers}")
        uvicorn.run("logentra_server.app:create_app", factory=True, workers=workers, log_level="info", **common)
    else:
        uvicorn.run(app, log_level="info", **common)


def main() -> None:
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "migrate":
            raise SystemExit(_migrate(sys.argv[2:]))
        if len(sys.argv) > 1 and sys.argv[1] == "create-admin":
            raise SystemExit(_create_admin(sys.argv[2:]))
        _serve()
    except MigrationRequired as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
