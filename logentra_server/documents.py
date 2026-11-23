"""Document versions, approval routes, and the approval workflow."""
from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from logentra_server import events, service
from logentra_server.auth import Principal
from logentra_server.models import (
    ApprovalRoute,
    ApprovalRouteStep,
    Document,
    DocumentApprovalStep,
    DocumentComment,
    DocumentVersion,
    Equipment,
    Record,
    User,
    WorkOrder,
)
from logentra_server.pagination import clamp_page, page_meta
from logentra_server.utils import uid, utcnow_iso

ROOT = Path(__file__).resolve().parent.parent
ATTACH_DIR = ROOT / "data" / "attachments" / "documents"

STATUSES = ("draft", "in_review", "approved", "rejected", "returned")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    base = Path(name or "upload.bin").name
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "upload.bin"
    return cleaned[:180]


def store_file(document_id: str, version_no: int, filename: str, data: bytes) -> tuple[str, str]:
    """Write bytes to disk; return (relative_storage_path, sha256_hex)."""
    digest = hashlib.sha256(data).hexdigest()
    dest_dir = ATTACH_DIR / document_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = dest_dir / f"v{version_no}_{digest[:12]}_{safe_filename(filename)}"
    stored.write_bytes(data)
    return str(stored.relative_to(ROOT)), digest


def absolute_path(storage_path: str) -> Path:
    path = Path(storage_path)
    if not path.is_absolute():
        path = ROOT / path
    return path


def guess_mime(filename: str, declared: str | None = None) -> str:
    if declared and declared != "application/octet-stream":
        return declared
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or declared or "application/octet-stream"


def _load(db: Session, doc_id: str) -> Document | None:
    return (
        db.query(Document)
        .options(
            selectinload(Document.versions),
            selectinload(Document.approval_steps),
            selectinload(Document.comments),
        )
        .filter(Document.id == doc_id)
        .one_or_none()
    )


def get_document(db: Session, doc_id: str) -> Document | None:
    return _load(db, doc_id)


def current_version(doc: Document) -> DocumentVersion | None:
    if doc.current_version_id:
        for v in doc.versions or []:
            if v.id == doc.current_version_id:
                return v
    if doc.versions:
        return max(doc.versions, key=lambda v: v.version_no)
    return None


def current_cycle_steps(doc: Document) -> list[DocumentApprovalStep]:
    ver = current_version(doc)
    steps = list(doc.approval_steps or [])
    if ver:
        matched = [s for s in steps if s.version_id == ver.id]
        if matched:
            return sorted(matched, key=lambda s: s.sort_order)
    # fall back to latest sort groups
    return sorted(steps, key=lambda s: s.sort_order)


def current_pending_step(doc: Document) -> DocumentApprovalStep | None:
    steps = current_cycle_steps(doc)
    pending = [s for s in steps if s.status == "pending"]
    if pending:
        return pending[0]
    if 0 <= doc.current_step_index < len(steps):
        step = steps[doc.current_step_index]
        if step.status == "pending":
            return step
    return None


def _user_name(db: Session, user_id: str | None, fallback: str) -> str:
    if not user_id:
        return fallback
    u = db.get(User, user_id)
    if not u:
        return fallback
    return u.display_name or u.username or fallback


def step_label(step) -> str:
    if getattr(step, "label", None):
        return step.label
    if getattr(step, "assignee_type", "") == "user":
        return "Named approver"
    role = (getattr(step, "role_name", None) or "approver").replace("_", " ")
    return role[:1].upper() + role[1:]


def can_act_on_step(principal: Principal, step: DocumentApprovalStep) -> bool:
    if principal.mode in ("open", "legacy_key"):
        return True
    if not principal.can("documents.approve"):
        return False
    if principal.role_name == "admin" or principal.can("users.manage"):
        return True
    if step.assignee_type == "user":
        return bool(principal.user_id and principal.user_id == step.user_id)
    if step.assignee_type == "role":
        return (principal.role_name or "").lower() == (step.role_name or "").lower()
    return False


