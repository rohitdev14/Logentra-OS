"""Headless UI smoke test against a live server (skipped if PLAYWRIGHT_SKIP=1)."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SHOT = ROOT / "screenshots"
SHOT.mkdir(exist_ok=True)

pytestmark = pytest.mark.timeout(90) if False else pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _start_server(db_path: Path, port: int):
    from logbook_server.app import create_app
    from logbook_server.config import Settings

    settings = Settings(database_url=f"sqlite:///{db_path}", open_browser=False, host="127.0.0.1", port=port)
    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    return server


@pytest.fixture(scope="module")
def live_url(tmp_path_factory):
    if os.environ.get("PLAYWRIGHT_SKIP") == "1":
        pytest.skip("PLAYWRIGHT_SKIP=1")
    tmp = tmp_path_factory.mktemp("ui")
    port = int(os.environ.get("LOGBOOK_TEST_PORT", "8799"))
    server = _start_server(tmp / "ui.db", port)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def test_ui_smoke_and_swagger_screenshot(live_url):
    from playwright.sync_api import sync_playwright

    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])
        page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)
        page.on("response", lambda res: print("HTTP", res.status, res.url) if res.status >= 400 else None)
        page.on("dialog", lambda d: d.accept())

        page.goto(live_url + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#view")
        page.wait_for_function("() => window.Logbook && window.LogbookAPI")
        # empty dashboard, then load demo via UI
        page.get_by_role("button", name="Load fictional demo").click()
        page.wait_for_timeout(800)
        page.evaluate("() => { const t=document.getElementById('toast'); if(t) t.style.display='none'; }")
        assert page.get_by_role("heading", name="Dashboard").is_visible()
        assert page.get_by_text("Northbrook Works").count() > 0
        badge = page.locator("#api-badge").inner_text()
        assert "connect" in badge.lower() or "sqlite" in badge.lower()
        page.screenshot(path=str(SHOT / "01-dashboard.png"), full_page=True)

        page.evaluate("() => Logbook.goto('trends', { logEq: 'eq_p101', trendParam: 'vibration', from: '', to: '' })")
        page.wait_for_selector("#chart svg")
        page.evaluate("() => { const t=document.getElementById('toast'); if(t) t.style.display='none'; }")
        page.screenshot(path=str(SHOT / "02-trends.png"), full_page=True)

        page.goto(live_url + "/docs", wait_until="domcontentloaded")
        page.wait_for_selector("#swagger-ui, .swagger-ui, .information-container, h2", timeout=15000)
        page.wait_for_timeout(600)
        page.screenshot(path=str(SHOT / "03-swagger.png"), full_page=True)

        browser.close()

    real = [e for e in errors if "favicon" not in e.lower()
            and "swagger" not in e.lower()
            and "failed to load resource" not in e.lower()]
    assert real == [], real
    for name in ("01-dashboard.png", "02-trends.png", "03-swagger.png"):
        assert (SHOT / name).is_file(), name
