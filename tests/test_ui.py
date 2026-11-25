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
            page.screenshot(path=str(SHOT / "login.png"), full_page=True)
            # a throttled sign-in shows a friendly wait message and disables the button
            page.route("**/api/auth/login", lambda route: route.fulfill(
                status=429, headers={"Retry-After": "3", "Content-Type": "application/json"},
                body='{"detail":"Too many sign-in attempts. Please wait 3 seconds and try again."}'))
            page.fill("#login-user", "admin")
            page.fill("#login-pass", "adminpass1")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_function(
                "document.querySelector('#login-error').textContent.includes('Please wait')", timeout=5000)
            assert page.get_by_role("button", name="Sign in").is_disabled()
            page.unroute("**/api/auth/login")
            page.wait_for_function("!document.querySelector('#login-form button[type=submit]').disabled", timeout=8000)
            # successful login should reveal the app
            page.fill("#login-user", "admin")
            page.fill("#login-pass", "adminpass1")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_selector("#view h2, nav.nav, #app-shell", timeout=10000)
            page.wait_for_timeout(400)
            page.locator("#login-gate").wait_for(state="hidden", timeout=10000)
            base = f"http://127.0.0.1:{port}"
            headers = {"Authorization": "Bearer " + page.evaluate("LogbookAPI.getToken()")}
            equipment = page.request.post(base + "/api/equipment", headers=headers, data={
                "id":"eq_login", "values":{"fld_tag":"P-102"},
            })
            assert equipment.ok, equipment.text()
            issued = page.request.post(base + "/api/equipment/qr-codes", headers=headers, data={"ids":["eq_login"]})
            code = issued.json()["codes"]["eq_login"]
            phone = browser.new_page(viewport={"width":390,"height":844},is_mobile=True,has_touch=True)
            phone.goto(base + "/q/" + code)
            phone.get_by_role("heading",name="Sign in to log readings").wait_for()
            (SHOT / "v0.7").mkdir(exist_ok=True)
            phone.screenshot(path=str(SHOT / "v0.7" / "qr-phone-login.png"),full_page=True)
            phone.get_by_label("Username").fill("admin")
            phone.get_by_label("Password").fill("adminpass1")
            phone.get_by_role("button",name="Sign in",exact=True).click()
            phone.get_by_role("form",name="Quick entry").wait_for()
            page.get_by_role("button",name="Profile",exact=True).click()
            page.locator("#qr-own-sessions").get_by_role("button",name="Sign out").click()
            phone.reload()
            phone.get_by_role("heading",name="Sign in to log readings").wait_for()
            browser.close()
    finally:
        server.should_exit = True
    assert (SHOT / "06-login.png").is_file()


def test_browser_retains_catalog_edit_on_conflict(live_url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("LOGENTRA_TEST_BROWSER_CHANNEL"), headless=True)
        page = browser.new_page()
        seeded = page.request.post(live_url + "/api/records", data={"id": "browser-conflict", "notes": "original"})
        assert seeded.ok
        original = seeded.json()
        page.goto(live_url)
        page.wait_for_function("() => window.Logbook && Logbook.getState().records.some(r => r.id === 'browser-conflict')")
        edit = page.request.post(live_url + "/api/records", data={
            "id": original["id"], "revision": original["revision"], "notes": "committed elsewhere",
        })
        assert edit.ok
        page.evaluate("""() => {
            Logbook.getState().records.find(r => r.id === 'browser-conflict').notes = 'local draft';
            Logbook.persist();
        }""")
        page.get_by_role("status").get_by_text("Save conflict · changes unsaved").wait_for()
        assert page.evaluate("Logbook.getState().records.find(r => r.id === 'browser-conflict').notes") == "local draft"
        rows = page.request.get(live_url + "/api/records").json()
        assert next(record for record in rows if record["id"] == original["id"])["notes"] == "committed elsewhere"
        browser.close()