def is_waiting_on(principal: Principal, step: DocumentApprovalStep) -> bool:
    """Whether this step is in this principal's approvals queue (not admin-wildcard)."""
    if principal.mode in ("open", "legacy_key"):
        return True
    if step.assignee_type == "user":
        return bool(principal.user_id and principal.user_id == step.user_id)
    if step.assignee_type == "role":
        return (principal.role_name or "").lower() == (step.role_name or "").lower()
    return False


def add_comment(
    db: Session,
    doc: Document,
    *,
    body: str,
    kind: str = "comment",
    event: str | None = None,
    actor: str = "api",
    user_id: str | None = None,
    version_id: str | None = None,
) -> DocumentComment:
    row = DocumentComment(
        id=uid("dcm"),
        document_id=doc.id,
        version_id=version_id or doc.current_version_id,
        kind=kind,
        event=event,
        author_id=user_id,
        author=actor,
        body=body,
        created_at=utcnow_iso(),
    )
    db.add(row)
    return row


def _fire(db: Session, event: str, doc: Document, extra: dict[str, Any] | None = None) -> None:
    payload = {
        "event": event,
        "at": utcnow_iso(),
        "document_id": doc.id,
        "title": doc.title,
        "status": doc.status,
        "document_type": doc.document_type,
        "equipment_id": doc.equipment_id,
        "record_id": doc.record_id,
        "work_order_id": doc.work_order_id,
        "route_id": doc.route_id,
        "version_id": doc.current_version_id,
        "locked": bool(doc.locked),
    }
    if extra:
        payload.update(extra)
    events.deliver(db, event, payload)


def route_to_dict(route: ApprovalRoute) -> dict[str, Any]:
    steps = sorted(route.steps or [], key=lambda s: s.sort_order)
    return {
        "id": route.id,
        "name": route.name,
        "description": route.description,
        "document_type": route.document_type,
        "site_id": route.site_id,
        "enabled": bool(route.enabled),
        "created_at": route.created_at,
        "updated_at": route.updated_at,
        "steps": [
            {
                "id": s.id,
                "sort_order": s.sort_order,
                "assignee_type": s.assignee_type,
                "role_name": s.role_name,
                "user_id": s.user_id,
                "label": s.label or step_label(s),
            }
            for s in steps
        ],
    }


def version_to_dict(v: DocumentVersion) -> dict[str, Any]:
    return {
        "id": v.id,
        "version_no": v.version_no,
        "filename": v.filename,
        "mime": v.mime,
        "size": v.size,
        "sha256": v.sha256,
        "storage_path": v.storage_path,
        "uploaded_by": v.uploaded_by,
        "uploaded_by_id": v.uploaded_by_id,
        "created_at": v.created_at,
    }


def step_to_dict(s: DocumentApprovalStep) -> dict[str, Any]:
    return {
        "id": s.id,
        "version_id": s.version_id,
        "sort_order": s.sort_order,
        "assignee_type": s.assignee_type,
        "role_name": s.role_name,
        "user_id": s.user_id,
        "label": s.label or step_label(s),
        "status": s.status,
        "actor_id": s.actor_id,
        "actor_name": s.actor_name,
        "comment": s.comment,
        "acted_at": s.acted_at,
    }


def comment_to_dict(c: DocumentComment) -> dict[str, Any]:
    return {
        "id": c.id,
        "version_id": c.version_id,
        "kind": c.kind,
        "event": c.event,
        "author_id": c.author_id,
        "author": c.author,
        "body": c.body,
        "created_at": c.created_at,
    }


