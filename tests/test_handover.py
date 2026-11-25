from datetime import datetime, timedelta, timezone
from hashlib import sha256

from logentra_server import handovers, service
from logentra_server.models import AuditLog, Equipment, Punch, Record, WorkOrder
from logentra_server.utils import utcnow_iso
from test_shiftlog import post, rostered_shift


def seeded_evidence(client):
    now = utcnow_iso()
    with client.app.state.SessionLocal.begin() as db:
        equipment = Equipment(id="eq_pump", tag="P-101", site_id="site_north", area_id="area_util",
                              created_at=now, updated_at=now)
        db.add(equipment)
        db.flush()
        service.ensure_parameter(db, equipment.id, "vibration").max_value = 5
        db.flush()
        service.ingest_readings(db, [{"equipment_id":equipment.id,"parameter":"vibration","value":8}],
                               source="manual", commit=False)
        db.add_all([WorkOrder(id=identifier, title=title, priority=priority, status=status,
                             equipment_id=equipment.id, site_id=equipment.site_id, area_id=equipment.area_id,
                             created_at=now, updated_at=now)
                    for identifier, title, priority, status in [
                        ("WO-0001", "Inspect pump", "urgent", "open"),
                        ("WO-0002", "Routine check", "low", "open"),
                        ("WO-0003", "Completed work", "high", "closed"),
                    ]])
        db.add(Record(id="record_1", equipment_id=equipment.id, created_at=now, updated_at=now))
        db.flush()
        db.add(Punch(id="punch_1", record_id="record_1", description="Replace guard", status="Open", created_at=now))


def test_frozen_handover_return_then_acknowledge(client):
    shift = rostered_shift(client)
    seeded_evidence(client)
    path = f"/shifts/{shift['id']}"
    keep = post(client, path + "/entries", {"title":"Monitor #P-101", "in_handover":True})
    assert keep["equipment_id"] == "eq_pump"
    post(client, path + "/entries", {"title":"Ordinary note"})
    draft = client.get(f"/api{path}/handover")
    assert draft.status_code == 200, draft.text
    current = draft.json()
    assert {item["kind"] for item in current["items"]} == {"oor_reading", "work_order", "punch", "log_entry"}
    assert len(current["items"]) == 4
    identifier = current["id"]
    endpoint = f"/handovers/{identifier}"
    remove = next(item for item in current["items"] if item["kind"] == "punch")
    assert client.delete(f"/api{endpoint}/items/{remove['id']}").status_code == 200
    signed = post(client, endpoint + "/sign-off", {"summary":"Monitor cooling loop","author_name":"Outgoing lead"}, 200)
    assert signed["status"] == "signed_off"
    assert len(signed["items"]) == 3
    assert sha256(signed["snapshot_json"].encode()).hexdigest() == signed["snapshot_sha256"]
    late = post(client, path + "/entries", {"title":"Late observation", "in_handover":True})
    assert late["after_handover"]
    item = signed["items"][0]["id"]
    question = post(client, endpoint + f"/items/{item}/questions", {"body":"Was load stable?"})
    comment = question["comments"][0]
    post(client, f"/handover-comments/{comment['id']}/answer", {"body":"Yes, stable."})
    post(client, endpoint + "/return", {"reason":" "}, 422)
    replacement = post(client, endpoint + "/return", {"reason":"Clarify set point"}, 200)
    assert replacement["status"] == "draft" and replacement["version"] == 2
    original = client.get(f"/api{endpoint}").json()
    assert sum(event["action"] == "handover.opened" for event in original["audit_events"]) == 1
    for _ in range(2):
        opened = client.get(f"/api/handovers/{replacement['id']}").json()
        assert sum(event["action"] == "handover.opened" for event in opened["audit_events"]) == 1
    assert original["status"] == "returned"
    assert original["snapshot_json"] == signed["snapshot_json"]
    report = client.get(f"/api{endpoint}/report")
    assert all(text in report.text for text in (signed["snapshot_sha256"], "Clarify set point", "Yes, stable."))
    endpoint = f"/handovers/{replacement['id']}"
    signed = post(client, endpoint + "/sign-off", {"summary":"Check the pump set point"}, 200)
    reviewed = signed["items"][0]["id"]
    post(client, endpoint + f"/items/{reviewed}/seen", {}, 200)
    ack = post(client, endpoint + "/acknowledge", {"comment":"Rounds underway"}, 200)
    assert ack["unseen_count"] == len(signed["items"]) - 1
    assert ack["snapshot_sha256"] == signed["snapshot_sha256"]
    assert client.get("/api/handovers", params={"q":"set point","status":"acknowledged"}).json()["total"] == 1
    assert client.get("/api/handovers/export.csv").status_code == 200