def test_scoped_technician_creates_and_edits_record_in_chromium(tmp_path):
    import socket

    from fastapi.testclient import TestClient
    from playwright.sync_api import sync_playwright
    from logentra_server.app import create_app
    from logentra_server.config import Settings

    if os.environ.get("PLAYWRIGHT_SKIP") == "1":
        pytest.skip("PLAYWRIGHT_SKIP=1")
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/scoped-browser.db", profile="production",
                              bootstrap_admin_username="admin", bootstrap_admin_password="synthetic-scoped-browser"))
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        token = client.post("/api/auth/login", json={
            "username": "admin", "password": "synthetic-scoped-browser",
        }).json()["token"]
        admin = {"Authorization": "Bearer " + token}
        assert client.post("/api/sites", headers=admin, json={
            "id": "browser-site", "name": "Browser site", "areas": [{"id": "browser-zone", "name": "Zone"}],
        }).status_code == 201
        assert client.post("/api/users", headers=admin, json={
            "username": "scoped-browser", "password": "synthetic-scoped-browser", "role": "technician",
            "site_id": "browser-site", "area_id": "browser-zone",
        }).status_code == 201
        for item in (
            {"id": "browser-allowed", "values": {"fld_tag": "BR-ALLOWED"}, "site_id": "browser-site", "area_id": "browser-zone"},
            {"id": "browser-denied", "values": {"fld_tag": "BR-DENIED"}},
        ):
            assert client.post("/api/equipment", headers=admin, json=item).status_code == 201
        assert client.post("/api/templates", headers=admin, json={
            "id": "browser-template", "name": "Scoped checklist", "kind": "custom",
        }).status_code == 201

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    app = create_app(app.state.settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.getenv("LOGENTRA_TEST_BROWSER_CHANNEL"))
            page = browser.new_page()
            page.goto(url)
            page.fill("#login-user", "scoped-browser")
            page.fill("#login-pass", "synthetic-scoped-browser")
            page.get_by_role("button", name="Sign in").click()
            page.locator("#login-gate").wait_for(state="hidden")
            page.evaluate("Logbook.goto('records')")
            page.get_by_role("button", name="New record").click()
            assert page.locator("#m-eq option").all_text_contents() == ["BR-ALLOWED"]
            with page.expect_response(lambda response: response.url.endswith("/api/sync")) as created:
                page.locator("#m-go").click()
            assert created.value.ok
            page.locator("#rec-notes").fill("Scoped browser update")
            with page.expect_response(lambda response: response.url.endswith("/api/sync")) as saved:
                page.get_by_role("button", name="Save").click()
            assert saved.value.ok
            page.wait_for_function("Logbook.getState().records.some(record => record.revision === 2)")
            recovered = page.request.get(url + "/api/records", headers=admin).json()[0]
            assert recovered["notes"] == "Scoped browser update"
            assert recovered["equipmentId"] == "browser-allowed"
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_qr_print_and_phone_quick_entry(live_url):
    from playwright.sync_api import expect, sync_playwright

    shots = SHOT / "v0.7"
    shots.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        desktop = browser.new_page(viewport={"width": 1440, "height": 1000})
        bootstrap = desktop.request.get(live_url + "/api/bootstrap")
        if not bootstrap.json()["equipment"]:
            assert desktop.request.post(live_url + "/api/demo").ok
        desktop.goto(live_url)
        desktop.wait_for_function("window.Logbook?.getState().equipment.length > 0")
        desktop.evaluate("Logbook.goto('equipment')")
        desktop.get_by_role("button", name="QR tags", exact=True).click()
        expect(desktop.get_by_role("heading", name="Print QR tags")).to_be_visible()
        desktop.get_by_role("checkbox", name="Select P-101", exact=True).check()
        desktop.get_by_label("Start at position").fill("3")
        sheet = desktop.frame_locator('iframe[title="Label sheet preview"]')
        expect(sheet.locator(".label")).to_have_count(21)
        expect(sheet.locator(".label").nth(2)).to_contain_text("P-101")
        desktop.screenshot(path=str(shots / "qr-print-tags.png"), full_page=True)
        issued = desktop.request.post(live_url + "/api/equipment/qr-codes", data={"ids": ["eq_p101"]})
        assert issued.ok, issued.text()
        code = issued.json()["codes"]["eq_p101"]

        phone = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        phone.goto(f"{live_url}/q/{code}")
        expect(phone.get_by_role("heading", name="P-101")).to_be_visible()
        phone.get_by_label("Your name").fill("Plant operator")
        phone.locator("[data-parameter]").first.fill("900")
        expect(phone.get_by_text("Out of range", exact=True).first).to_be_visible()
        phone.screenshot(path=str(shots / "qr-phone-entry.png"), full_page=True)
        phone.get_by_role("button", name="Save readings").click()
        expect(phone.get_by_role("heading", name="Readings saved")).to_be_visible()
        phone.screenshot(path=str(shots / "qr-phone-saved.png"), full_page=True)
        phone.get_by_role("button", name="Raise work order").click()
        phone.get_by_role("button", name="Create work order").click()
        expect(phone.get_by_role("status").get_by_text("Work order created · Inspect P-101")).to_be_visible()

        printed = browser.new_page(viewport={"width":850,"height":1240})
        printed.goto(live_url + "/tags/print?ids=eq_p101&start=3")
        expect(printed.locator(".label")).to_have_count(21)
        printed.screenshot(path=str(shots / "qr-label-sheet.png"),full_page=True)

        spare = desktop.request.post(live_url + "/api/equipment",data={
            "id":"eq_qr_compact", "values":{"fld_tag":"AHU-12-TRAINING"},
            "params":[{"key":f"reading_{index}","label":f"Reading {index + 1}","unit":"Pa","max":20}
                      for index in range(7)],
        })
        assert spare.ok, spare.text()
        issued = desktop.request.post(live_url + "/api/equipment/qr-codes",data={"ids":["eq_qr_compact"]})
        compact = issued.json()["codes"]["eq_qr_compact"]
        phone.goto(f"{live_url}/q/{compact}")
        expect(phone.get_by_role("table")).to_be_visible()
        phone.locator("[data-parameter]").first.fill("12,2")
        phone.screenshot(path=str(shots / "qr-phone-table.png"),full_page=True)
        phone.get_by_role("button", name="Save readings").click()
        expect(phone.get_by_role("heading", name="Readings saved")).to_be_visible()
        assert desktop.request.post(live_url + "/api/equipment/eq_qr_compact/qr-code/rotate").ok
        phone.goto(f"{live_url}/q/{compact}")
        expect(phone.get_by_role("heading",name="This tag was replaced")).to_be_visible()
        phone.screenshot(path=str(shots / "qr-phone-replaced.png"),full_page=True)
        browser.close()


