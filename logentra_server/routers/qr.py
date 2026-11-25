"""Equipment label issuance and print rendering."""
from html import escape
from io import BytesIO
from secrets import choice
from urllib.parse import urlsplit

import segno
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from logentra_server import qr_entries, qr_phone, service
from logentra_server.auth import Principal, create_session, hash_token
from logentra_server.models import Area, Equipment, QRRetiredCode, Reading, ReadingPhoto, SessionToken, Setting, User
from logentra_server.phone_auth import PHONE_COOKIE, csrf_token, phone_principal, require_csrf
from logentra_server.routers.auth_users import LoginIn, verify_login
from logentra_server.utils import utcnow_iso

QR_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
QUICK_FORM_MAX_PARAMS = 6
LABELS_PER_SHEET = 21


class EquipmentSelection(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


class PublicURL(BaseModel):
    public_base_url: str = Field(max_length=500)


def set_setting(db, key, value, *, lock_only=False):
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else postgres_insert
    statement = insert(Setting).values(key=key, value=value, updated_at=utcnow_iso())
    changes = {"value": Setting.value} if lock_only else {
        "value": statement.excluded.value, "updated_at": statement.excluded.updated_at,
    }
    db.execute(statement.on_conflict_do_update(index_elements=[Setting.key], set_=changes))


def issue_code(db, equipment):
    while True:
        code = "".join(choice(QR_ALPHABET) for _ in range(8))
        if not db.get(QRRetiredCode, code) and not db.query(Equipment.id).filter_by(qr_code=code).first():
            break
    equipment.qr_code = code
    equipment.qr_code_issued_at = utcnow_iso()
    db.flush()
    return code


def equipment_for(db, eid, principal):
    equipment = db.get(Equipment, eid)
    if not equipment:
        raise HTTPException(404, "Equipment not found")
    if not principal.allows_scope(equipment.site_id, equipment.area_id):
        raise HTTPException(403, "You are not allowed to access this area")
    return equipment


def public_url(db, settings):
    row = db.get(Setting, "public_base_url")
    return (row.value if row else settings.public_base_url).rstrip("/")


def qr_summary(db, equipment):
    area = db.get(Area, equipment.area_id) if equipment.area_id else None
    name = next((item.value for item in equipment.values if item.field.key == "name"), "") or ""
    params = []
    for param in sorted(equipment.parameters, key=lambda item: (item.sort_order, item.key)):
        last = db.query(Reading.value).filter_by(equipment_id=equipment.id, parameter_key=param.key).order_by(
            Reading.ts.desc(), Reading.id.desc()
        ).first()
        params.append({**service.param_to_ui(param), "last": last[0] if last else None})
    return {"id": equipment.id, "tag": equipment.tag, "name": name, "area": area.name if area else "",
            "code": equipment.qr_code, "parameters": params,
            "layout": "form" if len(params) <= QUICK_FORM_MAX_PARAMS else "table"}


def resolve_code(db, code):
    return db.query(Equipment).filter_by(qr_code=code.upper()).one_or_none()


def current_equipment(db, code, principal):
    equipment = resolve_code(db, code)
    if not equipment:
        retired = db.get(QRRetiredCode, code.upper())
        if retired and retired.equipment_id:
            raise HTTPException(410, "This tag was replaced")
        raise HTTPException(404, "QR tag not found")
    if not principal.allows_scope(equipment.site_id, equipment.area_id):
        raise HTTPException(403, "You are not allowed to access this area")
    return equipment


def render_label(equipment, area, base_url):
    address = f"{base_url}/q/{equipment.qr_code}"
    stream = BytesIO()
    segno.make_qr(address, error="m").save(stream, kind="svg", xmldecl=False, svgns=False, border=4)
    svg = stream.getvalue().decode()
    name = next((field.value for field in equipment.values if field.field.key == "name"), "") or ""
    return (
        f'<div class="label">{svg}<div><strong>{escape(equipment.tag)}</strong>'
        f'<div>{escape(name)}</div><div>{escape(area)}</div>'
        f'<small>Scan to log readings<br>{escape(address.split("://", 1)[-1])}</small></div></div>'
    )


def render_sheet(labels, start):
    blank = '<div class="label blank"></div>'
    slots = [blank] * (start - 1) + labels
    slots.extend([blank] * (-len(slots) % LABELS_PER_SHEET))
    sheets = "".join(
        '<section class="sheet">' + "".join(slots[index:index + LABELS_PER_SHEET]) + "</section>"
        for index in range(0, len(slots), LABELS_PER_SHEET)
    )
    return """<!doctype html><html lang="en"><meta charset="utf-8"><title>Logentra OS QR tags</title>
<style>
@page{size:A4;margin:0}
*{box-sizing:border-box}
body{margin:0;background:#e6ebf2;color:#17283a;font:11pt system-ui,sans-serif}
.sheet{width:210mm;height:297mm;background:white;margin:12px auto;padding:15.15mm 0 0 7.2mm;
display:grid;grid-template-columns:repeat(3,63.5mm);grid-template-rows:repeat(7,38.1mm);column-gap:2.5mm}
.label{display:flex;align-items:center;gap:1mm;overflow:hidden;outline:1px dashed #d7e0eb;padding-right:1mm}
.label svg{width:28mm;height:28mm;flex:none}.label div{min-width:0;overflow-wrap:anywhere}
.label small{font-size:7pt;line-height:1.2;display:block;margin-top:1mm}
@media print{body{background:white}.sheet{margin:0;break-after:page}.sheet:last-child{break-after:auto}.label{outline:none}}
</style>""" + sheets + "</html>"


class PhoneLogin(LoginIn):
    keep_signed_in: bool = True


def build_router(get_db, get_principal, settings):
    router = APIRouter(tags=["qr"])

    @router.post("/api/equipment/qr-codes")
    def generate(body: EquipmentSelection, db: Session = Depends(get_db),
                 principal: Principal = Depends(get_principal)):
        principal.require("equipment.write")
        set_setting(db, "qr_issuance_lock", "", lock_only=True)
        codes = {}
        issued = []
        for eid in dict.fromkeys(body.ids):
            equipment = equipment_for(db, eid, principal)
            if not equipment.qr_code:
                issue_code(db, equipment)
                issued.append(eid)
            codes[eid] = equipment.qr_code
        if issued:
            service.audit(db, "qr.codes_generated", actor=principal.username, user_id=principal.user_id,
                          entity_type="equipment", detail={"ids": issued, "count": len(issued)})
        db.commit()
        return {"codes": codes, "issued": issued}

    @router.post("/api/equipment/{eid}/qr-code/rotate")
    def rotate(eid: str, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
        principal.require("equipment.write")
        set_setting(db, "qr_issuance_lock", "", lock_only=True)
        equipment = equipment_for(db, eid, principal)
        old = equipment.qr_code
        if old:
            db.add(QRRetiredCode(code=old, equipment_id=eid, retired_at=utcnow_iso(),
                                 retired_by=principal.username))
        code = issue_code(db, equipment)
        service.audit(db, "qr.code_rotated", actor=principal.username, user_id=principal.user_id,
                      entity_type="equipment", entity_id=eid, detail={"old_code": old, "code": code})
        db.commit()
        return {"code": code}

    @router.get("/api/settings/public-url")
    def get_public_url(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
        principal.require("settings.manage")
        printed = db.get(Setting, "qr_labels_printed")
        return {"public_base_url": public_url(db, settings), "labels_printed": bool(printed)}

    @router.put("/api/settings/public-url")
    def update_public_url(body: PublicURL, db: Session = Depends(get_db),
                          principal: Principal = Depends(get_principal)):
        principal.require("settings.manage")
        value = body.public_base_url.strip().rstrip("/")
        try:
            parts = urlsplit(value)
            valid = (parts.scheme in ("http", "https") and parts.hostname and parts.netloc
                     and parts.username is None and parts.password is None and not parts.path
                     and not parts.query and not parts.fragment and not any(c.isspace() for c in value)
                     and "\\" not in value and (parts.port is None or parts.port > 0))
        except ValueError:
            valid = False
        if value and not valid:
            raise HTTPException(422, "Enter an HTTP or HTTPS server address without a path")
        previous = public_url(db, settings)
        set_setting(db, "public_base_url", value)
        if previous != value:
            service.audit(db, "settings.public_url_changed", actor=principal.username,
                          user_id=principal.user_id, entity_type="setting", entity_id="public_base_url",
                          detail={"previous": previous, "current": value})
        db.commit()
        return {"public_base_url": value, "labels_printed": bool(db.get(Setting, "qr_labels_printed"))}

    @router.get("/tags/print", response_class=HTMLResponse)
    def print_tags(request: Request, ids: str, start: int = Query(1, ge=1, le=21), preview: bool = False,
                   db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
        principal.require("equipment.read")
        selected = list(dict.fromkeys(value for value in ids.split(",") if value))
        if not 1 <= len(selected) <= 500:
            raise HTTPException(422, "Select between 1 and 500 equipment items")
        base = public_url(db, settings) or str(request.base_url).rstrip("/")
        labels = []
        for eid in selected:
            equipment = equipment_for(db, eid, principal)
            if not equipment.qr_code:
                raise HTTPException(409, f"Generate a QR code for {equipment.tag} first")
            area = db.get(Area, equipment.area_id) if equipment.area_id else None
            labels.append(render_label(equipment, area.name if area else "", base))
        if not preview:
            set_setting(db, "qr_labels_printed", "1")
            service.audit(db, "qr.sheet_printed", actor=principal.username, user_id=principal.user_id,
                          entity_type="equipment", detail={"ids": selected, "count": len(selected), "start": start})
            db.commit()
        return HTMLResponse(render_sheet(labels, start), headers={"Cache-Control": "no-store"})

    @router.get("/q/{code}", response_class=HTMLResponse)
    def scan(code: str, request: Request, db: Session = Depends(get_db)):
        equipment = resolve_code(db, code)
        if not equipment:
            retired = db.get(QRRetiredCode, code.upper())
            current = db.get(Equipment, retired.equipment_id) if retired and retired.equipment_id else None
            if current and current.qr_code:
                return qr_phone.render_phone(
                    request, '<section><h1>This tag was replaced</h1>'
                    f'<a class="button" href="/q/{current.qr_code}">Open current tag</a></section>',
                    {}, status=410,
                )
            return qr_phone.render_phone(request, "<section><h1>QR tag not found</h1></section>", {}, status=404)
        token = csrf_token(request)
        try:
            principal = phone_principal(db, request, settings)
            principal.require("readings.read", "readings.write")
            current_equipment(db, code, principal)
        except HTTPException as error:
            if error.status_code == 401:
                return qr_phone.render_phone(
                    request, qr_phone.equipment_card(equipment.tag, "", "") + qr_phone.login_form(),
                    {"screen": "login", "csrf": token},
                )
            return qr_phone.render_phone(
                request, "<section><h1>You are not allowed to log readings for this area</h1></section>",
                {}, status=403,
            )
        summary = qr_summary(db, equipment)
        db.commit()
        open_mode = principal.mode == "open"
        content = qr_phone.equipment_card(summary["tag"], summary["name"], summary["area"])
        content += qr_phone.reading_form(summary, open_mode=open_mode, photo_limit=settings.photos_per_entry)
        return qr_phone.render_phone(
            request, content, {**summary, "screen": "entry", "csrf": token, "open": open_mode,
                               "photoLimit": settings.photos_per_entry},
        )

    @router.get("/api/qr/{code}")
    def summary(code: str, request: Request, db: Session = Depends(get_db)):
        principal = phone_principal(db, request, settings)
        principal.require("readings.read")
        equipment = current_equipment(db, code, principal)
        result = qr_summary(db, equipment)
        db.commit()
        return result

    @router.post("/api/qr/{code}/entry")
    async def entry(code: str, request: Request, entry_id: str = Form(max_length=36),
                    values: str = Form("{}"), note: str = Form("", max_length=10000),
                    entered_by: str = Form("", max_length=250),
                    photos: list[UploadFile] = File(default_factory=list), db: Session = Depends(get_db)):
        principal = phone_principal(db, request, settings, write=True)
        principal.require("readings.write")
        equipment = current_equipment(db, code, principal)
        return await qr_entries.save_entry(db, equipment, principal, settings, entry_id, values, note,
                                           entered_by, photos)

    @router.get("/api/readings/photos/{pid}")
    def get_photo(pid: str, request: Request, db: Session = Depends(get_db)):
        principal = phone_principal(db, request, settings)
        principal.require("readings.read")
        photo = db.get(ReadingPhoto, pid)
        if not photo:
            raise HTTPException(404, "Photo not found")
        equipment_for(db, photo.equipment_id, principal)
        path = qr_entries.attachment_path(photo.storage_path, "readings")
        if not path.is_file():
            raise HTTPException(404, "Photo not found")
        return FileResponse(path, media_type=photo.content_type, filename=photo.filename,
                            content_disposition_type="inline")

    @router.get("/api/storage")
    def storage(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
        principal.require("settings.manage")
        totals = {}
        for label, folder in (("reading_photos", "readings"), ("shift_log_attachments", "shiftlog"),
                              ("documents", "documents")):
            root = qr_entries.ATTACHMENTS / folder
            sizes = [file.stat().st_size for file in root.rglob("*") if file.is_file() and not file.is_symlink()]
            totals[label] = {"files": len(sizes), "bytes": sum(sizes)}
        return totals

    @router.post("/q/login")
    def login(body: PhoneLogin, request: Request, db: Session = Depends(get_db)):
        require_csrf(request)
        user = verify_login(db, body.username, body.password)
        days = settings.phone_session_days if body.keep_signed_in else 1
        raw, expires = create_session(db, user.id, days=days, kind="phone",
                                      label=request.headers.get("user-agent", "Phone")[:250])
        user.last_login_at = utcnow_iso()
        service.audit(db, "auth.phone_login", actor=user.username, user_id=user.id,
                      entity_type="user", entity_id=user.id)
        db.commit()
        response = JSONResponse({"expires_at": expires})
        response.set_cookie(PHONE_COOKIE, raw, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https",
                            max_age=days * 86400 if body.keep_signed_in else None)
        return response

    def list_phones(db, user_id):
        rows = db.query(SessionToken).filter_by(user_id=user_id, kind="phone", revoked_at=None)
        return [
            {key: getattr(row, key) for key in ("id", "label", "created_at", "expires_at", "last_used_at")}
            for row in rows.filter(SessionToken.expires_at > utcnow_iso()).order_by(SessionToken.created_at.desc())
        ]

    def revoke_phone(db, principal, user_id, sid, *, admin=False):
        count = db.query(SessionToken).filter_by(id=sid, user_id=user_id, kind="phone", revoked_at=None).update(
            {"revoked_at": utcnow_iso()}
        )
        if not count:
            raise HTTPException(404, "Phone session not found")
        service.audit(db, "auth.phone_session_revoked", actor=principal.username, user_id=principal.user_id,
                      entity_type="session", entity_id=sid, detail={"user_id": user_id, "admin": admin})
        db.commit()
        return {"ok": True}

    @router.post("/q/logout")
    def logout(request: Request, db: Session = Depends(get_db)):
        require_csrf(request)
        raw = request.cookies.get(PHONE_COOKIE)
        if raw:
            try:
                principal = phone_principal(db, request, settings)
            except HTTPException as error:
                if error.status_code != 401:
                    raise
            else:
                row = db.query(SessionToken).filter_by(token_hash=hash_token(raw), kind="phone").one_or_none()
                if row and row.user_id == principal.user_id:
                    revoke_phone(db, principal, row.user_id, row.id)
        response = JSONResponse({"ok": True})
        response.delete_cookie(PHONE_COOKIE)
        return response

    @router.get("/api/auth/sessions")
    def own_sessions(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
        if principal.mode != "user" or not principal.user_id:
            raise HTTPException(401, "Sign in to list phone sessions")
        return list_phones(db, principal.user_id)

    @router.delete("/api/auth/sessions/{sid}")
    def revoke_own(sid: str, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
        if principal.mode != "user" or not principal.user_id:
            raise HTTPException(401, "Sign in to revoke a phone session")
        return revoke_phone(db, principal, principal.user_id, sid)

    @router.get("/api/users/{user_id}/sessions")
    def user_sessions(user_id: str, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
        principal.require("users.manage")
        if not db.get(User, user_id):
            raise HTTPException(404, "User not found")
        return list_phones(db, user_id)

    @router.delete("/api/users/{user_id}/sessions/{sid}")
    def revoke_user_phone(user_id: str, sid: str, db: Session = Depends(get_db),
                          principal: Principal = Depends(get_principal)):
        principal.require("users.manage")
        return revoke_phone(db, principal, user_id, sid, admin=True)

    return router
