#!/usr/bin/env python3
"""Start Logbook v0.3 (API + UI) with one command:  python run.py"""
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

from logbook_server.app import create_app  # noqa: E402
from logbook_server.config import load_settings  # noqa: E402


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Logbook v0.3 local server")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")
    args = parser.parse_args()

    open_browser = settings.open_browser and not args.no_browser and os.environ.get("LOGBOOK_OPEN_BROWSER", "1") not in ("0", "false", "no")
    url = f"http://{args.host}:{args.port}/"
    docs = f"http://{args.host}:{args.port}/docs"
    print(f"Logbook v0.3  {url}")
    print(f"API docs       {docs}")
    print(f"Database       {settings.sqlalchemy_url}")
    print("Logbook v0.3 – early release.")

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
