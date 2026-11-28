import csv
from datetime import datetime, timezone
from io import BytesIO
import json
import re
from uuid import uuid4

from PIL import Image

from logentra_server.models import Equipment, Parameter
from logentra_server.utils import utcnow_iso


def equipment(client, count=1):
    eid = "eq_qr"
    now = utcnow_iso()
    with client.app.state.SessionLocal.begin() as db:
        db.add(Equipment(id=eid, tag="P-101", created_at=now, updated_at=now))
        db.flush()
        db.add_all(Parameter(id=f"p_{i}", key=f"value_{i}", label=f"Reading {i}", equipment_id=eid,
                            min_value=0, max_value=5) for i in range(count))
    issued = client.post("/api/equipment/qr-codes", json={"ids": [eid]})
    assert issued.status_code == 200, issued.text
    return eid, issued.json()["codes"][eid]


def csrf(client, code="ABCDEFGH"):
    page = client.get(f"/q/{code}")
    data = re.search(r'<script type="application/json" id="phone-data">(.*?)</script>', page.text).group(1)
    return {"Origin": str(client.base_url).rstrip("/"), "X-CSRF-Token": json.loads(data)["csrf"]}


def test_code_rotation_and_label_sheet(client):
    eid, code = equipment(client)
    assert len(code) == 8 and not set(code) & set("01OIL")
    assert client.post("/api/equipment/qr-codes", json={"ids": [eid]}).json()["codes"][eid] == code
    sheet = client.get("/tags/print", params={"ids": eid, "start": 5})
    assert sheet.status_code == 200, sheet.text
    assert sheet.text.count('class="label') == 21
    assert "viewBox=" in sheet.text
    assert sheet.text.count('class="label blank"') == 20
    assert sheet.text.index("P-101") > sheet.text.index("<svg")
    replacement = client.post(f"/api/equipment/{eid}/qr-code/rotate").json()["code"]
    assert replacement != code
    retired = client.get(f"/q/{code}")
    assert retired.status_code == 410
    assert f"/q/{replacement}" in retired.text
    assert client.get("/q/ZZZZZZZZ").status_code == 404


def test_quick_entry_photo_and_idempotency(client):
    _, code = equipment(client, count=7)
    assert client.get(f"/api/qr/{code}").json()["layout"] == "table"
    assert client.get(f"/q/{code}").status_code == 200
    path = f"/api/qr/{code}/entry"
    body = {"entry_id": str(uuid4()), "values": '{"value_0":"6,2","value_1":" "}',
            "note": "Cooling water vibration", "entered_by": "Operator"}
    image = BytesIO()
    Image.new("RGB", (10, 10)).save(image, "PNG")
    photo = image.getvalue()
    assert client.post(path, data=body).status_code == 403
    assert client.post(path, data=body, headers={**csrf(client, code), "Origin": "http://example.invalid"}).status_code == 403
    invalid = client.post(path, data={**body, "values": '{"value_0":"bad"}'}, headers=csrf(client, code))
    assert invalid.status_code == 422
    assert "value_0" in invalid.json()["detail"]
    files = [("photos", ("photo.png", photo, "image/png"))]
    gif = BytesIO()
    Image.new("RGB", (10, 10)).save(gif, "GIF")
    assert client.post(path, data=body, files={"photos": ("photo.gif", gif.getvalue(), "image/gif")},
                       headers=csrf(client, code)).status_code == 422
    assert client.post(path, data=body, files=files * 4, headers=csrf(client, code)).status_code == 422
    assert client.post(path, data=body, files={"photos": ("bad.png", b"invalid", "image/png")},
                       headers=csrf(client, code)).status_code == 422
    assert client.post(path, data=body, files={"photos": ("big.png", b"x" * 2097153, "image/png")},
                       headers=csrf(client, code)).status_code == 413
    saved = client.post(path, data=body, files=files, headers=csrf(client, code))
    assert saved.status_code == 200, saved.text
    value, = saved.json()["values"]
    assert value["value"] == 6.2 and value["out_of_range"]
    assert client.post(path, data=body, files=files, headers=csrf(client, code)).json() == saved.json()
    pid, = saved.json()["photos"]
    assert client.get(f"/api/readings/photos/{pid}").content == photo
    assert client.get("/api/storage").json()["reading_photos"] == {"files": 1, "bytes": len(photo)}


