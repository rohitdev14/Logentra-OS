# Packaging Logbook v0.3 as a desktop app

## 1. PyInstaller (recommended first installer)

From the repository root, after `pip install pyinstaller`:

```bash
pyinstaller --noconfirm --clean --name Logbook \
  --onefile \
  --add-data "ui/Logbook.html:ui" \
  --add-data "ui/index.html:ui" \
  --add-data "docs/schema.sql:docs" \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import sqlalchemy.dialects.sqlite \
  --hidden-import sqlalchemy.dialects.postgresql \
  run.py
```

On Windows the `--add-data` separator is `;` instead of `:`.

Then, in `run.py` / `config.py`, resolve `ROOT` with `sys._MEIPASS` when frozen and write `logbook.db` under a user-writable directory (for example `%APPDATA%/Logbook` or `~/Library/Application Support/Logbook`). A small NSIS, Inno Setup, or `cargo bundle`-style installer can drop the exe, a Start Menu shortcut, and the VC++ redistributable.

Typical size: tens of megabytes. First start creates the SQLite file.

## 2. Tauri sidecar (nicer window)

- Build the PyInstaller binary as a sidecar.
- A Tauri (Rust) shell starts the sidecar, waits for `GET /api/health`, and opens the UI in a WebView.
- Optional: leave the sidecar running in the tray so MQTT and the API stay up after the window is closed.
- The sidecar still owns the database; Grafana and other tools keep using `http://127.0.0.1:8787/api`.

pywebview is a lighter alternative if you want to stay in Python only.

## 3. Docker (always-on)

A two-service compose file (app + Postgres) is enough for a small plant server. Disable `open_browser`, set `LOGBOOK_API_KEY`, and publish port 8787 on the plant LAN only.
