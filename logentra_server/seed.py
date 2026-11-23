"""Bootstrap built-in roles and the first admin user."""
from __future__ import annotations

from sqlalchemy.orm import Session

from logentra_server.auth import BUILTIN_ROLES, hash_password
from logentra_server.models import ApprovalRoute, ApprovalRouteStep, Role, User
from logentra_server.utils import dumps, uid, utcnow_iso


def ensure_builtin_roles(db: Session) -> dict[str, Role]:
    now = utcnow_iso()
    out: dict[str, Role] = {}
    for name, (desc, perms) in BUILTIN_ROLES.items():
        row = db.query(Role).filter(Role.name == name).one_or_none()
        payload = dumps(list(perms))
        if row is None:
            row = Role(
                id=uid("role"),
                name=name,
                description=desc,
                built_in=1,
                permissions=payload,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        else:
            # keep built-in permission sets in sync with the catalogue
            row.description = desc
            row.built_in = 1
            row.permissions = payload
            row.updated_at = now
        out[name] = row
    db.flush()
    return out


def create_admin_user(
    db: Session,
    *,
    username: str,
    password: str,
    display_name: str | None = None,
    email: str | None = None,
) -> User:
    if not username or not password:
        raise ValueError("username and password are required")
    existing = db.query(User).filter(User.username == username).one_or_none()
    if existing:
        raise ValueError(f"User {username!r} already exists")
    roles = ensure_builtin_roles(db)
    now = utcnow_iso()
    user = User(
        id=uid("usr"),
        username=username.strip(),
        display_name=display_name or username,
        email=email or "",
        password_hash=hash_password(password),
        role_id=roles["admin"].id,
        is_active=1,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()
    return user


def maybe_bootstrap_admin(db: Session, settings) -> User | None:
    """Create the first admin from env/config when auth_mode=users and no users exist."""
    ensure_builtin_roles(db)
    username = (getattr(settings, "bootstrap_admin_username", "") or "").strip()
    password = getattr(settings, "bootstrap_admin_password", "") or ""
    if not username or not password:
        return None
    if db.query(User).count() > 0:
        return None
    return create_admin_user(db, username=username, password=password)



def ensure_default_routes(db: Session) -> list[ApprovalRoute]:
    """Seed a standard Supervisor → Admin route when none exist."""
    existing = db.query(ApprovalRoute).count()
    if existing:
        return db.query(ApprovalRoute).all()
    now = utcnow_iso()
    route = ApprovalRoute(
        id=uid("rt"),
        name="Standard review",
        description="Supervisor review, then admin sign-off. Matches any document type.",
        document_type=None,
        site_id=None,
        enabled=1,
        created_at=now,
        updated_at=now,
    )
    db.add(route)
    db.flush()
    db.add(ApprovalRouteStep(
        id=uid("rst"), route_id=route.id, sort_order=0,
        assignee_type="role", role_name="supervisor", label="Supervisor",
    ))
    db.add(ApprovalRouteStep(
        id=uid("rst"), route_id=route.id, sort_order=1,
        assignee_type="role", role_name="admin", label="Admin",
    ))
    # A slightly more specific commissioning route with the same steps
    comm = ApprovalRoute(
        id=uid("rt"),
        name="Commissioning test sheet",
        description="Used for commissioning and start-up test sheets.",
        document_type="commissioning_test_sheet",
        site_id=None,
        enabled=1,
        created_at=now,
        updated_at=now,
    )
    db.add(comm)
    db.flush()
    db.add(ApprovalRouteStep(
        id=uid("rst"), route_id=comm.id, sort_order=0,
        assignee_type="role", role_name="supervisor", label="Supervisor",
    ))
    db.add(ApprovalRouteStep(
        id=uid("rst"), route_id=comm.id, sort_order=1,
        assignee_type="role", role_name="admin", label="Admin",
    ))
    db.flush()
    return [route, comm]