def test_phone_login_and_revocation(db_url):
    from fastapi.testclient import TestClient

    from logentra_server.app import create_app
    from logentra_server.config import Settings

    app = create_app(Settings(database_url=db_url, auth_mode="users",
                              bootstrap_admin_username="operator", bootstrap_admin_password="test-password"))
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
        credentials = {"username": "operator", "password": "test-password"}
        browser = client.post("/api/auth/login", json=credentials).json()
        headers = {"Authorization": f"Bearer {browser['token']}"}
        now = utcnow_iso()
        with app.state.SessionLocal.begin() as db:
            db.add(Equipment(id="eq_qr", tag="P-101", qr_code="ABCDEFGH", created_at=now, updated_at=now))
        assert "Sign in to log readings" in client.get("/q/ABCDEFGH").text
        assert client.post("/q/login", json=credentials).status_code == 403
        phone = client.post("/q/login", json=credentials, headers=csrf(client))
        assert phone.status_code == 200, phone.text
        assert "HttpOnly" in phone.headers["set-cookie"] and "Max-Age=604800" in phone.headers["set-cookie"]
        expiry = datetime.fromisoformat(phone.json()["expires_at"])
        assert 6.99 < (expiry - datetime.now(timezone.utc)).total_seconds() / 86400 <= 7
        assert "Quick entry" in client.get("/q/ABCDEFGH").text
        assert client.get("/api/bootstrap").status_code == 401
        bearer = {"Authorization": "Bearer " + client.cookies["logentra_phone"]}
        assert client.get("/api/bootstrap", headers=bearer).status_code == 401
        assert client.get("/api/qr/ABCDEFGH", headers=bearer).status_code == 200
        session, = client.get("/api/auth/sessions", headers=headers).json()
        assert client.delete(f"/api/auth/sessions/{session['id']}", headers=headers).status_code == 200
        assert "Sign in to log readings" in client.get("/q/ABCDEFGH").text
        assert client.post("/q/login", json=credentials, headers=csrf(client)).status_code == 200
        user_id = browser["user"]["id"]
        session, = client.get(f"/api/users/{user_id}/sessions", headers=headers).json()
        assert client.delete(f"/api/users/{user_id}/sessions/{session['id']}", headers=headers).status_code == 200
        assert "Sign in to log readings" in client.get("/q/ABCDEFGH").text


def test_phone_login_sets_secure_cookie_behind_trusted_proxy(db_url):
    from fastapi.testclient import TestClient

    from logentra_server.app import create_app
    from logentra_server.config import Settings

    app = create_app(Settings(database_url=db_url, auth_mode="users", trusted_proxies=["127.0.0.1/32"],
                              bootstrap_admin_username="operator", bootstrap_admin_password="test-password"))
    async def upstream(scope, receive, send):
        if scope["type"] == "http":
            scope["scheme"] = "http"
        await app(scope, receive, send)

    with TestClient(upstream, base_url="https://logbook.plant.local:8443", client=("127.0.0.1", 1)) as client:
        now = utcnow_iso()
        with app.state.SessionLocal.begin() as db:
            db.add(Equipment(id="eq_qr", tag="P-101", qr_code="ABCDEFGH", created_at=now, updated_at=now))
        proxy = {"X-Forwarded-Proto": "https"}
        page = client.get("/q/ABCDEFGH", headers=proxy)
        assert "Secure" in page.headers["set-cookie"]
        data = re.search(r'<script type="application/json" id="phone-data">(.*?)</script>', page.text).group(1)
        form_headers = {**proxy, "Origin": "https://logbook.plant.local:8443",
                        "X-CSRF-Token": json.loads(data)["csrf"]}
        phone = client.post("/q/login", headers=form_headers,
                            json={"username":"operator", "password":"test-password"})
        assert phone.status_code == 200, phone.text
        assert "Secure" in phone.headers["set-cookie"]


def test_demo_equipment_name_and_form_layout(client):
    assert client.post("/api/demo").status_code == 200
    code = client.post("/api/equipment/qr-codes", json={"ids": ["eq_p101"]}).json()["codes"]["eq_p101"]
    summary = client.get(f"/api/qr/{code}").json()
    assert summary["layout"] == "form"
    assert summary["name"] == "Pump"
    assert summary["last_logged_at"] and summary["last_logged_by"]
    rows = client.get("/api/equipment/qr-labels", params={"q": "P-101"}).json()["items"]
    row = next(item for item in rows if item["id"] == "eq_p101")
    assert (row["name"], row["area"]) == (summary["name"], summary["area"])
    assert "<div>Pump</div>" in client.get("/tags/print", params={"ids": "eq_p101"}).text
    assert "<p>Pump</p>" in client.get(f"/q/{code}").text