def to_dict(doc: Document, db: Session | None = None, *, detail: bool = False) -> dict[str, Any]:
    ver = current_version(doc)
    steps = current_cycle_steps(doc)
    pending = current_pending_step(doc)
    tag = None
    if db is not None and doc.equipment_id:
        eq = db.get(Equipment, doc.equipment_id)
        tag = eq.tag if eq else None
    out: dict[str, Any] = {
        "id": doc.id,
        "title": doc.title,
        "document_type": doc.document_type,
        "status": doc.status,
        "equipment_id": doc.equipment_id,
        "equipment_tag": tag,
        "record_id": doc.record_id,
        "work_order_id": doc.work_order_id,
        "site_id": doc.site_id,
        "route_id": doc.route_id,
        "current_version_id": doc.current_version_id,
        "current_step_index": doc.current_step_index,
        "locked": bool(doc.locked),
        "submitted_by": doc.submitted_by,
        "submitted_by_id": doc.submitted_by_id,
        "submitted_at": doc.submitted_at,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "current_version": version_to_dict(ver) if ver else None,
        "pending_step": step_to_dict(pending) if pending else None,
    }
    if detail:
        out["versions"] = [version_to_dict(v) for v in sorted(doc.versions or [], key=lambda x: x.version_no)]
        out["steps"] = [step_to_dict(s) for s in steps]
        # include earlier-cycle steps in the audit trail via comments; also expose all steps
        out["all_steps"] = [step_to_dict(s) for s in sorted(doc.approval_steps or [], key=lambda s: (s.created_at if hasattr(s, "created_at") else "", s.sort_order))]
        # DocumentApprovalStep has no created_at — sort by version then order
        out["all_steps"] = [step_to_dict(s) for s in sorted(doc.approval_steps or [], key=lambda s: ((s.version_id or ""), s.sort_order))]
        out["comments"] = [comment_to_dict(c) for c in sorted(doc.comments or [], key=lambda c: c.created_at)]
        if db is not None and doc.route_id:
            route = db.get(ApprovalRoute, doc.route_id)
            if route:
                db.refresh(route)
                # ensure steps loaded
                _ = route.steps
                out["route"] = route_to_dict(route)
    return out


