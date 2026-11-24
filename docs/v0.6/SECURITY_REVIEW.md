# Security review

| Finding | Resolution and residual exposure |
| --- | --- |
| Unauthenticated network bind | Open mode checks configured and CLI bind addresses, peer, Host and Origin. Production rejects open mode and the legacy global key. |
| Unrestricted cross-origin requests | Same-origin default, with an explicit CORS allowlist. |
| Credential guessing and password storage | Per-peer and per-worker rate limiting. PBKDF2-SHA256 uses 600,000 iterations with a per-password salt. Old hashes upgrade after a successful login. Password changes revoke sessions. |
| Unbounded API keys | New keys expire after 90 days by default. Production requires replacement of legacy keys without an expiry. Revocation takes effect on subsequent requests. |
| Mixed endpoint privileges and object access | Catalog sync checks permissions by section. Bootstrap filters by permission, and scope-aware queries apply site and area. Unsafe legacy paths require unscoped access. |
| Payload and upload abuse | Declared and streamed request-size checks, bounded upload reads, CSV/XLSX extension checks, XLSX unpacked-size preflight, path containment and checksum verification. General attachments are downloads. |
| Webhook SSRF | HTTP(S) only, no userinfo, redirects or environment proxies. Resolve and check addresses, then pin the connection while preserving Host and TLS identity. Private destinations need explicit non-production configuration. |
| Sensitive application logging | Route templates and correlation IDs, SQLAlchemy parameter hiding, redacted validation responses and bounded webhook error categories. |
| Engineering evidence | Signed records reject changes and deletion. Approved documents lock and verify checksums. Checked paths protect linked objects from deletion. Audit is append-only through the application API. |

Negative tests cover network access, invalid configuration, request size, throttle behavior, expired keys, scope enforcement, stale changes, checksum tampering and restricted webhook destinations. Residual risks include fail-closed scope gaps, per-process login throttling behind proxies, filesystem and database coordination after a crash and legacy mutation paths with limited concurrency coverage.

## Qualification changes

- Verified allowed same-area and denied cross-area or cross-site operational access. JSON backup exports and global import and sync remain unscoped. The [endpoint matrix](AUTHORIZATION_MATRIX.md) describes each control.
- SQLite and PostgreSQL block audit rewrites and deletion. PostgreSQL also blocks truncation with a trigger. Authorized user deletion can detach an audit account reference while retaining actor and evidence fields.
- Audit snapshots can be fingerprinted for offline comparison. Store the trusted fingerprint separately. Database owners and filesystem administrators remain outside this guarantee.
- Ran nginx with a test certificate and verified HTTPS readiness, HSTS, production anonymous denial and edge login throttling. Docker and target ingress remain unqualified.
