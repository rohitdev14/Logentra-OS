CREATE TABLE shift_patterns (
    id TEXT NOT NULL,
    site_id TEXT REFERENCES sites(id) ON DELETE RESTRICT NOT NULL,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE shift_definitions (
    id TEXT NOT NULL,
    pattern_id TEXT REFERENCES shift_patterns(id) ON DELETE CASCADE NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    start_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT uq_shift_definitions_pattern_id_code UNIQUE (pattern_id, code)
);

CREATE TABLE crews (
    id TEXT NOT NULL,
    site_id TEXT REFERENCES sites(id) ON DELETE RESTRICT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE crew_members (
    crew_id TEXT REFERENCES crews(id) ON DELETE CASCADE NOT NULL,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    is_lead BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (crew_id, user_id)
);

CREATE TABLE roster_entries (
    id TEXT NOT NULL,
    shift_definition_id TEXT REFERENCES shift_definitions(id) ON DELETE RESTRICT NOT NULL,
    date TEXT NOT NULL,
    area_id TEXT REFERENCES areas(id) ON DELETE SET NULL,
    crew_id TEXT REFERENCES crews(id) ON DELETE RESTRICT NOT NULL,
    lead_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_roster_entries_slot UNIQUE (shift_definition_id, date, area_id)
);

CREATE UNIQUE INDEX uq_roster_entries_slot_all_areas ON roster_entries(shift_definition_id, date, COALESCE(area_id, ''));

CREATE TABLE shifts (
    id TEXT NOT NULL,
    site_id TEXT REFERENCES sites(id) ON DELETE RESTRICT NOT NULL,
    area_id TEXT REFERENCES areas(id) ON DELETE SET NULL,
    shift_definition_id TEXT REFERENCES shift_definitions(id) ON DELETE RESTRICT NOT NULL,
    date TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    crew_id TEXT REFERENCES crews(id) ON DELETE RESTRICT NOT NULL,
    lead_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    opened_at TEXT,
    closed_at TEXT,
    PRIMARY KEY (id),
    CONSTRAINT uq_shifts_slot UNIQUE (shift_definition_id, date, area_id)
);

CREATE UNIQUE INDEX uq_shifts_slot_all_areas ON shifts(shift_definition_id, date, COALESCE(area_id, ''));

CREATE INDEX ix_shifts_area_id_starts_at ON shifts(area_id, starts_at);

CREATE TABLE shift_log_categories (
    id TEXT NOT NULL,
    site_id TEXT REFERENCES sites(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (id)
);

CREATE TABLE shift_log_entries (
    id TEXT NOT NULL,
    shift_id TEXT REFERENCES shifts(id) ON DELETE RESTRICT NOT NULL,
    event_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    category_id TEXT REFERENCES shift_log_categories(id) ON DELETE RESTRICT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    equipment_id TEXT REFERENCES equipment(id) ON DELETE SET NULL,
    work_order_id TEXT REFERENCES work_orders(id) ON DELETE SET NULL,
    reading_id TEXT REFERENCES readings(id) ON DELETE SET NULL,
    punch_id TEXT REFERENCES punches(id) ON DELETE SET NULL,
    in_handover BOOLEAN NOT NULL DEFAULT false,
    author_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_id TEXT REFERENCES shift_log_entries(id) ON DELETE SET NULL,
    voided_at TEXT,
    void_reason TEXT,
    after_handover BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (id),
    CONSTRAINT uq_shift_log_entries_supersedes_id UNIQUE (supersedes_id)
);

CREATE INDEX ix_shift_log_entries_shift_id_event_at ON shift_log_entries(shift_id, event_at);

CREATE TABLE shift_log_attachments (
    id TEXT NOT NULL,
    entry_id TEXT REFERENCES shift_log_entries(id) ON DELETE RESTRICT NOT NULL,
    equipment_id TEXT REFERENCES equipment(id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    width INTEGER,
    height INTEGER,
    sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    uploaded_by TEXT NOT NULL,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_shift_log_attachments_entry_id ON shift_log_attachments(entry_id);

CREATE TABLE handovers (
    id TEXT NOT NULL,
    outgoing_shift_id TEXT REFERENCES shifts(id) ON DELETE RESTRICT NOT NULL,
    incoming_shift_id TEXT REFERENCES shifts(id) ON DELETE SET NULL,
    site_id TEXT REFERENCES sites(id) ON DELETE RESTRICT NOT NULL,
    area_id TEXT REFERENCES areas(id) ON DELETE SET NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    snapshot_json TEXT,
    snapshot_sha256 TEXT,
    signed_off_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    signed_off_at TEXT,
    acknowledged_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    acknowledged_at TEXT,
    ack_comment TEXT,
    unseen_count INTEGER NOT NULL DEFAULT 0,
    returned_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    returned_at TEXT,
    return_reason TEXT,
    ack_due_at TEXT,
    escalated_at TEXT,
    escalated_to TEXT,
    reminder_sent_at TEXT,
    signed_off_name TEXT,
    acknowledged_name TEXT,
    returned_name TEXT,
    PRIMARY KEY (id),
    CONSTRAINT uq_handovers_outgoing_shift_id_version UNIQUE (outgoing_shift_id, version)
);

CREATE INDEX ix_handovers_area_id_status ON handovers(area_id, status);

CREATE TABLE handover_items (
    id TEXT NOT NULL,
    handover_id TEXT REFERENCES handovers(id) ON DELETE CASCADE NOT NULL,
    kind TEXT NOT NULL,
    ref_id TEXT,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    severity TEXT NOT NULL,
    auto BOOLEAN NOT NULL DEFAULT false,
    pinned BOOLEAN NOT NULL DEFAULT false,
    removed_manually BOOLEAN NOT NULL DEFAULT false,
    removed_at TEXT,
    removed_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    seen_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    seen_at TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id)
);

CREATE INDEX ix_handover_items_handover_id_kind_ref_id ON handover_items(handover_id, kind, ref_id);

CREATE TABLE handover_comments (
    id TEXT NOT NULL,
    handover_id TEXT REFERENCES handovers(id) ON DELETE CASCADE NOT NULL,
    item_id TEXT REFERENCES handover_items(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    author_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reply_to_id TEXT REFERENCES handover_comments(id) ON DELETE SET NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_handover_comments_handover_id_created_at ON handover_comments(handover_id, created_at);

CREATE TABLE shift_log_comments (
    id TEXT NOT NULL,
    entry_id TEXT REFERENCES shift_log_entries(id) ON DELETE RESTRICT NOT NULL,
    author_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_shift_log_comments_entry_id ON shift_log_comments(entry_id);

CREATE TABLE site_handover_settings (
    site_id TEXT REFERENCES sites(id) ON DELETE CASCADE NOT NULL,
    ack_within_minutes INTEGER NOT NULL DEFAULT 30,
    escalate_to_role TEXT NOT NULL DEFAULT 'supervisor',
    signoff_reminder_minutes INTEGER NOT NULL DEFAULT 15,
    PRIMARY KEY (site_id)
);
