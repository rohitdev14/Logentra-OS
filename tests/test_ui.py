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
    from logentra_server.app import create_app
    from logentra_server.config import Settings

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
    port = int(os.environ.get("LOGENTRA_TEST_PORT") or os.environ.get("LOGBOOK_TEST_PORT", "8799"))
    server = _start_server(tmp / "ui.db", port)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def test_ui_smoke_and_swagger_screenshot(live_url):
    from playwright.sync_api import sync_playwright

    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("LOGENTRA_TEST_BROWSER_CHANNEL"), headless=True, args=["--disable-gpu"])
        page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)
        page.on("response", lambda res: print("HTTP", res.status, res.url) if res.status >= 400 else None)
        page.on("dialog", lambda d: d.accept())

        docs_img = SHOT / "docs"
        docs_img.mkdir(parents=True, exist_ok=True)

        def hide_toast():
            page.evaluate("() => { const t=document.getElementById('toast'); if(t) t.style.display='none'; }")

        def set_theme(theme):
            page.evaluate(
                """(t) => { localStorage.setItem('logentra.theme', t); document.documentElement.setAttribute('data-theme', t); if (window.LogbookTheme) LogbookTheme.apply(t); }""",
                theme,
            )
            page.wait_for_timeout(150)

        def shot(name):
            hide_toast()
            dest = SHOT / name
            page.screenshot(path=str(dest), full_page=False, clip={"x": 0, "y": 0, "width": 1440, "height": 900})
            return dest

        page.goto(live_url + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#view")
        page.wait_for_function("() => window.Logbook && window.LogbookAPI")
        page.get_by_role("button", name="Load fictional demo").click()
        page.wait_for_function(
            "() => window.Logbook && window.Logbook.getState && window.Logbook.getState().equipment.length > 0",
            timeout=20000,
        )
        page.wait_for_timeout(400)
        hide_toast()
        assert page.get_by_role("heading", name="Dashboard").is_visible()
        assert page.get_by_text("Northbrook Works").count() > 0
        badge = page.locator("#api-badge").inner_text()
        assert "connect" in badge.lower() or "sqlite" in badge.lower()

        # Theme toggle: click, persist in localStorage, survive reload
        initial = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        assert initial in ("light", "dark")
        page.locator("#theme-toggle").click()
        page.wait_for_timeout(200)
        toggled = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        assert toggled in ("light", "dark") and toggled != initial
        stored = page.evaluate("() => localStorage.getItem('logentra.theme') || localStorage.getItem('logbook.theme')")
        assert stored == toggled
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#view")
        page.wait_for_function("() => window.Logbook && window.LogbookAPI")
        page.wait_for_timeout(400)
        assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == toggled
        # switch back so the first themed shots start from a known state
        set_theme("light")
        page.wait_for_function("() => window.Logbook && window.Logbook.getState && window.Logbook.getState().equipment.length")
        hide_toast()

        async_goto = """(view, extra) => {
          const p = Logbook.goto(view, extra || {});
          return p && p.then ? p : Promise.resolve();
        }"""

        def capture_suite(theme):
            set_theme(theme)
            page.evaluate(async_goto, "dashboard")
            page.wait_for_timeout(500)
            hide_toast()
            assert page.get_by_role("heading", name="Dashboard").is_visible()
            shot("01-dashboard.png" if theme == "light" else "01-dashboard-dark.png")
            page.screenshot(path=str(docs_img / f"dashboard-{theme}.png"), full_page=False, clip={"x":0,"y":0,"width":1440,"height":900})

            page.evaluate(async_goto, "equipment")
            page.wait_for_selector("h2")
            page.wait_for_timeout(300)
            hide_toast()
            assert page.get_by_role("heading", name="Equipment").is_visible()
            page.screenshot(path=str(docs_img / f"equipment-{theme}.png"), full_page=False, clip={"x":0,"y":0,"width":1440,"height":900})

            page.evaluate("""() => Logbook.goto('trends', { logEq: 'eq_p101', trendParam: 'vibration', from: '', to: '' })""")
            page.wait_for_selector("#chart svg")
            page.wait_for_timeout(300)
            hide_toast()
            shot("02-trends.png" if theme == "light" else "02-trends-dark.png")
            page.screenshot(path=str(docs_img / f"trends-{theme}.png"), full_page=False, clip={"x":0,"y":0,"width":1440,"height":900})

            page.evaluate(async_goto, "workorders")
            page.wait_for_selector("h2")
            page.wait_for_timeout(400)
            hide_toast()
            assert page.get_by_role("heading", name="Work orders").is_visible()
            shot("04-work-orders.png" if theme == "light" else "04-work-orders-dark.png")
            page.screenshot(path=str(docs_img / f"work-orders-{theme}.png"), full_page=False, clip={"x":0,"y":0,"width":1440,"height":900})

            page.evaluate(async_goto, "approvals")
            page.wait_for_selector("h2")
            page.wait_for_timeout(400)
            # open the in-review demo document
            opened = page.locator("button[data-act='doc-open']").first
            if opened.count() > 0:
                opened.click()
                page.wait_for_timeout(700)
            hide_toast()
            page.screenshot(path=str(docs_img / f"approvals-{theme}.png"), full_page=False, clip={"x":0,"y":0,"width":1440,"height":900})

        capture_suite("light")
        capture_suite("dark")
        # stable aliases used in the README
        import shutil
        shutil.copyfile(docs_img / "dashboard-light.png", docs_img / "dashboard.png")
        shutil.copyfile(docs_img / "trends-light.png", docs_img / "trends.png")
        shutil.copyfile(docs_img / "work-orders-light.png", docs_img / "work-orders.png")
        shutil.copyfile(docs_img / "approvals-light.png", docs_img / "approvals.png")
        shutil.copyfile(docs_img / "equipment-light.png", docs_img / "equipment.png")

        page.evaluate("""async () => {
          await LogbookAPI.createUser({ username: "maya", password: "mayapass1", display_name: "Maya Chen", role: "supervisor" });
          await LogbookAPI.createUser({ username: "tom", password: "tompass12", display_name: "Tom Okonkwo", role: "technician" });
          await Logbook.goto('users');
        }""")
        page.wait_for_timeout(800)
        page.wait_for_selector("h2")
        hide_toast()
        assert page.get_by_role("heading", name="Users and roles").is_visible()
        page.screenshot(path=str(SHOT / "05-users.png"), full_page=False, clip={"x":0,"y":0,"width":1440,"height":900})
        page.screenshot(path=str(docs_img / "users.png"), full_page=False, clip={"x":0,"y":0,"width":1440,"height":900})

        page.goto(live_url + "/docs", wait_until="domcontentloaded")
        page.wait_for_selector("#swagger-ui, .swagger-ui, .information-container, h2", timeout=15000)
        page.wait_for_timeout(600)
        page.screenshot(path=str(SHOT / "03-swagger.png"), full_page=True)
        page.screenshot(path=str(docs_img / "api-docs.png"), full_page=True)

        browser.close()

    real = [e for e in errors if "favicon" not in e.lower()
            and "swagger" not in e.lower()
            and "failed to load resource" not in e.lower()]
    assert real == [], real
    for name in ("01-dashboard.png", "02-trends.png", "03-swagger.png", "04-work-orders.png", "05-users.png"):
        assert (SHOT / name).is_file(), name
    img = ROOT / "docs" / "images"
    for name in (
        "dashboard-light.png", "dashboard-dark.png",
        "approvals-light.png", "approvals-dark.png",
        "work-orders-light.png", "work-orders-dark.png",
        "trends-light.png", "trends-dark.png",
        "equipment-light.png", "equipment-dark.png",
    ):
        assert (img / name).is_file(), name


def test_login_screenshot(tmp_path):
    if os.environ.get("PLAYWRIGHT_SKIP") == "1":
        pytest.skip("PLAYWRIGHT_SKIP=1")
    from playwright.sync_api import sync_playwright
    from logentra_server.app import create_app
    from logentra_server.config import Settings
    import uvicorn

    port = int(os.environ.get("LOGENTRA_TEST_PORT_LOGIN") or os.environ.get("LOGBOOK_TEST_PORT_LOGIN", "8801"))
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/login.db",
        open_browser=False, host="127.0.0.1", port=port,
        auth_mode="users",
        bootstrap_admin_username="admin",
        bootstrap_admin_password="adminpass1",
    )
    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel=os.getenv("LOGENTRA_TEST_BROWSER_CHANNEL"), headless=True, args=["--disable-gpu"])
            page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_selector("#login-gate:not([hidden]), #login-form", timeout=10000)
            page.wait_for_timeout(400)
            login_png = SHOT / "06-login.png"
            page.screenshot(path=str(login_png), full_page=True)
            (ROOT / "docs" / "images").mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOT / "login.png"), full_page=True)
            # successful login should reveal the app
            page.fill("#login-user", "admin")
            page.fill("#login-pass", "adminpass1")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_selector("#view h2, nav.nav, #app-shell", timeout=10000)
            page.wait_for_timeout(400)
            assert page.locator("#login-gate").is_hidden() or page.locator("#login-gate[hidden]").count() >= 0
            browser.close()
    finally:
        server.should_exit = True
    assert (SHOT / "06-login.png").is_file()
