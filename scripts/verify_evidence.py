"""Read-only audit fingerprint and document-file reconciliation."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json

from logentra_server.config import load_settings
from logentra_server.database import make_engine, make_session_factory
from logentra_server.documents import ATTACH_DIR, verify_version
from logentra_server.models import AuditLog, DocumentVersion


def inspect_evidence(db):
    digest = hashlib.sha256()
    entries = 0
    for entry in db.query(AuditLog).order_by(AuditLog.id).yield_per(500):
        data = [getattr(entry, column.name) for column in AuditLog.__table__.columns]
        digest.update(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        entries += 1

    referenced = set()
    issues = []
    for version in db.query(DocumentVersion).yield_per(100):
        try:
            path = verify_version(version)
        except ValueError as exc:
            issues.append({"version": version.id, "error": str(exc)})
        else:
            referenced.add(path)
    # Corrupt but referenced files are evidence. Report them above and exclude them from orphan results.
    from logentra_server.documents import absolute_path

    for (storage_path,) in db.query(DocumentVersion.storage_path).yield_per(500):
        try:
            referenced.add(absolute_path(storage_path))
        except ValueError:
            continue
    root = ATTACH_DIR.resolve()
    orphans = sorted(
        str(path.relative_to(root)) for path in root.rglob("*")
        if path.is_file() and path.resolve() not in referenced
    )
    return {"audit_rows": entries, "audit_sha256": digest.hexdigest(), "document_issues": issues,
            "unreferenced_files": orphans}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-audit-sha256", help="Compare with a trusted fingerprint of the same snapshot")
    args = parser.parse_args()
    engine = make_engine(load_settings().sqlalchemy_url)
    try:
        with make_session_factory(engine)() as db:
            result = inspect_evidence(db)
    finally:
        engine.dispose()
    matches = not args.expected_audit_sha256 or hmac.compare_digest(args.expected_audit_sha256, result["audit_sha256"])
    print(json.dumps({**result, "audit_matches": bool(matches)}, indent=2))
    return int(not matches or bool(result["document_issues"]))


if __name__ == "__main__":
    raise SystemExit(main())