def list_documents(
    db: Session,
    *,
    status: str | None = None,
    equipment_id: str | None = None,
    record_id: str | None = None,
    work_order_id: str | None = None,
    document_type: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 50,
    principal: Principal | None = None,
) -> dict[str, Any]:
    query = db.query(Document)
    if principal and principal.site_id:
        query = query.filter(or_(Document.site_id == principal.site_id, Document.site_id.is_(None)))
    if status:
        query = query.filter(Document.status == status)
    if equipment_id:
        query = query.filter(Document.equipment_id == equipment_id)
    if record_id:
        query = query.filter(Document.record_id == record_id)
    if work_order_id:
        query = query.filter(Document.work_order_id == work_order_id)
    if document_type:
        query = query.filter(Document.document_type == document_type)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Document.title.ilike(like), Document.document_type.ilike(like), Document.id.ilike(like)))
    total = query.count()
    page, page_size = clamp_page(page, page_size)
    rows = query.order_by(Document.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    # load collections for pending_step
    ids = [r.id for r in rows]
    if ids:
        db.query(Document).options(
            selectinload(Document.versions),
            selectinload(Document.approval_steps),
        ).filter(Document.id.in_(ids)).all()
    return {
        "items": [to_dict(r, db) for r in rows],
        **page_meta(total, page, page_size),
    }


def list_my_queue(db: Session, principal: Principal, *, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    query = (
        db.query(Document)
        .options(selectinload(Document.versions), selectinload(Document.approval_steps))
        .filter(Document.status == "in_review", Document.locked == 0)
    )
    if principal.site_id:
        query = query.filter(or_(Document.site_id == principal.site_id, Document.site_id.is_(None)))
    rows = query.order_by(Document.updated_at.asc()).all()
    waiting = []
    for doc in rows:
        step = current_pending_step(doc)
        if step and is_waiting_on(principal, step):
            waiting.append(doc)
    total = len(waiting)
    page, page_size = clamp_page(page, page_size)
    sliced = waiting[(page - 1) * page_size: page * page_size]
    return {
        "items": [to_dict(r, db) for r in sliced],
        **page_meta(total, page, page_size),
    }


def list_routes(db: Session, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    q = db.query(ApprovalRoute).options(selectinload(ApprovalRoute.steps))
    if enabled_only:
        q = q.filter(ApprovalRoute.enabled == 1)
    rows = q.order_by(ApprovalRoute.name.asc()).all()
    return [route_to_dict(r) for r in rows]


def get_route(db: Session, route_id: str) -> ApprovalRoute | None:
    return (
        db.query(ApprovalRoute)
        .options(selectinload(ApprovalRoute.steps))
        .filter(ApprovalRoute.id == route_id)
        .one_or_none()
    )


def match_route(db: Session, *, route_id: str | None = None, document_type: str | None = None, site_id: str | None = None) -> ApprovalRoute | None:
    if route_id:
        route = get_route(db, route_id)
        if not route:
            raise ValueError("Approval route not found")
        if not route.enabled:
            raise ValueError("Approval route is disabled")
        return route
    rows = (
        db.query(ApprovalRoute)
        .options(selectinload(ApprovalRoute.steps))
        .filter(ApprovalRoute.enabled == 1)
        .all()
    )
    def score(r: ApprovalRoute) -> int:
        s = 0
        if r.document_type:
            if document_type and r.document_type == document_type:
                s += 4
            else:
                return -1
        if r.site_id:
            if site_id and r.site_id == site_id:
                s += 2
            else:
                return -1
        return s
    ranked = [(score(r), r) for r in rows]
    ranked = [(s, r) for s, r in ranked if s >= 0]
    if not ranked:
        return None
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1]


def _replace_route_steps(db: Session, route: ApprovalRoute, steps: list[dict[str, Any]]) -> None:
    route.steps.clear()
    db.flush()
    for i, raw in enumerate(steps or []):
        assignee_type = (raw.get("assignee_type") or raw.get("assigneeType") or "role").lower()
        if assignee_type not in ("role", "user"):
            raise ValueError("assignee_type must be 'role' or 'user'")
        role_name = (raw.get("role_name") or raw.get("roleName") or raw.get("role") or "").strip() or None
        user_id = raw.get("user_id") or raw.get("userId")
        if assignee_type == "role" and not role_name:
            raise ValueError("role steps need a role_name")
        if assignee_type == "user" and not user_id:
            raise ValueError("user steps need a user_id")
        route.steps.append(
            ApprovalRouteStep(
                id=uid("rst"),
                route_id=route.id,
                sort_order=int(raw.get("sort_order", i)),
                assignee_type=assignee_type,
                role_name=role_name,
                user_id=user_id,
                label=(raw.get("label") or "").strip() or None,
            )
        )


def create_route(db: Session, body: dict[str, Any], *, actor: str = "api", user_id: str | None = None) -> ApprovalRoute:
    name = (body.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    now = utcnow_iso()
    route = ApprovalRoute(
        id=uid("rt"),
        name=name,
        description=(body.get("description") or "").strip() or None,
        document_type=(body.get("document_type") or body.get("documentType") or "").strip() or None,
        site_id=body.get("site_id") or body.get("siteId"),
        enabled=1 if body.get("enabled", True) else 0,
        created_at=now,
        updated_at=now,
    )
    db.add(route)
    db.flush()
    _replace_route_steps(db, route, body.get("steps") or [])
    if not route.steps:
        raise ValueError("A route needs at least one step")
    service.audit(db, "create", actor=actor, user_id=user_id, entity_type="approval_route", entity_id=route.id, detail={"name": name})
    db.commit()
    db.refresh(route)
    return get_route(db, route.id) or route


def update_route(db: Session, route: ApprovalRoute, body: dict[str, Any], *, actor: str = "api", user_id: str | None = None) -> ApprovalRoute:
    if "name" in body and body["name"]:
        route.name = str(body["name"]).strip()
    if "description" in body:
        route.description = (body.get("description") or "").strip() or None
    if "document_type" in body or "documentType" in body:
        route.document_type = (body.get("document_type") or body.get("documentType") or "").strip() or None
    if "site_id" in body or "siteId" in body:
        route.site_id = body.get("site_id") or body.get("siteId")
    if "enabled" in body:
        route.enabled = 1 if body.get("enabled") else 0
    if "steps" in body:
        _replace_route_steps(db, route, body.get("steps") or [])
        if not route.steps:
            raise ValueError("A route needs at least one step")
    route.updated_at = utcnow_iso()
    service.audit(db, "update", actor=actor, user_id=user_id, entity_type="approval_route", entity_id=route.id)
    db.commit()
    return get_route(db, route.id) or route


def delete_route(db: Session, route: ApprovalRoute, *, actor: str = "api", user_id: str | None = None) -> None:
    in_use = db.query(Document).filter(Document.route_id == route.id, Document.status == "in_review").count()
    if in_use:
        raise ValueError("Cannot delete a route while documents are in review on it")
    service.audit(db, "delete", actor=actor, user_id=user_id, entity_type="approval_route", entity_id=route.id)
    db.delete(route)
    db.commit()


def _validate_links(db: Session, body: dict[str, Any]) -> None:
    eq_id = body.get("equipment_id") or body.get("equipmentId")
    if eq_id and not db.get(Equipment, eq_id):
        raise ValueError("equipment not found")
    rec_id = body.get("record_id") or body.get("recordId")
    if rec_id and not db.get(Record, rec_id):
        raise ValueError("record not found")
    wo_id = body.get("work_order_id") or body.get("workOrderId")
    if wo_id and not db.get(WorkOrder, wo_id):
        raise ValueError("work order not found")


def create_document(
    db: Session,
    *,
    title: str,
    filename: str,
    data: bytes,
    mime: str | None = None,
    document_type: str | None = None,
    equipment_id: str | None = None,
    record_id: str | None = None,
    work_order_id: str | None = None,
    site_id: str | None = None,
    route_id: str | None = None,
    actor: str = "api",
    user_id: str | None = None,
) -> Document:
    title = (title or "").strip() or (filename or "Untitled document")
    if not data:
        raise ValueError("A file is required")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File exceeds the {MAX_UPLOAD_BYTES} byte limit")
    _validate_links(db, {
        "equipment_id": equipment_id, "record_id": record_id, "work_order_id": work_order_id,
    })
    now = utcnow_iso()
    doc = Document(
        id=uid("doc"),
        title=title,
        document_type=(document_type or "").strip() or None,
        status="draft",
        equipment_id=equipment_id,
        record_id=record_id,
        work_order_id=work_order_id,
        site_id=site_id,
        route_id=route_id,
        current_step_index=0,
        locked=0,
        created_at=now,
        updated_at=now,
    )
    db.add(doc)
    db.flush()
    path, digest = store_file(doc.id, 1, filename, data)
    ver = DocumentVersion(
        id=uid("dv"),
        document_id=doc.id,
        version_no=1,
        filename=safe_filename(filename),
        mime=guess_mime(filename, mime),
        size=len(data),
        sha256=digest,
        storage_path=path,
        uploaded_by=actor,
        uploaded_by_id=user_id,
        created_at=now,
    )
    db.add(ver)
    db.flush()
    doc.current_version_id = ver.id
    add_comment(db, doc, body=f"Uploaded {ver.filename} ({ver.size} bytes).", kind="system", event="uploaded", actor=actor, user_id=user_id, version_id=ver.id)
    service.audit(db, "create", actor=actor, user_id=user_id, entity_type="document", entity_id=doc.id, detail={"title": title})
    db.commit()
    return _load(db, doc.id) or doc


def add_version(
    db: Session,
    doc: Document,
    *,
    filename: str,
    data: bytes,
    mime: str | None = None,
    actor: str = "api",
    user_id: str | None = None,
) -> Document:
    if doc.locked or doc.status == "approved":
        raise ValueError("Approved documents are locked and cannot be changed")
    if doc.status not in ("draft", "returned"):
        raise ValueError("A new version can only be uploaded while the document is a draft or returned for changes")
    if not data:
        raise ValueError("A file is required")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File exceeds the {MAX_UPLOAD_BYTES} byte limit")
    next_no = max((v.version_no for v in (doc.versions or [])), default=0) + 1
    now = utcnow_iso()
    path, digest = store_file(doc.id, next_no, filename, data)
    ver = DocumentVersion(
        id=uid("dv"),
        document_id=doc.id,
        version_no=next_no,
        filename=safe_filename(filename),
        mime=guess_mime(filename, mime),
        size=len(data),
        sha256=digest,
        storage_path=path,
        uploaded_by=actor,
        uploaded_by_id=user_id,
        created_at=now,
    )
    db.add(ver)
    db.flush()
    doc.current_version_id = ver.id
    doc.updated_at = now
    add_comment(db, doc, body=f"Uploaded revision {next_no}: {ver.filename}.", kind="system", event="uploaded", actor=actor, user_id=user_id, version_id=ver.id)
    service.audit(db, "update", actor=actor, user_id=user_id, entity_type="document", entity_id=doc.id, detail={"version": next_no})
    db.commit()
    return _load(db, doc.id) or doc


def update_document(db: Session, doc: Document, body: dict[str, Any], *, actor: str = "api", user_id: str | None = None) -> Document:
    if doc.locked or doc.status == "approved":
        raise ValueError("Approved documents are locked and cannot be changed")
    if doc.status == "in_review":
        raise ValueError("Documents in review cannot be edited; return them for changes first")
    _validate_links(db, body)
    if "title" in body and body["title"]:
        doc.title = str(body["title"]).strip()
    if "document_type" in body or "documentType" in body:
        doc.document_type = (body.get("document_type") or body.get("documentType") or "").strip() or None
    for src, attr in (("equipment_id", "equipment_id"), ("equipmentId", "equipment_id"),
                      ("record_id", "record_id"), ("recordId", "record_id"),
                      ("work_order_id", "work_order_id"), ("workOrderId", "work_order_id"),
                      ("site_id", "site_id"), ("siteId", "site_id"),
                      ("route_id", "route_id"), ("routeId", "route_id")):
        if src in body:
            setattr(doc, attr, body.get(src) or None)
    doc.updated_at = utcnow_iso()
    service.audit(db, "update", actor=actor, user_id=user_id, entity_type="document", entity_id=doc.id)
    db.commit()
    return _load(db, doc.id) or doc


def _instantiate_steps(db: Session, doc: Document, route: ApprovalRoute, version_id: str) -> None:
    steps = sorted(route.steps or [], key=lambda s: s.sort_order)
    if not steps:
        raise ValueError("The selected route has no steps")
    for i, src in enumerate(steps):
        db.add(
            DocumentApprovalStep(
                id=uid("dst"),
                document_id=doc.id,
                version_id=version_id,
                sort_order=i,
                assignee_type=src.assignee_type,
                role_name=src.role_name,
                user_id=src.user_id,
                label=src.label or step_label(src),
                status="pending" if i == 0 else "waiting",
            )
        )
    doc.current_step_index = 0


def submit_document(
    db: Session,
    doc: Document,
    *,
    route_id: str | None = None,
    actor: str = "api",
    user_id: str | None = None,
) -> Document:
    if doc.locked or doc.status == "approved":
        raise ValueError("Approved documents are locked and cannot be submitted again")
    if doc.status == "in_review":
        raise ValueError("Document is already in review")
    if doc.status == "rejected":
        raise ValueError("Rejected documents cannot be resubmitted; upload a new document")
    if doc.status not in ("draft", "returned"):
        raise ValueError(f"Cannot submit a document in status {doc.status}")
    ver = current_version(doc)
    if not ver:
        raise ValueError("Upload a file before submitting")
    route = match_route(db, route_id=route_id or doc.route_id, document_type=doc.document_type, site_id=doc.site_id)
    if not route:
        raise ValueError("No matching approval route. Create a route or pass route_id.")
    now = utcnow_iso()
    doc.route_id = route.id
    doc.status = "in_review"
    doc.locked = 0
    doc.submitted_by = actor
    doc.submitted_by_id = user_id
    doc.submitted_at = now
    doc.updated_at = now
    _instantiate_steps(db, doc, route, ver.id)
    event = "document.submitted"
    verb = "Resubmitted" if any(s.version_id != ver.id for s in (doc.approval_steps or [])) else "Submitted"
    add_comment(
        db, doc,
        body=f"{verb} under route “{route.name}”.",
        kind="system", event="submitted", actor=actor, user_id=user_id, version_id=ver.id,
    )
    service.audit(db, "submit", actor=actor, user_id=user_id, entity_type="document", entity_id=doc.id, detail={"route_id": route.id})
    db.commit()
    doc = _load(db, doc.id) or doc
    _fire(db, event, doc, {"route_name": route.name})
    return _load(db, doc.id) or doc


def _require_comment(comment: str | None) -> str:
    text = (comment or "").strip()
    if not text:
        raise ValueError("A comment is required")
    return text


def approve_document(
    db: Session,
    doc: Document,
    principal: Principal,
    *,
    comment: str | None = None,
) -> Document:
    if doc.locked or doc.status == "approved":
        raise ValueError("Document is already approved and locked")
    if doc.status != "in_review":
        raise ValueError("Only documents in review can be approved")
    step = current_pending_step(doc)
    if not step:
        raise ValueError("No pending approval step")
    if not can_act_on_step(principal, step):
        raise PermissionError("You are not the assigned approver for this step")
    now = utcnow_iso()
    step.status = "approved"
    step.actor_id = principal.user_id
    step.actor_name = principal.actor
    step.comment = (comment or "").strip() or None
    step.acted_at = now
    add_comment(
        db, doc,
        body=step.comment or f"Approved by {principal.actor} ({step_label(step)}).",
        kind="system" if not step.comment else "comment",
        event="step_approved",
        actor=principal.actor, user_id=principal.user_id, version_id=doc.current_version_id,
    )
    steps = current_cycle_steps(doc)
    # find next waiting
    nxt = None
    for s in steps:
        if s.sort_order > step.sort_order and s.status == "waiting":
            nxt = s
            break
    if nxt is None:
        doc.status = "approved"
        doc.locked = 1
        doc.updated_at = now
        add_comment(
            db, doc,
            body="Final approval recorded. This version is locked read-only.",
            kind="system", event="approved", actor=principal.actor, user_id=principal.user_id,
        )
        service.audit(db, "approve", actor=principal.actor, user_id=principal.user_id, entity_type="document", entity_id=doc.id, detail={"final": True})
        db.commit()
        doc = _load(db, doc.id) or doc
        _fire(db, "document.step_approved", doc, {"step": step_to_dict(step)})
        _fire(db, "document.approved", doc)
        return _load(db, doc.id) or doc
    nxt.status = "pending"
    doc.current_step_index = nxt.sort_order
    doc.updated_at = now
    add_comment(
        db, doc,
        body=f"Routed to {step_label(nxt)}.",
        kind="system", event="step_approved", actor=principal.actor, user_id=principal.user_id,
    )
    service.audit(db, "approve", actor=principal.actor, user_id=principal.user_id, entity_type="document", entity_id=doc.id, detail={"next": step_label(nxt)})
    db.commit()
    doc = _load(db, doc.id) or doc
    _fire(db, "document.step_approved", doc, {"step": step_to_dict(step), "next_label": step_label(nxt)})
    return _load(db, doc.id) or doc


def reject_document(db: Session, doc: Document, principal: Principal, *, comment: str) -> Document:
    if doc.locked or doc.status == "approved":
        raise ValueError("Approved documents are locked")
    if doc.status != "in_review":
        raise ValueError("Only documents in review can be rejected")
    step = current_pending_step(doc)
    if not step:
        raise ValueError("No pending approval step")
    if not can_act_on_step(principal, step):
        raise PermissionError("You are not the assigned approver for this step")
    text = _require_comment(comment)
    now = utcnow_iso()
    step.status = "rejected"
    step.actor_id = principal.user_id
    step.actor_name = principal.actor
    step.comment = text
    step.acted_at = now
    doc.status = "rejected"
    doc.updated_at = now
    add_comment(db, doc, body=text, kind="comment", event="rejected", actor=principal.actor, user_id=principal.user_id)
    service.audit(db, "reject", actor=principal.actor, user_id=principal.user_id, entity_type="document", entity_id=doc.id)
    db.commit()
    doc = _load(db, doc.id) or doc
    _fire(db, "document.rejected", doc, {"comment": text})
    return _load(db, doc.id) or doc


def return_document(db: Session, doc: Document, principal: Principal, *, comment: str) -> Document:
    if doc.locked or doc.status == "approved":
        raise ValueError("Approved documents are locked")
    if doc.status != "in_review":
        raise ValueError("Only documents in review can be returned for changes")
    step = current_pending_step(doc)
    if not step:
        raise ValueError("No pending approval step")
    if not can_act_on_step(principal, step):
        raise PermissionError("You are not the assigned approver for this step")
    text = _require_comment(comment)
    now = utcnow_iso()
    step.status = "returned"
    step.actor_id = principal.user_id
    step.actor_name = principal.actor
    step.comment = text
    step.acted_at = now
    # cancel remaining waiting steps in this cycle
    for s in current_cycle_steps(doc):
        if s.status == "waiting":
            s.status = "returned"
    doc.status = "returned"
    doc.updated_at = now
    add_comment(
        db, doc,
        body=text,
        kind="comment", event="returned", actor=principal.actor, user_id=principal.user_id,
    )
    service.audit(db, "return", actor=principal.actor, user_id=principal.user_id, entity_type="document", entity_id=doc.id)
    db.commit()
    doc = _load(db, doc.id) or doc
    _fire(db, "document.returned", doc, {"comment": text})
    return _load(db, doc.id) or doc


def dashboard_summary(db: Session, principal: Principal | None = None) -> dict[str, Any]:
    from logentra_server.models import Annotation, Equipment, Reading, WorkOrder

    eq_count = db.query(Equipment).count()
    open_statuses = ("open", "in_progress", "on_hold")
    open_wo = db.query(WorkOrder).filter(WorkOrder.status.in_(open_statuses)).count()
    now = utcnow_iso()
    overdue = (
        db.query(WorkOrder)
        .filter(WorkOrder.status.in_(open_statuses), WorkOrder.due_at.isnot(None), WorkOrder.due_at < now[:10])
        .count()
    )
    # unacknowledged OOR: out_of_range=1 and no annotation
    oor_q = (
        db.query(Reading)
        .outerjoin(Annotation, Annotation.reading_id == Reading.id)
        .filter(Reading.out_of_range == 1, Annotation.id.is_(None))
    )
    oor_count = oor_q.count()
    pending_all = db.query(Document).filter(Document.status == "in_review").count()
    my_queue = list_my_queue(db, principal or Principal(mode="open"), page=1, page_size=8) if principal else {"items": [], "total": 0}

    alert_rows = (
        db.query(Reading, Equipment)
        .join(Equipment, Equipment.id == Reading.equipment_id)
        .outerjoin(Annotation, Annotation.reading_id == Reading.id)
        .filter(Reading.out_of_range == 1)
        .order_by(Reading.ts.desc())
        .limit(8)
        .all()
    )
    alerts = []
    for reading, eq in alert_rows:
        ann = reading.annotation
        alerts.append({
            "reading_id": reading.id,
            "equipment_id": eq.id,
            "tag": eq.tag,
            "parameter": reading.parameter_key,
            "value": reading.value,
            "min": reading.limit_min,
            "max": reading.limit_max,
            "ts": reading.ts,
            "acknowledged": bool(ann),
            "cause": ann.cause if ann else None,
            "action": ann.action if ann else None,
        })
    return {
        "equipment_count": eq_count,
        "open_work_orders": open_wo,
        "overdue_work_orders": overdue,
        "out_of_range_alerts": oor_count,
        "pending_approvals": pending_all,
        "my_pending_approvals": my_queue["total"],
        "my_approvals": my_queue["items"],
        "recent_alerts": alerts,
    }