def test_reminder_escalation_and_no_incoming(client):
    shift = rostered_shift(client)
    end = datetime.fromisoformat(shift["ends_at"])
    with client.app.state.SessionLocal() as db:
        assert handovers.process_notifications(db, end - timedelta(minutes=5))["reminded"] == 1
        assert handovers.process_notifications(db, end - timedelta(minutes=4))["reminded"] == 0
    row = client.get(f"/api/shifts/{shift['id']}/handover").json()
    signed = post(client, f"/handovers/{row['id']}/sign-off", {"summary":"Normal operation"}, 200)
    assert signed["incoming_shift_id"]
    due = datetime.fromisoformat(signed["ack_due_at"])
    with client.app.state.SessionLocal() as db:
        assert handovers.process_notifications(db, due + timedelta(seconds=1))["escalated"] == 1
        assert handovers.process_notifications(db, due + timedelta(seconds=2))["escalated"] == 0
        assert db.query(AuditLog).filter_by(action="handover.escalated").count() == 1


def test_missing_incoming_notifies_immediately(client):
    shift = rostered_shift(client, with_incoming=False)
    row = client.get(f"/api/shifts/{shift['id']}/handover").json()
    signed = post(client, f"/handovers/{row['id']}/sign-off", {}, 200)
    assert not signed["incoming_shift_id"]
    assert signed["escalated_to"] == "supervisor"
    assert datetime.fromisoformat(signed["escalated_at"]) <= datetime.now(timezone.utc)


def test_rostered_leads_and_area_permissions(client):
    from logentra_server.auth import create_session
    from logentra_server.models import Area, Role, User
    from logentra_server.shift_models import Shift
    from logentra_server.utils import uid

    shift = rostered_shift(client)
    now = utcnow_iso()
    with client.app.state.SessionLocal.begin() as db:
        db.add(Area(id="area_other", site_id="site_north", name="Workshop", created_at=now))
        db.flush()
        roles = {row.name: row.id for row in db.query(Role)}
        tokens = {}
        users = {}
        for name, role, area in (("outgoing","technician","area_util"), ("incoming","technician","area_util"),
                                 ("crew","technician","area_util"), ("viewer","viewer","area_util"),
                                 ("other","supervisor","area_other"), ("supervisor","supervisor","area_util")):
            row = User(id=uid("usr"), username=name, role_id=roles[role], site_id="site_north", area_id=area,
                       password_hash="unused", is_active=1, created_at=now, updated_at=now)
            db.add(row)
            db.flush()
            users[name] = row.id
            tokens[name] = {"Authorization": "Bearer " + create_session(db, row.id)[0]}
        db.get(Shift, shift["id"]).lead_user_id = users["outgoing"]
    endpoint = f"/api/shifts/{shift['id']}"
    assert client.get(endpoint + "/entries", headers=tokens["other"]).status_code == 403
    assert client.post(endpoint + "/entries", headers=tokens["viewer"], json={"title":"No access"}).status_code == 403
    assert client.post(endpoint + "/entries", headers=tokens["crew"], json={"title":"Crew entry"}).status_code == 201
    draft = client.get(endpoint + "/handover", headers=tokens["viewer"]).json()
    with client.app.state.SessionLocal.begin() as db:
        # Attach the actor to the outgoing roster so repeated sign-off resolves
        # the same lead after refreshing the planned incoming roster.
        from logentra_server.shift_models import RosterEntry
        incoming = db.get(Shift, draft["incoming_shift_id"])
        slot = db.query(RosterEntry).filter_by(shift_definition_id=incoming.shift_definition_id,
                                                date=incoming.date, area_id=incoming.area_id).one()
        slot.lead_user_id = users["incoming"]
    path = "/api/handovers/" + draft["id"]
    for person in ("viewer", "crew"):
        for _ in range(2):
            assert client.get(path, headers=tokens[person]).status_code == 200
    openings = [event["actor"] for event in client.get(path, headers=tokens["viewer"]).json()["audit_events"]
                if event["action"] == "handover.opened"]
    assert sorted(openings) == ["crew", "viewer"]
    assert client.post(path + "/sign-off", headers=tokens["crew"]).status_code == 403
    signed = client.post(path + "/sign-off", headers=tokens["outgoing"])
    assert signed.status_code == 200, signed.text
    assert client.post(path + "/acknowledge", headers=tokens["outgoing"]).status_code == 403
    assert client.post(path + "/return", headers=tokens["supervisor"], json={"reason":"Wrong lead"}).status_code == 403
    assert client.post(path + "/acknowledge", headers=tokens["incoming"]).status_code == 200


