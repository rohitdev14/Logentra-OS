from copy import deepcopy

import pytest

from logentra_server.auth import create_session
from logentra_server.models import Area, AuditLog, Equipment, OutboundWebhook, Punch, Role, Signoff, Site, User, WebhookDelivery
from logentra_server.utils import dumps, utcnow_iso

PATH = "/api/records/rec_p101"
PUNCH = {"id": "punch_guard", "description": "Replace coupling guard", "priority": "B", "raised": "2026-11-26"}


@pytest.fixture
def roles(client):
    now = utcnow_iso()
    headers = {}
    with client.app.state.SessionLocal.begin() as db:
        db.add(Site(id="northbrook", name="Northbrook Works", timezone="Asia/Singapore", created_at=now))
        db.flush()
        db.add_all(Area(id=area, name=area.title(), site_id="northbrook", created_at=now)
                   for area in ("utilities", "workshop"))
        db.flush()
        db.add(Equipment(id="eq_p101", tag="P-101", site_id="northbrook", area_id="utilities",
                         created_at=now, updated_at=now))
        for username, display, role, area in (
            ("operator", "Lena Tan", "technician", "utilities"),
            ("approver", "Amir Rahman", "supervisor", "utilities"),
            ("viewer", "Nora Lim", "viewer", "utilities"),
            ("outside", "Daniel Chua", "supervisor", "workshop"),
        ):
            user = User(id=username, username=username, display_name=display, site_id="northbrook",
                        area_id=area, role_id=db.query(Role.id).filter_by(name=role).scalar(),
                        password_hash="unused", is_active=1, created_at=now, updated_at=now)
            db.add(user)
            db.flush()
            headers[username] = {"Authorization": "Bearer " + create_session(db, user.id)[0]}
    return headers


def create_record(client, headers=None, *, ready=False, with_column=True):
    response = client.post("/api/records", headers=headers, json={
        "id": "rec_p101", "equipmentId": "eq_p101" if headers else "",
        "templateName": "Pump commissioning",
        "columns": [{"id": "p101_result", "label": "Result", "type": "passfail" if with_column else "text"}],
        "items": [{"id": "p101_guard", "label": "Inspect guard",
                   "values": {"p101_result": "Pass"} if ready else {}}],
    })
    assert response.status_code == 201, response.text
    return response.json()


def action(client, path, row, headers=None, status=200, **extra):
    response = client.post(f"{PATH}/{path}", headers=headers, json={"revision": row["revision"], **extra})
    assert response.status_code == status, response.text
    return response.json()


