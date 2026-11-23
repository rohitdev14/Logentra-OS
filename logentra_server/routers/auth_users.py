"""Login, users, roles, sites, and API keys."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, selectinload

from logentra_server import seed, service
from logentra_server.auth import (
    ALL_PERMISSIONS,
    PERMISSIONS,
    Principal,
    create_session,
    hash_password,
    new_api_key,
    parse_bearer,
    principal_from_user,
    revoke_session,
    verify_password,
)
from logentra_server.models import ApiKey, Area, Role, Site, User
from logentra_server.pagination import clamp_page, page_result
from logentra_server.utils import dumps, loads, uid, utcnow_iso


def _user_dict(u: User, *, include_role: bool = True) -> dict[str, Any]:
    out = {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name or u.username,
        "email": u.email or "",
        "role_id": u.role_id,
        "role": u.role.name if include_role and u.role else None,
        "site_id": u.site_id,
        "area_id": u.area_id,
        "is_active": bool(u.is_active),
        "created_at": u.created_at,
        "updated_at": u.updated_at,
        "last_login_at": u.last_login_at,
    }
    return out


def _role_dict(r: Role) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description or "",
        "built_in": bool(r.built_in),
        "permissions": loads(r.permissions, []) or [],
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _site_dict(s: Site) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "code": s.code or "",
        "created_at": s.created_at,
        "areas": [
            {"id": a.id, "site_id": a.site_id, "name": a.name, "code": a.code or "", "created_at": a.created_at}
            for a in (s.areas or [])
        ],
    }


def build_router(get_db, get_principal, settings) -> APIRouter:
    router = APIRouter()

    def current(principal: Principal = Depends(get_principal)) -> Principal:
        return principal

    # ---------- auth (login is public; /me requires a principal) ----------
    @router.post("/api/auth/login", tags=["auth"], summary="Exchange username and password for a session token")
    def login(body: dict[str, Any], db: Session = Depends(get_db)):
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        if not username or not password:
            raise HTTPException(400, "username and password are required")
        user = (
            db.query(User)
            .options(selectinload(User.role))
            .filter(User.username == username)
            .one_or_none()
        )
        if not user or not user.is_active or not verify_password(password, user.password_hash):
            raise HTTPException(401, "Invalid username or password")
        raw, expires = create_session(db, user.id, days=getattr(settings, "session_days", 7) or 7)
        user.last_login_at = utcnow_iso()
        service.audit(db, "login", actor=user.username, user_id=user.id, entity_type="user", entity_id=user.id)
        db.commit()
        principal = principal_from_user(user)
        return {
            "token": raw,
            "token_type": "bearer",
            "expires_at": expires,
            "user": principal.to_public(),
        }

    @router.post("/api/auth/logout", tags=["auth"])
    def logout(request: Request, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        raw = parse_bearer(request.headers.get("authorization")) or request.headers.get("X-API-Key") or ""
        if raw:
            revoke_session(db, raw)
            db.commit()
        return {"ok": True}

    @router.get("/api/auth/me", tags=["auth"])
    def me(principal: Principal = Depends(current)):
        return principal.to_public()

    @router.get("/api/auth/permissions", tags=["auth"], summary="Catalogue of permission strings")
    def list_permissions(principal: Principal = Depends(current)):
        return {"permissions": list(PERMISSIONS)}

    @router.post("/api/auth/change-password", tags=["auth"])
    def change_password(body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        if not principal.user_id:
            raise HTTPException(400, "Password change requires a user session")
        current_pw = body.get("current_password") or body.get("currentPassword") or ""
        new_pw = body.get("new_password") or body.get("newPassword") or ""
        if not new_pw or len(new_pw) < 8:
            raise HTTPException(400, "New password must be at least 8 characters")
        user = db.get(User, principal.user_id)
        if not user or not verify_password(current_pw, user.password_hash):
            raise HTTPException(401, "Current password is incorrect")
        user.password_hash = hash_password(new_pw)
        user.updated_at = utcnow_iso()
        service.audit(db, "update", actor=principal.actor, user_id=principal.user_id, entity_type="user", entity_id=user.id, detail={"field": "password"})
        db.commit()
        return {"ok": True}

    # ---------- roles ----------
    @router.get("/api/roles", tags=["users"])
    def list_roles(db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        rows = db.query(Role).order_by(Role.built_in.desc(), Role.name).all()
        return [_role_dict(r) for r in rows]

    @router.post("/api/roles", tags=["users"], status_code=201)
    def create_role(body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        if db.query(Role).filter(Role.name == name).one_or_none():
            raise HTTPException(409, f"Role {name!r} already exists")
        perms = [p for p in (body.get("permissions") or []) if p in ALL_PERMISSIONS]
        now = utcnow_iso()
        row = Role(
            id=body.get("id") or uid("role"),
            name=name,
            description=body.get("description") or "",
            built_in=0,
            permissions=dumps(perms),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        service.audit(db, "create", actor=principal.actor, user_id=principal.user_id, entity_type="role", entity_id=row.id)
        db.commit()
        db.refresh(row)
        return _role_dict(row)

    @router.patch("/api/roles/{rid}", tags=["users"])
    def patch_role(rid: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        row = db.get(Role, rid)
        if not row:
            raise HTTPException(404, "Role not found")
        if row.built_in and "permissions" in body:
            raise HTTPException(400, "Built-in role permissions cannot be changed; clone a custom role instead")
        if "name" in body and body["name"] and not row.built_in:
            row.name = body["name"].strip()
        if "description" in body:
            row.description = body.get("description") or ""
        if "permissions" in body and not row.built_in:
            row.permissions = dumps([p for p in (body.get("permissions") or []) if p in ALL_PERMISSIONS])
        row.updated_at = utcnow_iso()
        service.audit(db, "update", actor=principal.actor, user_id=principal.user_id, entity_type="role", entity_id=row.id)
        db.commit()
        db.refresh(row)
        return _role_dict(row)

    @router.delete("/api/roles/{rid}", tags=["users"])
    def delete_role(rid: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        row = db.get(Role, rid)
        if not row:
            raise HTTPException(404, "Role not found")
        if row.built_in:
            raise HTTPException(400, "Built-in roles cannot be deleted")
        if db.query(User).filter(User.role_id == rid).count():
            raise HTTPException(400, "Role is still assigned to users")
        db.delete(row)
        service.audit(db, "delete", actor=principal.actor, user_id=principal.user_id, entity_type="role", entity_id=rid)
        db.commit()
        return {"ok": True}

    # ---------- users ----------
    @router.get("/api/users", tags=["users"])
    def list_users(
        db: Session = Depends(get_db),
        principal: Principal = Depends(current),
        q: str | None = None,
        page: int | None = Query(default=None),
        page_size: int | None = Query(default=None),
    ):
        principal.require("users.manage")
        query = db.query(User).options(selectinload(User.role)).order_by(User.username)
        if q:
            like = f"%{q.strip()}%"
            from sqlalchemy import or_
            query = query.filter(or_(User.username.ilike(like), User.display_name.ilike(like), User.email.ilike(like)))
        if page is None:
            return [_user_dict(u) for u in query.all()]
        page, page_size = clamp_page(page, page_size)
        total = query.count()
        rows = query.offset((page - 1) * page_size).limit(page_size).all()
        return page_result([_user_dict(u) for u in rows], total, page, page_size)

    @router.post("/api/users", tags=["users"], status_code=201)
    def create_user(body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        seed.ensure_builtin_roles(db)
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        if not username or not password:
            raise HTTPException(400, "username and password are required")
        if len(password) < 8:
            raise HTTPException(400, "Password must be at least 8 characters")
        if db.query(User).filter(User.username == username).one_or_none():
            raise HTTPException(409, f"User {username!r} already exists")
        role = None
        if body.get("role_id"):
            role = db.get(Role, body["role_id"])
        elif body.get("role"):
            role = db.query(Role).filter(Role.name == body["role"]).one_or_none()
        if role is None:
            role = db.query(Role).filter(Role.name == "technician").one_or_none()
        if role is None:
            raise HTTPException(400, "No role available")
        now = utcnow_iso()
        user = User(
            id=body.get("id") or uid("usr"),
            username=username,
            display_name=body.get("display_name") or body.get("displayName") or username,
            email=body.get("email") or "",
            password_hash=hash_password(password),
            role_id=role.id,
            site_id=body.get("site_id") or None,
            area_id=body.get("area_id") or None,
            is_active=0 if body.get("is_active") in (False, 0, "0") else 1,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        service.audit(db, "create", actor=principal.actor, user_id=principal.user_id, entity_type="user", entity_id=user.id)
        db.commit()
        user = db.query(User).options(selectinload(User.role)).filter(User.id == user.id).one()
        return _user_dict(user)

    @router.get("/api/users/{uid_}", tags=["users"])
    def get_user(uid_: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        if principal.user_id != uid_:
            principal.require("users.manage")
        user = db.query(User).options(selectinload(User.role)).filter(User.id == uid_).one_or_none()
        if not user:
            raise HTTPException(404, "User not found")
        return _user_dict(user)

    @router.patch("/api/users/{uid_}", tags=["users"])
    def patch_user(uid_: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        user = db.get(User, uid_)
        if not user:
            raise HTTPException(404, "User not found")
        if "display_name" in body or "displayName" in body:
            user.display_name = body.get("display_name") or body.get("displayName") or user.display_name
        if "email" in body:
            user.email = body.get("email") or ""
        if "site_id" in body:
            user.site_id = body.get("site_id") or None
        if "area_id" in body:
            user.area_id = body.get("area_id") or None
        if "is_active" in body:
            user.is_active = 1 if body.get("is_active") else 0
        if body.get("role_id") or body.get("role"):
            role = db.get(Role, body["role_id"]) if body.get("role_id") else db.query(Role).filter(Role.name == body["role"]).one_or_none()
            if not role:
                raise HTTPException(400, "Role not found")
            user.role_id = role.id
        if body.get("password"):
            if len(body["password"]) < 8:
                raise HTTPException(400, "Password must be at least 8 characters")
            user.password_hash = hash_password(body["password"])
        user.updated_at = utcnow_iso()
        service.audit(db, "update", actor=principal.actor, user_id=principal.user_id, entity_type="user", entity_id=user.id)
        db.commit()
        user = db.query(User).options(selectinload(User.role)).filter(User.id == user.id).one()
        return _user_dict(user)

    @router.delete("/api/users/{uid_}", tags=["users"])
    def delete_user(uid_: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        user = db.get(User, uid_)
        if not user:
            raise HTTPException(404, "User not found")
        if principal.user_id == uid_:
            raise HTTPException(400, "You cannot delete your own account")
        db.delete(user)
        service.audit(db, "delete", actor=principal.actor, user_id=principal.user_id, entity_type="user", entity_id=uid_)
        db.commit()
        return {"ok": True}

    @router.post("/api/users/{uid_}/api-keys", tags=["users"], status_code=201)
    def create_api_key(uid_: str, body: dict[str, Any] | None = None, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        body = body or {}
        user = db.get(User, uid_)
        if not user:
            raise HTTPException(404, "User not found")
        raw, key_hash, prefix = new_api_key()
        row = ApiKey(
            id=uid("key"),
            user_id=user.id,
            role_id=body.get("role_id") or user.role_id,
            name=body.get("name") or "API key",
            key_hash=key_hash,
            prefix=prefix,
            created_at=utcnow_iso(),
            revoked=0,
        )
        db.add(row)
        service.audit(db, "create", actor=principal.actor, user_id=principal.user_id, entity_type="api_key", entity_id=row.id)
        db.commit()
        return {"id": row.id, "name": row.name, "prefix": prefix, "key": raw, "created_at": row.created_at}

    @router.get("/api/users/{uid_}/api-keys", tags=["users"])
    def list_api_keys(uid_: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        rows = db.query(ApiKey).filter(ApiKey.user_id == uid_).order_by(ApiKey.created_at.desc()).all()
        return [
            {
                "id": k.id, "name": k.name, "prefix": k.prefix, "created_at": k.created_at,
                "last_used_at": k.last_used_at, "revoked": bool(k.revoked),
            }
            for k in rows
        ]

    @router.delete("/api/users/{uid_}/api-keys/{kid}", tags=["users"])
    def revoke_api_key(uid_: str, kid: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        row = db.get(ApiKey, kid)
        if not row or row.user_id != uid_:
            raise HTTPException(404, "API key not found")
        row.revoked = 1
        db.commit()
        return {"ok": True}

    # ---------- sites / areas (optional permission scope) ----------
    @router.get("/api/sites", tags=["users"])
    def list_sites(db: Session = Depends(get_db), principal: Principal = Depends(current)):
        if not (principal.can("users.manage") or principal.can("equipment.read")):
            principal.require("equipment.read")
        rows = db.query(Site).options(selectinload(Site.areas)).order_by(Site.name).all()
        return [_site_dict(s) for s in rows]

    @router.post("/api/sites", tags=["users"], status_code=201)
    def create_site(body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        now = utcnow_iso()
        site = Site(id=body.get("id") or uid("site"), name=name, code=(body.get("code") or "") or None, created_at=now)
        db.add(site)
        db.flush()
        for area in body.get("areas") or []:
            db.add(Area(
                id=area.get("id") or uid("area"),
                site_id=site.id,
                name=area.get("name") or "Area",
                code=area.get("code") or None,
                created_at=now,
            ))
        db.commit()
        site = db.query(Site).options(selectinload(Site.areas)).filter(Site.id == site.id).one()
        return _site_dict(site)

    @router.post("/api/sites/{sid}/areas", tags=["users"], status_code=201)
    def create_area(sid: str, body: dict[str, Any], db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        site = db.get(Site, sid)
        if not site:
            raise HTTPException(404, "Site not found")
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        area = Area(id=body.get("id") or uid("area"), site_id=sid, name=name, code=body.get("code") or None, created_at=utcnow_iso())
        db.add(area)
        db.commit()
        db.refresh(area)
        return {"id": area.id, "site_id": area.site_id, "name": area.name, "code": area.code or "", "created_at": area.created_at}

    @router.delete("/api/sites/{sid}", tags=["users"])
    def delete_site(sid: str, db: Session = Depends(get_db), principal: Principal = Depends(current)):
        principal.require("users.manage")
        site = db.get(Site, sid)
        if not site:
            raise HTTPException(404, "Site not found")
        db.delete(site)
        db.commit()
        return {"ok": True}

    return router
