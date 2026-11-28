"""Exercise the route contract with real sessions, a user-bound key and scoped data."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from logentra_server import authz, demo, documents, qr_entries
from logentra_server.app import create_app
from logentra_server.auth import BUILTIN_ROLES, create_session, hash_password, hash_token, new_api_key, principal_from_user
from logentra_server.config import Settings
from logentra_server.models import (
    Annotation, ApiKey, ApprovalRoute, AutoWorkOrderRule, Document, Equipment, OutboundWebhook, QREntry, Reading, ReadingPhoto, Role, SessionToken, Site, Template, User, WebhookDelivery, WorkOrder,
)
from logentra_server.route_policies import ROUTE_POLICIES
from logentra_server.shift_models import Crew, Handover, HandoverComment, HandoverItem, Shift, ShiftDefinition, ShiftLogAttachment, ShiftLogCategory, ShiftLogEntry, ShiftPattern, CrewMember
from logentra_server.shifts import utc_text
from logentra_server.utils import dumps
from test_authz_inventory import mounted_methods
from test_qr import csrf

SITE = "site_northbrook"
OWN_AREA = "area_nbk_1"
OTHER_AREA = "area_nbk_2"
QR = "ACT23456"
FOREIGN_QR = "OUT23456"
NEW_ENTRY = "12345678-1234-4234-8234-123456789012"
ACTORS = (*BUILTIN_ROLES, "scoped", "api_key", "anonymous")


def add_shift(db, suffix, site, area, lead, now):
    identifier = f"matrix-shift-{suffix}"
    db.add(Shift(id=identifier, site_id=site, area_id=area, shift_definition_id="matrix-definition",
                 crew_id="matrix-crew", date=now.date().isoformat(), starts_at=utc_text(now - timedelta(hours=1)),
                 ends_at=utc_text(now + timedelta(hours=1)), lead_user_id=lead, status="active"))
    db.flush()
    return identifier


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    root = tmp_path_factory.mktemp("authz-matrix")
    patch = pytest.MonkeyPatch()
    patch.setattr(documents, "ROOT", root)
    patch.setattr(documents, "ATTACH_DIR", root / "attachments" / "documents")
    patch.setattr(qr_entries, "ATTACHMENTS", root / "attachments")
    app = create_app(Settings(database_url=f"sqlite:///{root / 'matrix.db'}", auth_mode="users"))
    password = token_urlsafe(16)
    password_hash = hash_password(password)
    now = datetime.now(timezone.utc)
    timestamp = utc_text(now)
    headers = {"anonymous": {}}
    phones = {}
    with app.state.SessionLocal() as db:
        demo.load_demo(db)
        roles = {role.name: role for role in db.query(Role)}
        for actor in (*BUILTIN_ROLES, "scoped"):
            role = roles["technician" if actor == "scoped" else actor]
            user = User(id=f"matrix-{actor}", username=f"matrix-{actor}", role_id=role.id, password_hash=password_hash,
                        site_id=SITE if actor == "scoped" else None, area_id=OWN_AREA if actor == "scoped" else None,
                        created_at=timestamp, updated_at=timestamp)
            db.add(user)
            db.flush()
            raw, _ = create_session(db, user.id)
            headers[actor] = {"Authorization": f"Bearer {raw}"}
            phone, _ = create_session(db, user.id, kind="phone")
            phones[actor] = db.query(SessionToken.id).filter_by(token_hash=hash_token(phone)).scalar()

        raw, key_hash, prefix = new_api_key()
        db.add(ApiKey(id="matrix-key", user_id="matrix-technician", role_id=roles["technician"].id,
                      name="Matrix integration", key_hash=key_hash, prefix=prefix, created_at=timestamp))
        headers["api_key"] = {"X-API-Key": raw}

        for eid, area, code in (("eq_p101", OWN_AREA, QR), ("eq_ahu12", OTHER_AREA, FOREIGN_QR)):
            asset = db.get(Equipment, eid)
            asset.area_id, asset.qr_code = area, code

        db.add(Site(id="matrix-other-site", name="Other site", created_at=timestamp))
        db.add(ShiftPattern(id="matrix-pattern", site_id=SITE, name="Matrix pattern", timezone="UTC",
                            created_at=timestamp, updated_at=timestamp))
        db.add(Crew(id="matrix-crew", site_id=SITE, name="Matrix crew"))
        db.flush()
        db.add(CrewMember(crew_id="matrix-crew", user_id="matrix-technician"))
        db.add(OutboundWebhook(id="matrix-webhook", url="https://example.invalid/hook",
                               events='["out_of_range"]', created_at=timestamp))
        db.add(WebhookDelivery(id="matrix-delivery", webhook_id="matrix-webhook",
                               event="out_of_range", created_at=timestamp))
        db.add(AutoWorkOrderRule(id="matrix-rule", created_at=timestamp, updated_at=timestamp))
        db.add(ShiftDefinition(id="matrix-definition", pattern_id="matrix-pattern", code="A", name="Shift A",
                               start_time="00:00", duration_minutes=480))
        db.flush()
        local_shift = add_shift(db, "local", SITE, OWN_AREA, "matrix-scoped", now)
        foreign_shift = add_shift(db, "foreign", SITE, OTHER_AREA, "matrix-technician", now)
        category = db.query(ShiftLogCategory.id).filter(ShiftLogCategory.site_id.is_(None)).first()[0]
        objects = {}
        for label, eid, area, shift, actor in (
            ("local", "eq_p101", OWN_AREA, local_shift, "scoped"),
            ("foreign", "eq_ahu12", OTHER_AREA, foreign_shift, "technician"),
        ):
            entry = f"matrix-entry-{label}"
            handover = f"matrix-handover-{label}"
            item = f"matrix-item-{label}"
            qr_entry = str(uuid4())
            db.add(ShiftLogEntry(id=entry, shift_id=shift, event_at=timestamp, recorded_at=timestamp, category_id=category,
                                 severity="info", title="Inspection", body="", author_name="Operator", version=1))
            db.add(Handover(id=handover, outgoing_shift_id=shift, site_id=SITE, area_id=area,
                            version=1, status="draft", summary="Inspection"))
            db.add(QREntry(id=qr_entry, equipment_id=eid, note="Inspection", entered_by="Operator", created_at=timestamp))
            db.flush()
            db.add(HandoverItem(id=item, handover_id=handover, kind="note", title="Inspection", detail="", severity="info"))
            db.flush()
            db.add(HandoverComment(id=f"matrix-comment-{label}", handover_id=handover, item_id=item, kind="question",
                                   body="Ready?", author_name="Operator", created_at=timestamp))
            reading = db.query(Reading).filter_by(equipment_id=eid).first()
            annotation = reading.annotation or Annotation(
                id=f"matrix-annotation-{label}", reading_id=reading.id, annotated_at=timestamp, created_at=timestamp,
            )
            db.add(annotation)
            attachment = f"matrix-attachment-{label}"
            attachment_path = root / "attachments" / "shiftlog" / attachment
            attachment_path.parent.mkdir(parents=True, exist_ok=True)
            attachment_path.write_bytes(b"Inspection")
            db.add(ShiftLogAttachment(id=attachment, entry_id=entry, filename="note.txt", content_type="text/plain",
                                      size=10, sha256=sha256(b"Inspection").hexdigest(),
                                      storage_path=f"shiftlog/{attachment}", uploaded_by="Operator", created_at=timestamp))
            photo = f"matrix-photo-{label}"
            photo_path = root / "attachments" / "readings" / photo
            photo_path.parent.mkdir(parents=True, exist_ok=True)
            image = b"\x89PNG\r\n\x1a\n"
            photo_path.write_bytes(image)
            db.add(ReadingPhoto(id=photo, entry_id=qr_entry, equipment_id=eid, filename="photo.png",
                                content_type="image/png", size=len(image), width=1, height=1,
                                sha256=sha256(image).hexdigest(), storage_path=f"readings/{photo}",
                                uploaded_by="Operator", created_at=timestamp))
            order = db.query(WorkOrder).filter_by(equipment_id=eid).first()
            order.site_id, order.area_id, order.created_by_id, order.status = SITE, area, f"matrix-{actor}", "open"
            document = db.query(Document).filter_by(equipment_id=eid).first()
            document.site_id, document.area_id = SITE, area
            objects[label] = dict(
                eid=eid, reading=reading.id, entry_id=entry, reading_entry=reading.entry_id,
                parameter=reading.parameter_key, wid=order.id, did=document.id, shift_id=shift,
                handover=handover, item_id=item, comment_id=f"matrix-comment-{label}", qr_entry=qr_entry,
                aid=annotation.id, attachment=attachment, pid=photo,
                code=QR if label == "local" else FOREIGN_QR,
            )
        db.commit()
        objects["shared"] = {
            "rid": "rec_demo_1", "tid": db.query(Template.id).first()[0],
            "approval_route": db.query(ApprovalRoute.id).first()[0],
            "role": roles["viewer"].id, "category": category,
            "sid": SITE, "site": SITE,
        }

    # Each HTTP request exercises production dependencies and commit boundaries.
    # The outer transaction restores the shared fixtures between requests.
    route = next(route for method, path, route in mounted_methods(app.routes) if (method, path) == ("GET", "/api/equipment"))
    get_db = next(dep.call for dep in route.dependant.dependencies if dep.call.__name__ == "get_db")

    def isolated_db():
        with app.state.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                yield session
            connection.rollback()

    app.dependency_overrides[get_db] = isolated_db
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000), raise_server_exceptions=True)
    yield client, headers, password, phones, objects
    client.close()
    app.state.engine.dispose()
    patch.undo()


def sample(schema, components):
    if "$ref" in schema:
        return sample(components[schema["$ref"].rsplit("/", 1)[-1]], components)
    if "anyOf" in schema:
        return sample(next(part for part in schema["anyOf"] if part.get("type") != "null"), components)
    if "enum" in schema:
        return schema["enum"][0]
    fmt = schema.get("format")
    if fmt in {"date", "date-time", "time", "uuid"}:
        return {"date": "2026-11-27", "date-time": "2026-11-27T10:00:00Z", "time": "08:00", "uuid": NEW_ENTRY}[fmt]
    kind = schema.get("type", "object")
    if kind == "object":
        return {key: sample(schema["properties"][key], components) for key in schema.get("required", [])}
    if kind == "array":
        return [sample(schema["items"], components) for _ in range(schema.get("minItems", 0))]
    if kind in {"integer", "number"}:
        return schema.get("minimum", 1)
    if kind == "boolean":
        return True
    return "matrix"


def route_values(template, actor, phones, objects):
    ids = {**objects["shared"], **objects["local" if actor == "scoped" else "foreign"]}
    ids.update(uid_=f"matrix-{'technician' if actor == 'api_key' else actor}", user_id="matrix-technician",
               kid="matrix-key", definition_id="matrix-definition", delivery_id="matrix-delivery")
    ids["identifier"] = (
        ids["handover"] if "/handovers/" in template else ids["site"] if "/sites/" in template
        else "matrix-crew" if "/crews/" in template else "matrix-pattern" if "/shift-patterns/" in template
        else ids["category"] if "/shift-log-categories/" in template
        else ids["attachment"] if "/shift-log-attachments/" in template else "missing"
    )
    if "/roles/" in template:
        ids["rid"] = ids["role"]
    if "/approval-routes/" in template:
        ids["rid"] = ids["approval_route"]
    if "/readings/{rid}" in template:
        ids["rid"] = ids["reading"]
    if "/logs/{entry_id}" in template:
        ids["entry_id"] = ids["reading_entry"]
    if template.endswith("/api-keys/{kid}"):
        ids["uid_"] = "matrix-technician"
    if template.endswith("/sessions/{sid}"):
        ids["sid"] = phones.get("technician" if "/users/" in template else actor, "missing")
    if "/webhooks/{wid}" in template:
        ids["wid"] = "matrix-webhook"
    if "/work-order-rules/{rid}" in template:
        ids["rid"] = "matrix-rule"
    return ids


def request_input(method, template, schema, ids, password):
    spec = schema["paths"].get(template, {}).get(method.lower(), {})
    components = schema["components"]["schemas"]
    query = {param["name"]: sample(param["schema"], components)
             for param in spec.get("parameters", []) if param["in"] == "query" and param["required"]}
    body = spec.get("requestBody", {}).get("content", {})
    if "multipart/form-data" in body:
        filename, content = "matrix.txt", b"Inspection"
        if template.endswith("/migrate/v2/file"):
            filename, content = "matrix.json", b"{}"
            query["replace"] = "false"
        if template == "/api/ingest/file":
            filename, content = "matrix.csv", b"tag,parameter,value\nP-101,vibration,1\n"
            query["create_missing_equipment"] = "false"
        data = sample(body["multipart/form-data"]["schema"], components)
        data.pop("file", None)
        if "/qr/" in template:
            return {"params": query, "data": {"entry_id": NEW_ENTRY, "note": "Inspection"}}
        if template.endswith("/labour"):
            return {"params": query, "json": {"minutes": 1}}
        return {"params": query, "data": data, "files": {"file": (filename, content)}}
    if method in {"GET", "HEAD", "DELETE"} and not body:
        if template == "/api/shift-roster-users":
            query["site"] = SITE
        if template == "/tags/print":
            query["ids"] = ids["eid"]
            query["preview"] = "true"
        return {"params": query}
    value = sample(body["application/json"]["schema"], components) if "application/json" in body else {}
    if template in {"/api/auth/login", "/q/login"}:
        value = {"username": "matrix-admin", "password": password}
    elif template == "/api/auth/change-password":
        value = {"current_password": password, "new_password": token_urlsafe(16)}
    elif template == "/api/equipment/qr-codes":
        value = {"ids": [ids["eid"]]}
    elif template == "/api/qr/{code}/work-order":
        value = {"entry_id": ids["qr_entry"], "title": "Inspection"}
    elif template in {"/api/readings", "/api/ingest/readings"}:
        value = {"equipment_id": ids["eid"], "parameter": ids["parameter"], "value": 1}
    elif template == "/api/records":
        value = {"equipmentId": ids["eid"], "title": "Inspection"}
    elif template == "/api/migrate/v2":
        query["replace"] = "false"
    elif template.endswith("/transition"):
        value = {"status": "in_progress"}
    elif template == "/api/readings/bulk":
        value = {"readings": []}
    elif isinstance(value, dict):
        for key in {"site_id", "crew_id", "shift_definition_id", "user_id"} & value.keys():
            value[key] = ids.get(key, {"site_id": SITE, "crew_id": "matrix-crew",
                                       "shift_definition_id": "matrix-definition"}.get(key))
        if value.get("timezone") == "matrix":
            value["timezone"] = "UTC"
    return {"params": query, "json": value}


def expected_access(policy, actor, ids):
    if policy.kind == "public":
        return policy.condition != "qr_login_page" or actor != "viewer"
    if actor == "anonymous":
        return False
    role = "technician" if actor in {"scoped", "api_key"} else actor
    permissions = set(BUILTIN_ROLES[role][1])
    if not policy.scoped and actor == "scoped":
        return False
    if not set(policy.permissions) <= permissions or policy.any_of and not permissions.intersection(policy.any_of):
        return False
    if policy.condition == "user_session" and actor == "api_key":
        return False
    if policy.object_kind == "handover" and policy.action != "access":
        manager = "shifts.manage" in permissions and policy.action != "return"
        # The draft fixtures have no incoming shift. Only managers may act as incoming.
        lead = policy.action == "outgoing" and role == "technician"
        return manager or lead
    if policy.object_kind == "work_order" and policy.action == "delete":
        return role in {"admin", "supervisor", "technician"}
    if policy.condition == "self_or_manage":
        return ids["uid_"] == f"matrix-{role}" or actor == "scoped" or "users.manage" in permissions
    return True


@pytest.mark.parametrize("actor", ACTORS)
def test_role_route_matrix(matrix, actor):
    client, headers, password, phones, objects = matrix
    schema = client.app.openapi()
    failures = []
    for (method, template), policy in sorted(ROUTE_POLICIES.items()):
        ids = route_values(template, actor, phones, objects)
        url = template.format(**ids)
        if template == "/static":
            url += "/favicon.svg"
        kwargs = request_input(method, template, schema, ids, password)
        request_headers = headers[actor].copy()
        if template in {"/q/login", "/q/logout"}:
            request_headers.update(csrf(client, ids["code"]))
        response = client.request(method, url, headers=request_headers, **kwargs)
        client.cookies.clear()
        allowed = expected_access(policy, actor, ids)
        valid = 200 <= response.status_code < 300 or response.status_code in {400, 409, 415, 422}
        # An anonymous request can hit dependent resource validation on public routes.
        if (allowed and not valid) or (not allowed and response.status_code not in {401, 403}):
            failures.append((method, template, allowed, response.status_code, response.text[:220]))
    assert not failures, "\n".join(map(repr, failures))


def test_scoped_technician_cannot_access_foreign_objects(matrix):
    client, headers, password, phones, objects = matrix
    schema = client.app.openapi()
    actor = "scoped"
    failures = []
    covered = set()
    hidden_by_scope = {
        "equipment": "Equipment lookup conceals another area.",
        "record": "Record lookup conceals its equipment's area.",
        "reading": "Reading lookup conceals its equipment's area.",
        "work_order": "Work-order lookup conceals another area.",
        "document": "Document lookup conceals another area.",
        "qr_entry": "QR entry lookup conceals its equipment's area.",
    }
    for (method, template), policy in sorted(ROUTE_POLICIES.items()):
        if policy.kind != "object":
            continue
        ids = route_values(template, "technician", phones, objects)
        ids.update(uid_="matrix-supervisor", sid="matrix-other-site")
        if policy.object_kind == "record":
            ids["rid"] = "rec_demo_2"
        if policy.object_kind == "site":
            ids["identifier"] = "matrix-other-site"
        if template.endswith("/sessions/{sid}"):
            ids["sid"] = phones["technician"]
        response = client.request(method, template.format(**ids), headers=headers[actor],
                                  **request_input(method, template, schema, ids, password))
        covered.add(policy.object_kind)
        denied = {401, 403, 404} if policy.object_kind in hidden_by_scope else {401, 403}
        if response.status_code not in denied:
            failures.append((method, template, response.status_code, response.text[:220]))
    assert covered == authz.OBJECTS.keys()
    assert not failures, failures

    with client.app.state.SessionLocal() as db:
        principal = principal_from_user(db.get(User, "matrix-scoped"))
        foreign = objects["foreign"]
        targets = {
            "equipment": (foreign["eid"], "access", 404),
            "record": ("rec_demo_2", "access", 404),
            "reading": (foreign["reading"], "access", 404),
            "work_order": (foreign["wid"], "access", 404),
            "document": (foreign["did"], "access", 404),
            "shift": (foreign["shift_id"], "access", 403),
            "shift_log_entry": (foreign["entry_id"], "access", 403),
            "handover": (foreign["handover"], "access", 403),
            "qr_entry": (foreign["qr_entry"], "access", 404),
            "user": ("matrix-supervisor", "self_or_manage", 403),
            "role": (objects["shared"]["role"], "manage", 403),
            "site": ("matrix-other-site", "access", 403),
        }
        for kind, (identifier, action, status) in targets.items():
            with pytest.raises(HTTPException) as error:
                authz.require_object(db, principal, kind, identifier, action)
            assert error.value.status_code == status, kind


def custom_session(client, name, permissions, password, *, scoped=False):
    now = utc_text(datetime.now(timezone.utc))
    with client.app.state.SessionLocal.begin() as db:
        role = Role(id=f"role-{name}", name=name, permissions=dumps(permissions), built_in=0,
                    created_at=now, updated_at=now)
        db.add(role)
        db.flush()
        user = User(id=name, username=name, role_id=role.id, password_hash=hash_password(password),
                    site_id=SITE if scoped else None, area_id=OWN_AREA if scoped else None,
                    created_at=now, updated_at=now)
        db.add(user)
        db.flush()
        raw, _ = create_session(db, user.id)
    return {"Authorization": f"Bearer {raw}"}


def test_global_document_upload_requires_read_permission_for_each_link(matrix):
    client, _, password, _, objects = matrix
    headers = custom_session(client, "uploader", ["documents.upload"], password)
    local = objects["local"]
    for field, target, permission in (
        ("equipment_id", local["eid"], "equipment.read"),
        ("record_id", "rec_demo_1", "records.read"),
        ("work_order_id", local["wid"], "workorders.read"),
    ):
        response = client.post("/api/documents", headers=headers, data={field: target},
                               files={"file": ("notes.txt", b"Inspection")})
        assert response.status_code == 403
        assert response.json()["detail"] == f"Missing permission: {permission}"
    assert client.post("/api/documents", headers=headers, files={"file": ("notes.txt", b"Inspection")}).status_code == 201
    draft = objects["foreign"]["did"]
    response = client.patch(f"/api/documents/{draft}", headers=headers, json={"equipmentId": local["eid"]})
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing permission: equipment.read"
    assert client.patch(f"/api/documents/{draft}", headers=headers, json={"title": "Inspection"}).status_code == 200


def test_assign_only_role_can_attach_to_an_in_scope_work_order(matrix):
    client, _, password, _, objects = matrix
    headers = custom_session(client, "assigner", ["workorders.assign"], password, scoped=True)
    local, remote = objects["local"]["wid"], objects["foreign"]["wid"]
    assert client.post(f"/api/work-orders/{local}/attachments", headers=headers,
                       json={"filename": "notes.txt"}).status_code == 201
    assert client.post(f"/api/work-orders/{remote}/attachments", headers=headers,
                       json={"filename": "notes.txt"}).status_code == 404


def test_work_order_delete_roles_and_scope(matrix):
    client, headers, _, _, objects = matrix
    local = objects["local"]["wid"]
    foreign = objects["foreign"]["wid"]
    with client.app.state.SessionLocal() as db:
        own = db.get(WorkOrder, local)
        own.assignee_id = None
        own.status = "open"
        db.commit()
    assert client.delete(f"/api/work-orders/{local}", headers=headers["technician"]).status_code == 403
    assert client.delete(f"/api/work-orders/{local}", headers=headers["scoped"]).status_code == 200
    assert client.delete(f"/api/work-orders/{foreign}", headers=headers["scoped"]).status_code == 404
    with client.app.state.SessionLocal() as db:
        row = db.get(WorkOrder, foreign)
        row.assignee_id = "matrix-technician"
        db.commit()
    assert client.delete(f"/api/work-orders/{foreign}", headers=headers["technician"]).status_code == 403
    assert client.delete(f"/api/work-orders/{foreign}", headers=headers["supervisor"]).status_code == 200
    with client.app.state.SessionLocal() as db:
        row = db.get(WorkOrder, foreign)
        row.status = "in_progress"
        db.commit()
    assert client.delete(f"/api/work-orders/{foreign}", headers=headers["supervisor"]).status_code == 403
    assert client.delete(f"/api/work-orders/{foreign}", headers=headers["admin"]).status_code == 200


def test_mixed_work_order_patch_requires_each_permission(matrix):
    client, headers, _, _, objects = matrix
    wid = objects["foreign"]["wid"]
    original = client.get(f"/api/work-orders/{wid}", headers=headers["admin"]).json()
    for fields in (
        {"title": "Changed", "status": "closed"},
        {"title": "Changed", "assignee_id": "matrix-technician"},
    ):
        assert client.patch(f"/api/work-orders/{wid}", headers=headers["technician"], json=fields).status_code == 403
        current = client.get(f"/api/work-orders/{wid}", headers=headers["admin"]).json()
        assert current["title"] == original["title"]
        assert current["status"] == original["status"]
        assert current["assignee_id"] == original["assignee_id"]
    response = client.patch(f"/api/work-orders/{wid}", headers=headers["supervisor"],
                            json={"title": "Changed", "status": "in_progress", "assignee_id": "matrix-technician"})
    assert response.status_code == 200, response.text


def test_public_qr_page_hides_equipment(matrix):
    client, _, _, _, objects = matrix
    valid = client.get(f"/q/{objects['local']['code']}")
    invalid = client.get("/q/ZZZZZZZZ")
    assert valid.status_code == invalid.status_code == 200
    assert valid.text == invalid.text
    assert "P-101" not in valid.text
    assert "Sign in to log readings" in valid.text


def test_work_order_delete_across_sites(matrix):
    client, headers, password, _, _ = matrix
    scoped_supervisor = custom_session(
        client, "site-supervisor", BUILTIN_ROLES["supervisor"][1], password, scoped=True,
    )
    timestamp = utc_text(datetime.now(timezone.utc))
    with client.app.state.SessionLocal.begin() as db:
        db.add(WorkOrder(id="matrix-other-site-order", title="Other site inspection",
                         site_id="matrix-other-site", status="open", priority="medium",
                         created_at=timestamp, updated_at=timestamp))
    url = "/api/work-orders/matrix-other-site-order"
    assert client.delete(url, headers=scoped_supervisor).status_code == 404
    assert client.delete(url, headers=headers["admin"]).status_code == 200
