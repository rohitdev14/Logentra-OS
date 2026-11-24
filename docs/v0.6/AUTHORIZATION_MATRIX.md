# Endpoint permission matrix

All paths below are relative to /api unless shown otherwise. An unscoped principal still needs the listed permission. Scoped principals must meet both permission and location checks. Site or area mismatch on an object lookup returns 404. Invalid location assignment is rejected. Open standalone sessions retain their existing local permissions.

| Method and path | Required permission and scope |
| --- | --- |
| GET /, /favicon.ico, /static/*, /docs, /redoc, /openapi.json, /api/health, /api/ready | Public interface and liveness metadata. Loopback restrictions apply to open standalone. |
| POST /auth/login | Public, throttled. |
| POST /auth/logout, GET /auth/me, GET /auth/permissions | Authenticated principal. |
| POST /auth/change-password | User session and current password. |
| GET/POST /roles, PATCH/DELETE /roles/{rid} | Unscoped users.manage. |
| GET/POST /users, GET/PATCH/DELETE /users/{uid} | Unscoped users.manage. |
| GET/POST /users/{uid}/api-keys, DELETE /users/{uid}/api-keys/{kid} | Unscoped users.manage. |
| GET /sites | users.manage or equipment.read. Site and area filter applies. |
| POST /sites, POST /sites/{sid}/areas, DELETE /sites/{sid} | Unscoped users.manage. |
| GET /dashboard | equipment.read, readings.read, workorders.read and documents.read. Scope filters each domain. |
| GET /bootstrap | equipment.read. Records, logs and templates require their respective read permission. |
| PUT /sync | Unscoped. Permissions checked per section. settings.manage for fields/presets, equipment.write for equipment, templates.write for templates, records.write plus signoff when applicable for records. Production requires If-Match. |
| POST /demo, DELETE /data, POST /migrate/v2, POST /migrate/v2/file | Unscoped settings.manage. |
| GET /fields, /equipment, /equipment/{eid} | equipment.read. Equipment lookups scoped. Fields are shared. |
| POST /equipment, POST /equipment/bulk, PATCH/DELETE /equipment/{eid} | equipment.write. Existing asset and destination scope checked. Scoped users cannot move assets. |
| GET /templates | templates.read. Templates are shared. |
| POST /templates, DELETE /templates/{tid} | Unscoped templates.write. |
| GET /records | records.read through equipment scope. |
| POST /records, DELETE /records/{rid} | records.write through equipment scope. Signed evidence is immutable. Signing requires signoff. |
| GET /readings, /logs, /events/out-of-range | readings.read through equipment scope. |
| POST /readings, POST /readings/bulk, POST /ingest/readings | readings.write. Scoped callers need existing authorized equipment and equipment.write for new parameters. Automatic equipment creation remains unscoped. |
| DELETE /readings/{rid} | readings.write. Scoped callers use explicit reading IDs. Entry-wide delete remains unscoped. |
| PUT /readings/{rid}/annotation, PUT/DELETE /logs/{entry_id}/annotations/{parameter}, DELETE /annotations/{aid} | annotations.write through reading equipment scope. |
| POST /ingest/file | readings.write. Scope checks apply to each reading. Equipment file ingestion additionally requires unscoped equipment.write. |
| GET /export/json | Unscoped readings.read, equipment.read, records.read and templates.read. |
| GET /export/equipment.csv, /export/readings.csv | equipment.read or readings.read respectively, with scope-aware row selection. |
| GET /work-orders, /work-orders/{wid} | workorders.read through work-order location. |
| POST /work-orders | workorders.create, plus workorders.assign when assigning another user. Scoped work-order creation checks location and equipment. |
| PATCH /work-orders/{wid}, POST /work-orders/{wid}/transition | Scoped target. workorders.close to close, workorders.create or workorders.assign for ordinary edits. Status-only transitions may also use workorders.close. |
| POST /work-orders/{wid}/assign | workorders.assign. Scoped callers must assign within their scope. |
| POST /work-orders/{wid}/comments | workorders.create, workorders.assign or workorders.close. Target scope required. |
| POST /work-orders/{wid}/labour, /work-orders/{wid}/attachments | workorders.create or workorders.assign. Target scope required. |
| DELETE /work-orders/{wid} | Unscoped users.manage or workorders.create for a caller-owned open order in scope. |
| GET /work-order-rules | Unscoped workorders.read. |
| POST /work-order-rules, PATCH/DELETE /work-order-rules/{rid} | Unscoped workorders.assign. |
| GET /documents, /documents/queue, /documents/{did}, /documents/{did}/file | documents.read and document location. Queue also checks step eligibility. |
| POST /documents, PATCH /documents/{did}, POST /documents/{did}/versions, /documents/{did}/submit | documents.upload and location. Linked object permissions checked on scoped creation. Moving or relinking an existing document is unscoped. |
| POST /documents/{did}/approve, /reject, /return | documents.approve, location and step eligibility. |
| POST /documents/{did}/comments | documents.read and document location. |
| GET /approval-routes | documents.read. Scoped callers get same-site and shared route metadata with steps omitted. |
| GET /approval-routes/{rid} | Unscoped documents.read. |
| POST /approval-routes, PATCH/DELETE /approval-routes/{rid} | Unscoped routes.manage. |
| GET/POST /webhooks, DELETE /webhooks/{wid}, GET /webhooks/deliveries, POST /webhooks/deliveries/{id}/replay, GET /integrations/health | Unscoped webhooks.manage. |
| GET /audit | Unscoped audit.read. |
| MQTT subscriber | Operator-configured broker connection. Optional site and area bind readings to existing assets. Broker ACL controls which publishers can reach ingress. |

Remaining scope verification includes legacy cross-linked rows, mixed-scope browser catalog writes, and a negative test for each method and endpoint combination. Existing area-less documents keep their previous classification. Reclassification requires explicit administrative review.
