from datetime import datetime, timedelta, timezone

from logentra_server.models import Area, Site
from logentra_server.utils import utcnow_iso


def post(client, path, body, status=201):
    response = client.post("/api" + path, json=body)
    assert response.status_code == status, response.text
    return response.json()


def rostered_shift(client, *, with_incoming=True):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    with client.app.state.SessionLocal.begin() as db:
        db.add(Site(id="site_north", name="Northbrook Works", created_at=utcnow_iso()))
        db.flush()
        db.add(Area(id="area_util", site_id="site_north", name="Utilities", created_at=utcnow_iso()))
    pattern = post(client, "/shift-patterns", {"site_id":"site_north", "name":"Utilities", "timezone":"UTC"})
    tail = f"/shift-patterns/{pattern['id']}/definitions"
    start = now - timedelta(minutes=30)
    incoming = now + timedelta(minutes=10)
    out_def = post(client, tail, {"code":"A", "name":"Current crew", "start_time":start.strftime("%H:%M"),
                                 "duration_minutes":40})
    first = post(client, "/crews", {"site_id":"site_north", "name":"Crew A"})
    roster = [{"shift_definition_id": out_def["id"], "date":start.date().isoformat(),
               "crew_id": first["id"], "area_id":"area_util"}]
    if with_incoming:
        in_def = post(client, tail, {"code":"B", "name":"Incoming crew", "start_time":incoming.strftime("%H:%M"),
                                    "duration_minutes":720})
        second = post(client, "/crews", {"site_id":"site_north", "name":"Crew B"})
        roster.append({"shift_definition_id":in_def["id"], "date":incoming.date().isoformat(),
                       "crew_id":second["id"], "area_id":"area_util"})
    response = client.put("/api/roster", json=roster)
    assert response.status_code == 200, response.text
    response = client.get("/api/shifts/current", params={"area":"area_util"})
    assert response.status_code == 200, response.text
    current, = response.json()["items"]
    return current


def test_roster_repeat_and_append_only_log(client, tmp_path, monkeypatch):
    from logentra_server import qr_entries
    from logentra_server.shift_models import ShiftLogEntry

    monkeypatch.setattr(qr_entries, "ATTACHMENTS", tmp_path)
    shift = rostered_shift(client)
    path = f"/shifts/{shift['id']}/entries"
    original = post(client, path, {"title":"Pump vibration", "body":"Check bearings", "author_name":"R. Operator"})
    base = f"/shift-log-entries/{original['id']}"
    amendment = post(client, base + "/amend", {"title":"Pump vibration checked", "severity":"medium"})
    assert amendment["version"] == 2 and amendment["supersedes_id"] == original["id"]
    post(client, base + "/amend", {"title":"Conflicting amendment"}, 409)
    post(client, f"/shift-log-entries/{amendment['id']}/comments", {"body":"Reviewed on round"})
    upload = client.post(f"/api/shift-log-entries/{amendment['id']}/attachments",
                         files={"file": ("inspection.txt", b"Inspection complete")})
    assert upload.status_code == 201, upload.text
    assert client.get(f"/api/shift-log-attachments/{upload.json()['id']}").content == b"Inspection complete"
    voided = post(client, f"/shift-log-entries/{amendment['id']}/void", {"reason":"Entered against wrong equipment"})
    assert voided["version"] == 3 and voided["void_reason"]
    assert client.get(f"/api{path}").json()["total"] == 1
    assert client.get(f"/api{path}", params={"revisions":True}).json()["total"] == 3
    assert client.get("/api/shift-log-entries", params={"q":"bearings"}).json()["total"] == 1
    with client.app.state.SessionLocal() as db:
        assert db.get(ShiftLogEntry, original["id"]).voided_at is None
    day = shift["date"]
    repeat = post(client, "/roster/repeat", {"first_date":day,"last_date":day,"interval_days":7,"repetitions":2}, 200)
    assert repeat["count"] in (2, 4)