def snapshot(client, headers=None):
    response = client.get(PATH, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def sync_record(client, row, headers=None, expected=200):
    result = client.put("/api/sync", headers=headers, json={"records": [row]})
    assert result.status_code == expected, result.text
    return result.json()


def test_record_lifecycle_audit_display_names_and_webhooks(client, roles):
    client.app.state.delivery_worker.stop()
    with client.app.state.SessionLocal.begin() as db:
        db.add(OutboundWebhook(id="record_lifecycle", url="https://example.invalid/hook",
                               events=dumps(["record.submitted", "record.returned", "record.approved"]),
                               enabled=1, secret="", created_at=utcnow_iso()))
    technician, supervisor = roles["operator"], roles["approver"]
    row = create_record(client, technician)
    row["items"][0]["values"]["p101_result"] = "Fail"
    row["punches"] = [PUNCH]
    sync_record(client, row, technician)
    with client.app.state.SessionLocal() as db:
        created_at = db.get(Punch, PUNCH["id"]).created_at
        counts = db.query(AuditLog).count()
    row = snapshot(client, technician)
    sync_record(client, row, technician)
    with client.app.state.SessionLocal() as db:
        assert db.query(AuditLog).count() == counts
    submitted = action(client, "submit", row, technician)
    assert submitted["status"] == "submitted" and submitted["catalogRevision"]
    assert submitted["submittedBy"] == "Lena Tan"
    action(client, "return", submitted, supervisor, status=422, comment="  ")
    returned = action(client, "return", submitted, supervisor, comment="Recheck the coupling guard")
    assert returned["status"] == "returned" and returned["returnComment"] == "Recheck the coupling guard"
    returned["items"][0]["values"]["p101_result"] = "Pass"
    returned["punches"][0]["raised"] = "2026-11-28"
    sync_record(client, returned, technician)
    edited = snapshot(client, technician)
    assert edited["punches"][0]["raised"] == PUNCH["raised"]
    again = action(client, "submit", edited, technician)
    approved = action(client, "approve", again, supervisor, comment="Verified on rounds")
    assert approved["status"] == "approved" and approved["approvalComment"] == "Verified on rounds"
    assert approved["approvedBy"] == approved["signoffName"] == "Amir Rahman"
    assert [row["id"] for row in client.get("/api/records?status=approved", headers=roles["viewer"]).json()] == ["rec_p101"]
    assert client.get("/api/records?status=draft", headers=roles["viewer"]).json() == []
    with client.app.state.SessionLocal() as db:
        assert db.get(Punch, PUNCH["id"]).created_at == created_at
        signature = db.query(Signoff).filter_by(record_id="rec_p101").one()
        assert (signature.name, signature.signed_at) == ("Amir Rahman", approved["approvedAt"])
        assert sorted(row.event for row in db.query(WebhookDelivery)) == [
            "record.approved", "record.returned", "record.submitted", "record.submitted",
        ]
    response = client.get(PATH + "/audit", headers=roles["viewer"])
    assert response.status_code == 200, response.text
    events = list(reversed(response.json()))
    assert [event["action"] for event in events] == ["record." + action for action in (
        "created", "item_changed", "punch_raised", "submitted", "returned", "item_changed",
        "submitted", "approved", "signed",
    )]
    assert events[0]["detail"] == {"template": "Pump commissioning", "equipment_tag": "P-101"}
    assert events[1]["actor"] == "operator" and events[1]["actor_name"] == "Lena Tan"
    assert events[1]["detail"]["changes"] == [{"item": "Inspect guard", "column": "Result", "from": "", "to": "Fail"}]
    assert events[4]["detail"] == {"comment": "Recheck the coupling guard"}
    assert events[5]["detail"]["changes"][0]["from"] == "Fail"
    assert events[-1]["actor_name"] == "Amir Rahman" and events[-1]["at"].endswith("Z")
    assert client.get(PATH + "/audit", headers=roles["outside"]).status_code == 404
    assert client.get("/api/audit", headers=roles["viewer"]).status_code == 403


def test_permissions_revisions_and_locked_records(client, roles):
    tech, supervisor = roles["operator"], roles["approver"]
    row = create_record(client, tech)
    action(client, "submit", row, roles["viewer"], status=403)
    action(client, "submit", row, tech, status=422)
    assert sync_record(client, {**row, "signoffName": "Fake"}, tech, expected=422)["detail"] == (
        "Use Submit for approval to sign records")
    assert client.post("/api/records", headers=tech, json={
        "equipmentId": "eq_p101", "signoffDate": "2026-11-26",
    }).status_code == 422
    row["items"][0]["values"]["p101_result"] = "Pass"
    sync_record(client, {**row, "status": "approved", "approvedBy": "Fake"}, tech)
    action(client, "submit", row, tech, status=409)
    row = snapshot(client, tech)
    assert row["status"] == "draft" and row["approvedBy"] == ""
    submitted = action(client, "submit", row, supervisor)
    action(client, "approve", submitted, tech, status=403)
    action(client, "approve", submitted, roles["outside"], status=404)
    action(client, "approve", submitted, supervisor, status=403)
    assert client.delete(PATH, headers=tech).status_code == 409
    changed = deepcopy(submitted)
    changed["items"][0]["values"]["p101_result"] = "Fail"
    assert sync_record(client, changed, tech, expected=409)["detail"] == "Record is waiting for approval"
    returned = action(client, "return", submitted, supervisor, comment="Review result")
    submitted = action(client, "submit", returned, tech)
    approved = action(client, "approve", submitted, supervisor)
    assert client.delete(PATH, headers=tech).status_code == 409
    changed["revision"] = approved["revision"]
    changed.update(signoffName=approved["signoffName"], signoffDate=approved["signoffDate"])
    assert sync_record(client, changed, tech, expected=409)["detail"] == "Approved records are locked"
    sync_record(client, approved, tech)


def test_open_mode_approval_accepts_any_result_without_passfail_column(client):
    draft = create_record(client, ready=True, with_column=False)
    submitted = action(client, "submit", draft)
    assert action(client, "approve", submitted)["status"] == "approved"


def test_legacy_signed_record_has_synthetic_event(client):
    row = create_record(client)
    with client.app.state.SessionLocal.begin() as db:
        db.add(Signoff(id="legacy_signature", record_id=row["id"], name="Amir Rahman", signed_at="2026-10-02"))
        db.add(AuditLog(id="legacy_revision", entity_type="record", entity_id=row["id"], action="revise",
                        ts="2026-10-01T08:00:00Z", actor="Amir Rahman", detail=dumps({"notes": "Pump inspected"})))
    trail = client.get(PATH + "/audit").json()
    event = next(event for event in trail if event["action"] == "record.signed")
    assert next(event for event in trail if event["action"] == "revise")["label"] == "Updated record"
    assert event["label"] == "Signed (before 0.7.1)"
    assert event["actor_name"] == "Amir Rahman" and event["at"] == "2026-10-02T00:00:00Z"
