#!/usr/bin/env python3
"""Start the Logentra OS local server:  python run.py

Create the first admin (when LOGENTRA_AUTH_MODE=users):
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

from logentra_server.branding import PRODUCT_NAME, RELEASE_LABEL  # noqa: E402
from logentra_server.config import env_value, load_settings  # noqa: E402


def _create_admin(argv: list[str]) -> int:
    from logentra_server.database import init_db, make_engine, make_session_factory
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
    init_db(engine)
    SessionLocal = make_session_factory(engine)
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
    print(f"Created admin user {username!r} ({user_id})")
    print(f"Log in at the UI or POST /api/auth/login  (auth_mode={settings.auth_mode})")
    return 0


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "create-admin":
        raise SystemExit(_create_admin(sys.argv[2:]))

    from logentra_server.app import create_app

    settings = load_settings()
    parser = argparse.ArgumentParser(description=f"{PRODUCT_NAME} {RELEASE_LABEL} local server")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")
    args = parser.parse_args()
    settings.validate_bind(args.host)

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

    if args.reload:
        uvicorn.run("logentra_server.app:create_app", factory=True, host=args.host, port=args.port, reload=True, proxy_headers=False, access_log=False)
    else:
        app = create_app(settings)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info", proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