def test_ui_shift_log_sign_off_return_and_acknowledge(live_url):
    from datetime import datetime, timedelta, timezone

    from playwright.sync_api import expect, sync_playwright

    shots = SHOT / "v0.7"
    shots.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, color_scheme="light")

        def rest(method, path, data):
            reply = page.request.fetch(live_url + "/api" + path, method=method, data=data)
            assert reply.ok, reply.text()
            return reply.json()

        site = rest("POST","/sites",{"name":"Northbrook Works Training"})
        area = rest("POST",f"/sites/{site['id']}/areas",{"name":"Utilities Training"})
        pattern = rest("POST","/shift-patterns",{"site_id":site["id"],"name":"Training shifts","timezone":"UTC"})
        crew = rest("POST","/crews",{"site_id":site["id"],"name":"Utilities crew"})
        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=15)
        incoming = start + timedelta(minutes=60)
        roster = []
        for code, name, moment in [("A","Outgoing",start),("B","Incoming",incoming)]:
            definition = rest("POST",f"/shift-patterns/{pattern['id']}/definitions",{
                "code":code,"name":name,"start_time":moment.strftime("%H:%M"),"duration_minutes":60,
            })
            roster.append({"shift_definition_id":definition["id"],"date":moment.date().isoformat(),
                           "area_id":area["id"],"crew_id":crew["id"]})
        rest("PUT","/roster",roster)
        page.goto(live_url)
        page.get_by_role("button", name="Shift log", exact=True).click()
        expect(page.get_by_role("heading",name="Add log entry")).to_be_visible()
        composer = page.locator("#shift-entry-form")
        composer.get_by_label("Your name").fill("Plant operator")
        composer.get_by_label("Title").fill("Cooling pump inspection")
        composer.get_by_label("Shift entry").fill("Inspect the bearings on the next round.")
        composer.get_by_label("Add to handover").check()
        composer.get_by_role("button",name="Add entry").click()
        expect(page.locator(".shift-item").get_by_text("Cooling pump inspection")).to_be_visible()
        expect(page.get_by_role("button",name="Sign off and hand over")).to_be_enabled()
        page.locator("#shift-sign-form").get_by_label("Outgoing summary").fill("Watch the cooling loop under load.")
        page.screenshot(path=str(shots / "handover-outgoing.png"), full_page=True)
        page.get_by_role("button",name="Sign off and hand over").click()
        expect(page.get_by_role("heading",name="Acknowledge handover")).to_be_visible()
        page.screenshot(path=str(shots / "handover-incoming.png"), full_page=True)
        page.get_by_role("button",name="Return",exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Return reason").fill("Include the bearing inspection.")
        dialog.get_by_role("button",name="Save").click()
        expect(page.get_by_text("Version 2 · draft",exact=True)).to_be_visible()
        page.get_by_role("button",name="Open shift log").click()
        expect(page.get_by_role("button",name="Sign off and hand over")).to_be_enabled()
        page.get_by_role("button",name="Sign off and hand over").click()
        expect(page.get_by_role("button",name="Acknowledge",exact=True)).to_be_enabled()
        page.get_by_role("button",name="Acknowledge",exact=True).click()
        expect(page.get_by_text("Unseen at acknowledgement · 1",exact=True)).to_be_visible()
        page.get_by_role("button",name="Shifts & rosters",exact=True).click()
        page.locator("#shift-view select[name=site]").select_option(site["id"])
        page.get_by_role("button",name="Handover & escalation",exact=True).click()
        expect(page.get_by_label("Acknowledge within (minutes)")).to_have_value("30")
        page.screenshot(path=str(shots / "handover-escalation.png"), full_page=True)
        browser.close()
