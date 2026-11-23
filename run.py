#!/usr/bin/env python3
"""Start the Logbook local server:  python run.py

Create the first admin (when LOGBOOK_AUTH_MODE=users):
    python run.py create-admin --username admin --password '...'
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logbook_server.branding import PRODUCT_NAME, RELEASE_LABEL  # noqa: E402
from logbook_server.config import load_settings  # noqa: E402


def _create_admin(argv: list[str]) -> int:
    from logbook_server.database import init_db, make_engine, make_session_factory
    from logbook_server import seed

    parser = argparse.ArgumentParser(prog="run.py create-admin", description="Create a bootstrap admin user")
    parser.add_argument("--username", default=os.environ.get("LOGBOOK_BOOTSTRAP_ADMIN_USER") or "admin")
    parser.add_argument("--password", default=os.environ.get("LOGBOOK_BOOTSTRAP_ADMIN_PASSWORD") or "")
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
    init_db(engine)
    SessionLocal = make_session_factory(engine)
    with SessionLocal() as db:
        try:
            user = seed.create_admin_user(
                db, username=args.username, password=args.password,
                display_name=args.display_name or args.username, email=args.email or None,
            )
            db.commit()
        except ValueError as exc:
            db.rollback()
            print(str(exc), file=sys.stderr)
            return 1
    print(f"Created admin user {user.username!r} ({user.id})")
    print(f"Log in at the UI or POST /api/auth/login  (auth_mode={settings.auth_mode})")
    return 0


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "create-admin":
        raise SystemExit(_create_admin(sys.argv[2:]))

    from logbook_server.app import create_app

    settings = load_settings()
    parser = argparse.ArgumentParser(description=f"{PRODUCT_NAME} {RELEASE_LABEL} local server")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")
    args = parser.parse_args()

    open_browser = settings.open_browser and not args.no_browser and os.environ.get("LOGBOOK_OPEN_BROWSER", "1") not in ("0", "false", "no")
    url = f"http://{args.host}:{args.port}/"
    docs = f"http://{args.host}:{args.port}/docs"
    print(f"{PRODUCT_NAME} {RELEASE_LABEL}  {url}")
    print(f"API docs       {docs}")
    print(f"Database       {settings.sqlalchemy_url}")
    print(f"Auth mode      {settings.auth_mode}")

    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    import uvicorn

    if args.reload:
        uvicorn.run("logbook_server.app:create_app", factory=True, host=args.host, port=args.port, reload=True)
    else:
        app = create_app(settings)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
