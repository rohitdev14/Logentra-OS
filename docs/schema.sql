-- Logbook v0.4 schema
-- Portable SQL for SQLite (default) and PostgreSQL.
-- Preferred apply path: `python run.py` runs Alembic (migrations/versions).
-- This file is the human-readable schema. On a new database Alembic creates
-- sites/users first; the CREATE TABLE order here is documentary.
-- Types chosen for portability: TEXT, INTEGER, REAL, timestamps as ISO-8601 TEXT (UTC).
-- JSON payloads are stored as TEXT.

-- ---------------------------------------------------------------------------
-- Equipment register (user-defined columns)
-- ---------------------------------------------------------------------------

CREATE TABLE field_definitions (
    id          TEXT PRIMARY KEY,
    key         TEXT NOT NULL,
    label       TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'text', -- text | number | date | select
    options     TEXT,                          -- JSON array of dropdown options
    locked      INTEGER NOT NULL DEFAULT 0,    -- 1 = required built-in (tag)
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_field_definitions_key ON field_definitions(key);

CREATE TABLE equipment (
    id          TEXT PRIMARY KEY,
    tag         TEXT NOT NULL,                 -- denormalized from the Tag field for joins
    site_id     TEXT,                          -- FK to sites(id); created in v0.4
    area_id     TEXT,                          -- FK to areas(id); created in v0.4
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_equipment_tag ON equipment(tag);
CREATE INDEX ix_equipment_site_id ON equipment(site_id);
CREATE INDEX ix_equipment_area_id ON equipment(area_id);

CREATE TABLE equipment_field_values (
    equipment_id TEXT NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    field_id     TEXT NOT NULL REFERENCES field_definitions(id) ON DELETE CASCADE,
    value        TEXT,
    PRIMARY KEY (equipment_id, field_id)
);

CREATE INDEX ix_efv_field ON equipment_field_values(field_id);

-- ---------------------------------------------------------------------------
-- Log parameters (per equipment) and category presets
-- ---------------------------------------------------------------------------

CREATE TABLE param_presets (
    id          TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    key         TEXT NOT NULL,
    label       TEXT NOT NULL,
    unit        TEXT,
    min_value   REAL,
    max_value   REAL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX uq_param_presets_cat_key ON param_presets(category, key);

CREATE TABLE parameters (
    id            TEXT PRIMARY KEY,
    equipment_id  TEXT NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,
    label         TEXT NOT NULL,
    unit          TEXT,
    min_value     REAL,
    max_value     REAL,
    sort_order    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX uq_parameters_eq_key ON parameters(equipment_id, key);
CREATE INDEX ix_parameters_equipment ON parameters(equipment_id);

-- ---------------------------------------------------------------------------
-- Record templates (user-defined checklists / forms)
-- ---------------------------------------------------------------------------

CREATE TABLE templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'custom', -- commissioning | inspection | maintenance | custom
    category    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE template_columns (
    id           TEXT PRIMARY KEY,
    template_id  TEXT NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
    key          TEXT NOT NULL,
    label        TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT 'text', -- text | number | date | select | passfail | unit
    options      TEXT,
    sort_order   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX ix_template_columns_tpl ON template_columns(template_id);

CREATE TABLE template_items (
    id           TEXT PRIMARY KEY,
    template_id  TEXT NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    hint         TEXT,
    sort_order   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX ix_template_items_tpl ON template_items(template_id);

-- ---------------------------------------------------------------------------
-- Records (instances of a template, snapshotted at start)
-- ---------------------------------------------------------------------------

CREATE TABLE records (
    id             TEXT PRIMARY KEY,
    template_id    TEXT REFERENCES templates(id) ON DELETE SET NULL,
    template_name  TEXT,
    kind           TEXT,
    equipment_id   TEXT REFERENCES equipment(id) ON DELETE SET NULL,
    notes          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX ix_records_equipment ON records(equipment_id);
CREATE INDEX ix_records_template ON records(template_id);

CREATE TABLE record_columns (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    label       TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'text',
    options     TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX ix_record_columns_rec ON record_columns(record_id);

CREATE TABLE record_items (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    hint        TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX ix_record_items_rec ON record_items(record_id);

CREATE TABLE record_values (
    item_id    TEXT NOT NULL REFERENCES record_items(id) ON DELETE CASCADE,
    column_id  TEXT NOT NULL REFERENCES record_columns(id) ON DELETE CASCADE,
    value      TEXT,
    PRIMARY KEY (item_id, column_id)
);

CREATE TABLE signoffs (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL UNIQUE REFERENCES records(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    signed_at   TEXT NOT NULL
);

CREATE TABLE punches (
    id           TEXT PRIMARY KEY,
    record_id    TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    description  TEXT NOT NULL,
    priority     TEXT,
    status       TEXT NOT NULL DEFAULT 'Open',
    owner        TEXT,
    raised_at    TEXT,
    closed_at    TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX ix_punches_record ON punches(record_id);
CREATE INDEX ix_punches_status ON punches(status);

-- ---------------------------------------------------------------------------
-- Time-series readings and annotations
-- ---------------------------------------------------------------------------

-- entry_id groups several parameter values taken at the same time
-- (a "log book line"). IoT ingest typically creates one reading and one entry.

CREATE TABLE readings (
    id             TEXT PRIMARY KEY,
    entry_id       TEXT NOT NULL,
    equipment_id   TEXT NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    parameter_key  TEXT NOT NULL,
    ts             TEXT NOT NULL,              -- ISO-8601 UTC, e.g. 2026-11-22T08:30:00
    value          REAL NOT NULL,
    remarks        TEXT,
    entered_by     TEXT,
    source         TEXT NOT NULL DEFAULT 'manual', -- manual | import | mqtt | webhook | api | demo
    limit_min      REAL,                       -- snapshot of limits at ingest (nullable)
    limit_max      REAL,
    out_of_range   INTEGER NOT NULL DEFAULT 0, -- denormalised flag for indexed event queries
    created_at     TEXT NOT NULL
);

CREATE INDEX ix_readings_eq_param_ts ON readings(equipment_id, parameter_key, ts);
CREATE INDEX ix_readings_ts ON readings(ts);
CREATE INDEX ix_readings_entry ON readings(entry_id);
CREATE INDEX ix_readings_source ON readings(source);
CREATE INDEX ix_readings_oor_ts ON readings(out_of_range, ts);

CREATE TABLE annotations (
    id            TEXT PRIMARY KEY,
    reading_id    TEXT NOT NULL UNIQUE REFERENCES readings(id) ON DELETE CASCADE,
    cause         TEXT,
    action        TEXT,
    annotated_by  TEXT,
    annotated_at  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Integration, audit, settings
-- ---------------------------------------------------------------------------

CREATE TABLE outbound_webhooks (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    secret      TEXT,
    events      TEXT NOT NULL DEFAULT '["out_of_range"]', -- JSON list
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE webhook_deliveries (
    id           TEXT PRIMARY KEY,
    webhook_id   TEXT REFERENCES outbound_webhooks(id) ON DELETE SET NULL,
    event        TEXT NOT NULL,
    payload      TEXT,
    status_code  INTEGER,
    ok           INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX ix_webhook_deliveries_wh ON webhook_deliveries(webhook_id, created_at);

CREATE TABLE audit_log (
    id           TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    actor        TEXT,
    user_id      TEXT,                         -- FK to users(id); created in v0.4
    action       TEXT NOT NULL,                -- create | update | delete | ingest | signoff | annotate | migrate | demo | clear | login
    entity_type  TEXT,
    entity_id    TEXT,
    detail       TEXT                          -- JSON
);

CREATE INDEX ix_audit_ts ON audit_log(ts);
CREATE INDEX ix_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX ix_audit_log_user_id ON audit_log(user_id);

CREATE TABLE settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL
);


-- ---------------------------------------------------------------------------
-- v0.4 — sites, users, roles, sessions, API keys, work orders
-- Alembic migrations/versions/0002_v04.py is the supported way to apply this
-- to an existing v0.3 database. On a fresh install, run `alembic upgrade head`
-- (python run.py does this automatically).
-- ---------------------------------------------------------------------------

CREATE TABLE sites (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    code        TEXT,
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_sites_code ON sites(code);

CREATE TABLE areas (
    id          TEXT PRIMARY KEY,
    site_id     TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    code        TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX ix_areas_site_id ON areas(site_id);

CREATE TABLE roles (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    built_in    INTEGER NOT NULL DEFAULT 0,
    permissions TEXT NOT NULL DEFAULT '[]',    -- JSON list of permission strings
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_roles_name ON roles(name);

CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    display_name  TEXT,
    email         TEXT,
    password_hash TEXT NOT NULL,               -- pbkdf2_sha256$iterations$salt$hash
    role_id       TEXT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    site_id       TEXT REFERENCES sites(id) ON DELETE SET NULL,
    area_id       TEXT REFERENCES areas(id) ON DELETE SET NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_login_at TEXT
);
CREATE UNIQUE INDEX uq_users_username ON users(username);
CREATE INDEX ix_users_role_id ON users(role_id);
CREATE INDEX ix_users_site_id ON users(site_id);

CREATE TABLE session_tokens (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL,                -- sha256 of the opaque token
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_used_at TEXT
);
CREATE UNIQUE INDEX uq_session_tokens_hash ON session_tokens(token_hash);
CREATE INDEX ix_session_tokens_user_id ON session_tokens(user_id);
CREATE INDEX ix_session_tokens_expires_at ON session_tokens(expires_at);

CREATE TABLE api_keys (
    id           TEXT PRIMARY KEY,
    user_id      TEXT REFERENCES users(id) ON DELETE CASCADE,
    role_id      TEXT REFERENCES roles(id) ON DELETE SET NULL,
    name         TEXT NOT NULL DEFAULT 'API key',
    key_hash     TEXT NOT NULL,
    prefix       TEXT,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked      INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX uq_api_keys_hash ON api_keys(key_hash);
CREATE INDEX ix_api_keys_user_id ON api_keys(user_id);

CREATE TABLE work_orders (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT,
    status                TEXT NOT NULL DEFAULT 'open',     -- open | in_progress | on_hold | done | closed
    priority              TEXT NOT NULL DEFAULT 'medium',   -- low | medium | high | urgent
    equipment_id          TEXT REFERENCES equipment(id) ON DELETE SET NULL,
    site_id               TEXT REFERENCES sites(id) ON DELETE SET NULL,
    area_id               TEXT REFERENCES areas(id) ON DELETE SET NULL,
    assignee_id           TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_by_id         TEXT REFERENCES users(id) ON DELETE SET NULL,
    due_at                TEXT,
    started_at            TEXT,
    completed_at          TEXT,
    closed_at             TEXT,
    linked_reading_id     TEXT REFERENCES readings(id) ON DELETE SET NULL,
    linked_annotation_id  TEXT REFERENCES annotations(id) ON DELETE SET NULL,
    linked_punch_id       TEXT REFERENCES punches(id) ON DELETE SET NULL,
    linked_record_id      TEXT REFERENCES records(id) ON DELETE SET NULL,
    parameter_key         TEXT,
    labour_minutes        INTEGER,
    labour_notes          TEXT,
    source                TEXT NOT NULL DEFAULT 'manual',   -- manual | api | auto_oor
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX ix_work_orders_status ON work_orders(status);
CREATE INDEX ix_work_orders_status_due ON work_orders(status, due_at);
CREATE INDEX ix_work_orders_equipment ON work_orders(equipment_id);
CREATE INDEX ix_work_orders_assignee ON work_orders(assignee_id);
CREATE INDEX ix_work_orders_created ON work_orders(created_at);

CREATE TABLE work_order_comments (
    id            TEXT PRIMARY KEY,
    work_order_id TEXT NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    user_id       TEXT REFERENCES users(id) ON DELETE SET NULL,
    author        TEXT,
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX ix_work_order_comments_wo ON work_order_comments(work_order_id);

CREATE TABLE work_order_attachments (
    id            TEXT PRIMARY KEY,
    work_order_id TEXT NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    content_type  TEXT,
    size          INTEGER,
    storage_path  TEXT,
    uploaded_by   TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX ix_work_order_attachments_wo ON work_order_attachments(work_order_id);

CREATE TABLE work_order_labour (
    id            TEXT PRIMARY KEY,
    work_order_id TEXT NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    user_id       TEXT REFERENCES users(id) ON DELETE SET NULL,
    author        TEXT,
    minutes       INTEGER NOT NULL DEFAULT 0,
    note          TEXT,
    worked_on     TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX ix_work_order_labour_wo ON work_order_labour(work_order_id);

CREATE TABLE auto_work_order_rules (
    id                    TEXT PRIMARY KEY,
    equipment_id          TEXT REFERENCES equipment(id) ON DELETE CASCADE,
    parameter_key         TEXT,
    enabled               INTEGER NOT NULL DEFAULT 0,      -- off by default
    priority              TEXT NOT NULL DEFAULT 'medium',
    title_template        TEXT,
    description_template  TEXT,
    assignee_id           TEXT REFERENCES users(id) ON DELETE SET NULL,
    cooldown_minutes      INTEGER NOT NULL DEFAULT 1440,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX ix_auto_wo_rules_eq ON auto_work_order_rules(equipment_id);


-- v0.5 document approvals
CREATE TABLE approval_routes (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    description    TEXT,
    document_type  TEXT,                          -- optional filter
    site_id        TEXT REFERENCES sites(id) ON DELETE SET NULL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX ix_approval_routes_document_type ON approval_routes(document_type);
CREATE INDEX ix_approval_routes_site_id ON approval_routes(site_id);

CREATE TABLE approval_route_steps (
    id             TEXT PRIMARY KEY,
    route_id       TEXT NOT NULL REFERENCES approval_routes(id) ON DELETE CASCADE,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    assignee_type  TEXT NOT NULL DEFAULT 'role', -- role | user
    role_name      TEXT,
    user_id        TEXT REFERENCES users(id) ON DELETE SET NULL,
    label          TEXT
);
CREATE INDEX ix_approval_route_steps_route_id ON approval_route_steps(route_id);

CREATE TABLE documents (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    document_type       TEXT,
    status              TEXT NOT NULL DEFAULT 'draft', -- draft | in_review | approved | rejected | returned
    equipment_id        TEXT REFERENCES equipment(id) ON DELETE SET NULL,
    record_id           TEXT REFERENCES records(id) ON DELETE SET NULL,
    work_order_id       TEXT REFERENCES work_orders(id) ON DELETE SET NULL,
    site_id             TEXT REFERENCES sites(id) ON DELETE SET NULL,
    route_id            TEXT REFERENCES approval_routes(id) ON DELETE SET NULL,
    current_version_id  TEXT,
    current_step_index  INTEGER NOT NULL DEFAULT 0,
    locked              INTEGER NOT NULL DEFAULT 0,    -- 1 once approved
    submitted_by        TEXT,
    submitted_by_id     TEXT REFERENCES users(id) ON DELETE SET NULL,
    submitted_at        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX ix_documents_status ON documents(status);
CREATE INDEX ix_documents_equipment ON documents(equipment_id);
CREATE INDEX ix_documents_updated ON documents(updated_at);
CREATE INDEX ix_documents_document_type ON documents(document_type);

CREATE TABLE document_versions (
    id              TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_no      INTEGER NOT NULL DEFAULT 1,
    filename        TEXT NOT NULL,
    mime            TEXT,
    size            INTEGER NOT NULL DEFAULT 0,
    sha256          TEXT NOT NULL,
    storage_path    TEXT NOT NULL,                  -- relative to the package root, under data/attachments/documents
    uploaded_by     TEXT,
    uploaded_by_id  TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX ix_document_versions_document_id ON document_versions(document_id);

CREATE TABLE document_approval_steps (
    id             TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id     TEXT REFERENCES document_versions(id) ON DELETE SET NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    assignee_type  TEXT NOT NULL DEFAULT 'role',
    role_name      TEXT,
    user_id        TEXT REFERENCES users(id) ON DELETE SET NULL,
    label          TEXT,
    status         TEXT NOT NULL DEFAULT 'waiting', -- waiting | pending | approved | rejected | returned
    actor_id       TEXT REFERENCES users(id) ON DELETE SET NULL,
    actor_name     TEXT,
    comment        TEXT,
    acted_at       TEXT
);
CREATE INDEX ix_document_approval_steps_document_id ON document_approval_steps(document_id);

CREATE TABLE document_comments (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id   TEXT REFERENCES document_versions(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL DEFAULT 'comment', -- comment | system
    event        TEXT,                            -- submitted | step_approved | approved | rejected | returned | uploaded
    author_id    TEXT REFERENCES users(id) ON DELETE SET NULL,
    author       TEXT,
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX ix_document_comments_document_id ON document_comments(document_id);