def test_reading_lookback_uses_site_setting(client):
    shift = rostered_shift(client)
    seeded_evidence(client)
    now = datetime.now(timezone.utc)
    with client.app.state.SessionLocal.begin() as db:
        for days in (3, 9):
            service.ingest_readings(db, [{"id": f"reading_{days}_days", "equipment_id": "eq_pump",
                                         "parameter": "vibration", "value": 8,
                                         "ts": (now - timedelta(days=days)).isoformat()}], commit=False)
    path = f"/api/shifts/{shift['id']}/handover"
    default = client.get(path).json()
    candidates = {item["ref_id"] for item in default["items"]}
    assert "reading_3_days" in candidates
    assert "reading_9_days" not in candidates
    response = client.put("/api/sites/site_north/handover-settings", json={"reading_lookback_days": 2})
    assert response.status_code == 200, response.text
    updated = client.get(path).json()
    assert not {item["ref_id"] for item in updated["items"]} & {"reading_3_days", "reading_9_days"}


def test_reading_detail_uses_recorded_limits_and_site_time(client):
    from logentra_server.models import Parameter
    from logentra_server.shift_models import Shift, ShiftPattern

    shift = rostered_shift(client)
    seeded_evidence(client)
    with client.app.state.SessionLocal.begin() as db:
        db.query(ShiftPattern).one().timezone = "Asia/Singapore"
        parameter = db.query(Parameter).filter_by(equipment_id="eq_pump", key="vibration").one()
        parameter.unit, parameter.min_value, parameter.max_value = "mm/s", 2.0, 4.5
        db.flush()
        for identifier, value in (("high-reading", 6.2), ("low-reading", 1.1)):
            service.ingest_readings(db, [{"id": identifier, "equipment_id": "eq_pump", "parameter": "vibration",
                                         "value": value, "ts": "2026-11-25T13:22:00Z"}], commit=False)
        parameter.max_value = 10
        db.flush()
        row = db.get(Shift, shift["id"])
        details = {item[1]: item[3] for item in handovers.live_candidates(
            db, row, datetime(2026, 11, 25, 14, tzinfo=timezone.utc)
        )}
    assert details["high-reading"] == "6.2 mm/s at 21:22 (limit max 4.5)"
    assert details["low-reading"] == "1.1 mm/s at 21:22 (limit min 2)"