def test_quick_entry_and_photos_respect_area_scope(client):
    from logentra_server.auth import create_session
    from logentra_server.models import Area, Role, Site, User

    eq_id, code = equipment(client)
    now = utcnow_iso()
    headers = {}
    with client.app.state.SessionLocal.begin() as db:
        db.add(Site(id="site_training", name="Northbrook Works", created_at=now))
        db.flush()
        db.add_all([Area(id=area, name=area, site_id="site_training", created_at=now)
                    for area in ("Utilities", "Workshop")])
        db.flush()
        eq = db.get(Equipment, eq_id)
        eq.site_id, eq.area_id = "site_training", "Utilities"
        technician = db.query(Role).filter_by(name="technician").one()
        for area in ("Utilities", "Workshop"):
            user = User(id=area, username=area, role_id=technician.id, site_id="site_training", area_id=area,
                        is_active=1, password_hash="unused", created_at=now, updated_at=now)
            db.add(user)
            db.flush()
            headers[area] = {"Authorization": "Bearer " + create_session(db, user.id, kind="phone")[0]}
        browser = {"Authorization": "Bearer " + create_session(db, "Workshop")[0]}
        allowed_browser = {"Authorization": "Bearer " + create_session(db, "Utilities")[0]}
        limited_role = Role(id="catalog-role", name="Catalog", permissions='["equipment.read"]',
                            created_at=now, updated_at=now)
        db.add(limited_role)
        db.flush()
        db.add(User(id="catalog-user", username="Catalog", role_id=limited_role.id, is_active=1,
                    password_hash="unused", created_at=now, updated_at=now))
        db.flush()
        catalog_browser = {"Authorization": "Bearer " + create_session(db, "catalog-user")[0]}
    path = f"/api/qr/{code}/entry"
    body = {"entry_id": str(uuid4()), "values": '{"value_0": "2"}'}
    assert client.post(path, data=body, headers=headers["Workshop"]).status_code == 403
    page = client.get(f"/q/{code}", headers=headers["Workshop"])
    assert page.status_code == 403 and "not allowed to log readings for this area" in page.text
    image = BytesIO()
    Image.new("RGB", (8, 8)).save(image, "PNG")
    saved = client.post(path, data=body, files={"photos": ("pump.png", image.getvalue())},
                        headers=headers["Utilities"])
    assert saved.status_code == 200, saved.text
    photo, = saved.json()["photos"]
    assert client.get(f"/api/readings/photos/{photo}", headers=headers["Workshop"]).status_code == 403
    assert client.get(f"/api/readings/photos/{photo}", headers=headers["Utilities"]).content == image.getvalue()
    assert not client.get("/api/logs", params={"equipment_id": eq_id}, headers=browser).json()["items"]
    logs = client.get("/api/logs", params={"equipment_id": eq_id}).json()["items"]
    assert logs[0]["photos"] == [{"id": photo, "filename": "pump.png"}]

    note = "Guard inspected with pump stopped."
    note_id = str(uuid4())
    body = {"entry_id": note_id, "note": note}
    assert client.post(path, data=body, headers=headers["Workshop"]).status_code == 403
    note_saved = client.post(path, data=body, files={"photos": ("guard.png", image.getvalue())},
                             headers=headers["Utilities"])
    assert note_saved.status_code == 200, note_saved.text
    assert note_saved.json()["values"] == []
    note_photo, = note_saved.json()["photos"]
    assert client.get(f"/api/readings/photos/{note_photo}", headers=headers["Workshop"]).status_code == 403
    assert client.get(f"/api/readings/photos/{note_photo}", headers=headers["Utilities"]).content == image.getvalue()

    params = {"equipment_id": eq_id, "q": "Guard inspected", "from": now[:10], "to": now[:10]}
    scoped = client.get("/api/logs", params=params, headers=allowed_browser).json()
    assert scoped["total"] == 1
    item, = scoped["items"]
    assert (item["kind"], item["remarks"], item["enteredBy"], item["readings"]) == ("note", note, "Utilities", {})
    assert item["photos"] == [{"id": note_photo, "filename": "guard.png"}]
    assert not client.get("/api/logs", params=params, headers=browser).json()["items"]
    assert client.get("/api/logs", params=params, headers=catalog_browser).status_code == 403
    assert note_id not in [entry["id"] for entry in client.get("/api/bootstrap", headers=catalog_browser).json()["logs"]]
    first_page = client.get("/api/logs", params={"equipment_id": eq_id, "page_size": 1}, headers=allowed_browser).json()
    second_page = client.get("/api/logs", params={"equipment_id": eq_id, "page_size": 1, "page": 2},
                             headers=allowed_browser).json()
    assert first_page["total"] == second_page["total"] == 2
    assert {first_page["items"][0]["id"], second_page["items"][0]["id"]} == {note_id, logs[0]["id"]}
    export = client.get("/api/export/readings.csv", params={"tag": "P-101"}, headers=allowed_browser)
    exported, = [row for row in csv.DictReader(export.content.decode("utf-8-sig").splitlines()) if row["entry_id"] == note_id]
    assert (exported["parameter"], exported["remarks"], exported["entered_by"]) == ("Note", note, "Utilities")
    assert note not in client.get("/api/export/readings.csv", headers=browser).text
    assert note not in client.get("/api/export/readings.csv", params={"parameter": "value_0"}, headers=allowed_browser).text
