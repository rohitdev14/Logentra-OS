-- Logbook v0.3 schema
-- Portable SQL for SQLite (default) and PostgreSQL.
-- Application code also creates these tables via SQLAlchemy metadata.
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
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_equipment_tag ON equipment(tag);

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
    created_at     TEXT NOT NULL
);

CREATE INDEX ix_readings_eq_param_ts ON readings(equipment_id, parameter_key, ts);
CREATE INDEX ix_readings_ts ON readings(ts);
CREATE INDEX ix_readings_entry ON readings(entry_id);
CREATE INDEX ix_readings_source ON readings(source);

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
    action       TEXT NOT NULL,                -- create | update | delete | ingest | signoff | annotate | migrate | demo | clear
    entity_type  TEXT,
    entity_id    TEXT,
    detail       TEXT                          -- JSON
);

CREATE INDEX ix_audit_ts ON audit_log(ts);
CREATE INDEX ix_audit_entity ON audit_log(entity_type, entity_id);

CREATE TABLE settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL
);
