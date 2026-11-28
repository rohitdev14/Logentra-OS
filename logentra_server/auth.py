"""Users, roles, password hashing, session tokens, and permission checks."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Header, HTTPException, Request
from sqlalchemy.orm import Session

from logentra_server import authz
from logentra_server.utils import uid, utcnow_iso

# ---------------------------------------------------------------------------
# Permission catalogue — used by built-in and custom roles
# ---------------------------------------------------------------------------

PERMISSIONS: list[str] = [
    "equipment.read",
    "equipment.write",
    "records.read",
    "records.write",
    "records.approve",
    "templates.read",
    "templates.write",
    "readings.read",
    "readings.write",
    "annotations.write",
    "signoff",
    "workorders.read",
    "workorders.create",
    "workorders.assign",
    "workorders.close",
    "documents.read",
    "documents.upload",
    "documents.approve",
    "routes.manage",
    "users.manage",
    "audit.read",
    "webhooks.manage",
    "settings.manage",
    "shiftlog.read",
    "shiftlog.write",
    "handover.act",
    "shifts.manage",
]

ALL_PERMISSIONS: frozenset[str] = frozenset(PERMISSIONS)

_READ = (
    "equipment.read", "records.read", "templates.read", "readings.read", "workorders.read", "documents.read", "shiftlog.read"
)
_TECH = _READ + (
    "records.write", "readings.write", "annotations.write", "signoff", "workorders.create", "documents.upload",
    "shiftlog.write", "handover.act",
)
_SUPER = _TECH + (
    "equipment.write", "templates.write", "workorders.assign", "workorders.close",
    "records.approve", "documents.approve", "audit.read", "webhooks.manage", "shifts.manage",
)

BUILTIN_ROLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "admin": ("Full access, including users and settings", tuple(PERMISSIONS)),
    "supervisor": ("Operate a site: equipment, records, work orders, sign-off", _SUPER),
    "technician": ("Log readings, fill records, raise work orders", _TECH),
    "viewer": ("Read-only access", _READ),
}

PBKDF2_ITERATIONS = 600_000
TOKEN_BYTES = 32
API_KEY_PREFIX = "lbk_"


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iter_s, salt_hex, hash_hex = stored.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_api_key() -> tuple[str, str, str]:
    """Return (raw_key, key_hash, prefix) — raw_key is shown only once."""
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_token(raw), raw[:11]


@dataclass
class Principal:
    mode: str  # open | api_key | user | legacy_key
    username: str = "api"
    user_id: str | None = None
    role_name: str = "admin"
    role_id: str | None = None
    permissions: set[str] = field(default_factory=lambda: set(ALL_PERMISSIONS))
    site_id: str | None = None
    area_id: str | None = None
    api_key_id: str | None = None
    is_active: bool = True

    @property
    def actor(self) -> str:
        return self.username or "api"

    def can(self, perm: str) -> bool:
        if self.mode == "open":
            return True
        return perm in self.permissions

    def require(self, *perms: str) -> None:
        authz.require(self, *perms, scoped=True)

    @property
    def scoped(self) -> bool:
        return bool(self.site_id or self.area_id)

    def allows_scope(self, site_id, area_id=None) -> bool:
        return authz.allows_location(self, site_id, area_id)

    def require_global(self) -> None:
        authz.require_global(self)

    def to_public(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "id": self.user_id,
            "username": self.username,
            "role": self.role_name,
            "role_id": self.role_id,
            "permissions": sorted(self.permissions),
            "site_id": self.site_id,
            "area_id": self.area_id,
        }


OPEN_PRINCIPAL = Principal(mode="open", username="api", role_name="admin")


def parse_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    kind, _, rest = authorization.partition(" ")
    if kind.lower() == "bearer" and rest.strip():
        return rest.strip()
    return None


def _role_perms(role) -> set[str]:
    from logentra_server.utils import loads
    raw = loads(role.permissions, []) or []
    return {p for p in raw if p in ALL_PERMISSIONS}


def principal_from_user(user, role=None) -> Principal:
    role = role or user.role
    perms = _role_perms(role) if role else set()
    if role and role.name == "admin":
        perms = set(ALL_PERMISSIONS)
    return Principal(
        mode="user",
        username=user.username,
        user_id=user.id,
        role_name=role.name if role else "viewer",
        role_id=user.role_id,
        permissions=perms,
        site_id=user.site_id,
        area_id=user.area_id,
        is_active=bool(user.is_active),
    )


def authenticate(
    db: Session,
    settings,
    *,
    x_api_key: str | None,
    authorization: str | None,
    actor_hint: str | None = None,
    request_path: str = "",
) -> Principal:
    """Resolve the caller. `settings.auth_mode` is `open` (default) or `users`."""
    from logentra_server.models import ApiKey, SessionToken, User

    raw = (x_api_key or "").strip() or parse_bearer(authorization) or ""
    mode = (getattr(settings, "auth_mode", "open") or "open").lower()

    # User-bound API keys (always recognised, in either mode)
    if raw:
        key_row = db.query(ApiKey).filter(ApiKey.key_hash == hash_token(raw), ApiKey.revoked == 0).one_or_none()
        if key_row:
            if (key_row.expires_at and key_row.expires_at <= utcnow_iso()) or (
                settings.profile == "production" and not key_row.expires_at
            ):
                raise HTTPException(401, "API key expired")
            user = db.get(User, key_row.user_id) if key_row.user_id else None
            if user and not user.is_active:
                raise HTTPException(status_code=401, detail="User is disabled")
            key_row.last_used_at = utcnow_iso()
            if user:
                p = principal_from_user(user)
            else:
                # key bound only to a role
                from logentra_server.models import Role
                role = db.get(Role, key_row.role_id) if key_row.role_id else None
                perms = _role_perms(role) if role else set()
                p = Principal(
                    mode="api_key",
                    username=key_row.name or "api-key",
                    role_name=role.name if role else "admin",
                    role_id=key_row.role_id,
                    permissions=perms,
                )
            p.mode = "api_key"
            p.api_key_id = key_row.id
            return p

        # Opaque session token
        tok = db.query(SessionToken).filter(SessionToken.token_hash == hash_token(raw)).one_or_none()
        if tok:
            phone_paths = ("/q", "/api/qr", "/api/readings/photos")
            if tok.kind == "phone" and not any(
                request_path == path or request_path.startswith(path + "/") for path in phone_paths
            ):
                raise HTTPException(401, "Phone sessions are limited to QR quick entry")
            if tok.revoked_at:
                raise HTTPException(status_code=401, detail="Session revoked")
            if tok.expires_at and tok.expires_at <= utcnow_iso():
                raise HTTPException(status_code=401, detail="Session expired")
            user = db.get(User, tok.user_id)
            if not user or not user.is_active:
                raise HTTPException(status_code=401, detail="Invalid or missing credentials")
            tok.last_used_at = utcnow_iso()
            return principal_from_user(user)

    # Legacy global API key (config / LOGENTRA_API_KEY, or deprecated LOGBOOK_API_KEY)
    expected = getattr(settings, "api_key", "") or ""
    if expected:
        if raw and hmac.compare_digest(raw, expected):
            return Principal(mode="legacy_key", username=actor_hint or "api", role_name="admin")
        if mode != "open":
            # in users mode a configured legacy key is still accepted, but required if no user token
            pass
        elif not raw:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
        else:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    if mode == "users":
        if not raw:
            raise HTTPException(status_code=401, detail="Authentication required")
        raise HTTPException(status_code=401, detail="Invalid or missing credentials")

    # open / single-user mode
    return Principal(mode="open", username=actor_hint or "api", role_name="admin")


def create_session(db: Session, user_id: str, *, days: int = 7,
                   kind: str = "browser", label: str | None = None) -> tuple[str, str]:
    from logentra_server.models import SessionToken

    raw = new_session_token()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=days)
    expires_s = expires.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    row = SessionToken(
        id=uid("ses"),
        user_id=user_id,
        token_hash=hash_token(raw),
        kind=kind,
        label=label,
        created_at=utcnow_iso(),
        expires_at=expires_s,
        last_used_at=utcnow_iso(),
    )
    db.add(row)
    db.flush()
    return raw, expires_s


def revoke_session(db: Session, raw_token: str) -> int:
    from logentra_server.models import SessionToken

    n = db.query(SessionToken).filter(SessionToken.token_hash == hash_token(raw_token)).delete()
    return n


def make_auth_dependency(settings, SessionLocal):
    """FastAPI dependency factory closed over app settings and the session factory."""

    def get_principal(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        authorization: str | None = Header(default=None),
    ) -> Principal:
        db = SessionLocal()
        try:
            principal = authenticate(
                db,
                settings,
                x_api_key=x_api_key,
                authorization=authorization,
                request_path=request.url.path,
                actor_hint=request.headers.get("X-Logentra-Actor") or request.headers.get("X-Logbook-Actor"),
            )
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        request.state.principal = principal
        return principal

    return get_principal


def require_permissions(*perms: str):
    """Return a dependency that authenticates and checks permissions."""

    def _checker(request: Request) -> Principal:
        principal: Principal | None = getattr(request.state, "principal", None)
        if principal is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        principal.require(*perms)
        return principal

    return _checker
