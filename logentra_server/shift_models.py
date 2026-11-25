"""Shift scheduling, append-only log and versioned handover models."""
from sqlalchemy import Boolean, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from logentra_server.database import Base


class ShiftPattern(Base):
    __tablename__ = "shift_patterns"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    site_id: Mapped[str] = mapped_column(Text, ForeignKey("sites.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class ShiftDefinition(Base):
    __tablename__ = "shift_definitions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    pattern_id: Mapped[str] = mapped_column(Text, ForeignKey("shift_patterns.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    start_time: Mapped[str] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, server_default='0')
    __table_args__ = (
        UniqueConstraint("pattern_id", "code", name="uq_shift_definitions_pattern_id_code"),
    )


class Crew(Base):
    __tablename__ = "crews"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    site_id: Mapped[str] = mapped_column(Text, ForeignKey("sites.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)


class CrewMember(Base):
    __tablename__ = "crew_members"
    crew_id: Mapped[str] = mapped_column(Text, ForeignKey("crews.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    is_lead: Mapped[bool] = mapped_column(Boolean, server_default='false')


class RosterEntry(Base):
    __tablename__ = "roster_entries"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    shift_definition_id: Mapped[str] = mapped_column(Text, ForeignKey("shift_definitions.id", ondelete="RESTRICT"))
    date: Mapped[str] = mapped_column(Text)
    area_id: Mapped[str | None] = mapped_column(Text, ForeignKey("areas.id", ondelete="SET NULL"))
    crew_id: Mapped[str] = mapped_column(Text, ForeignKey("crews.id", ondelete="RESTRICT"))
    lead_user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    __table_args__ = (
        UniqueConstraint("shift_definition_id", "date", "area_id", name="uq_roster_entries_slot"),
        Index("uq_roster_entries_slot_all_areas", "shift_definition_id", "date", func.coalesce(area_id, ""), unique=True),
    )


class Shift(Base):
    __tablename__ = "shifts"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    site_id: Mapped[str] = mapped_column(Text, ForeignKey("sites.id", ondelete="RESTRICT"))
    area_id: Mapped[str | None] = mapped_column(Text, ForeignKey("areas.id", ondelete="SET NULL"))
    shift_definition_id: Mapped[str] = mapped_column(Text, ForeignKey("shift_definitions.id", ondelete="RESTRICT"))
    date: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[str] = mapped_column(Text)
    ends_at: Mapped[str] = mapped_column(Text)
    crew_id: Mapped[str] = mapped_column(Text, ForeignKey("crews.id", ondelete="RESTRICT"))
    lead_user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("shift_definition_id", "date", "area_id", name="uq_shifts_slot"),
        Index("uq_shifts_slot_all_areas", "shift_definition_id", "date", func.coalesce(area_id, ""), unique=True),
        Index("ix_shifts_area_id_starts_at", 'area_id', 'starts_at'),
    )


class ShiftLogCategory(Base):
    __tablename__ = "shift_log_categories"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    site_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sites.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, server_default='0')
    active: Mapped[bool] = mapped_column(Boolean, server_default='true')


class ShiftLogEntry(Base):
    __tablename__ = "shift_log_entries"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    shift_id: Mapped[str] = mapped_column(Text, ForeignKey("shifts.id", ondelete="RESTRICT"))
    event_at: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[str] = mapped_column(Text)
    category_id: Mapped[str] = mapped_column(Text, ForeignKey("shift_log_categories.id", ondelete="RESTRICT"))
    severity: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    equipment_id: Mapped[str | None] = mapped_column(Text, ForeignKey("equipment.id", ondelete="SET NULL"))
    work_order_id: Mapped[str | None] = mapped_column(Text, ForeignKey("work_orders.id", ondelete="SET NULL"))
    reading_id: Mapped[str | None] = mapped_column(Text, ForeignKey("readings.id", ondelete="SET NULL"))
    punch_id: Mapped[str | None] = mapped_column(Text, ForeignKey("punches.id", ondelete="SET NULL"))
    in_handover: Mapped[bool] = mapped_column(Boolean, server_default='false')
    author_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    author_name: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    supersedes_id: Mapped[str | None] = mapped_column(Text, ForeignKey("shift_log_entries.id", ondelete="SET NULL"))
    voided_at: Mapped[str | None] = mapped_column(Text)
    void_reason: Mapped[str | None] = mapped_column(Text)
    after_handover: Mapped[bool] = mapped_column(Boolean, server_default='false')
    __table_args__ = (
        UniqueConstraint("supersedes_id", name="uq_shift_log_entries_supersedes_id"),
        Index("ix_shift_log_entries_shift_id_event_at", 'shift_id', 'event_at'),
    )


class ShiftLogAttachment(Base):
    __tablename__ = "shift_log_attachments"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    entry_id: Mapped[str] = mapped_column(Text, ForeignKey("shift_log_entries.id", ondelete="RESTRICT"))
    equipment_id: Mapped[str | None] = mapped_column(Text, ForeignKey("equipment.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        Index("ix_shift_log_attachments_entry_id", 'entry_id'),
    )


class Handover(Base):
    __tablename__ = "handovers"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    outgoing_shift_id: Mapped[str] = mapped_column(Text, ForeignKey("shifts.id", ondelete="RESTRICT"))
    incoming_shift_id: Mapped[str | None] = mapped_column(Text, ForeignKey("shifts.id", ondelete="SET NULL"))
    site_id: Mapped[str] = mapped_column(Text, ForeignKey("sites.id", ondelete="RESTRICT"))
    area_id: Mapped[str | None] = mapped_column(Text, ForeignKey("areas.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    snapshot_json: Mapped[str | None] = mapped_column(Text)
    snapshot_sha256: Mapped[str | None] = mapped_column(Text)
    signed_off_by: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    signed_off_at: Mapped[str | None] = mapped_column(Text)
    acknowledged_by: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[str | None] = mapped_column(Text)
    ack_comment: Mapped[str | None] = mapped_column(Text)
    unseen_count: Mapped[int] = mapped_column(Integer, server_default='0')
    returned_by: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    returned_at: Mapped[str | None] = mapped_column(Text)
    return_reason: Mapped[str | None] = mapped_column(Text)
    ack_due_at: Mapped[str | None] = mapped_column(Text)
    escalated_at: Mapped[str | None] = mapped_column(Text)
    escalated_to: Mapped[str | None] = mapped_column(Text)
    reminder_sent_at: Mapped[str | None] = mapped_column(Text)
    signed_off_name: Mapped[str | None] = mapped_column(Text)
    acknowledged_name: Mapped[str | None] = mapped_column(Text)
    returned_name: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("outgoing_shift_id", "version", name="uq_handovers_outgoing_shift_id_version"),
        Index("ix_handovers_area_id_status", 'area_id', 'status'),
    )


class HandoverItem(Base):
    __tablename__ = "handover_items"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    handover_id: Mapped[str] = mapped_column(Text, ForeignKey("handovers.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    ref_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    auto: Mapped[bool] = mapped_column(Boolean, server_default='false')
    pinned: Mapped[bool] = mapped_column(Boolean, server_default='false')
    removed_manually: Mapped[bool] = mapped_column(Boolean, server_default='false')
    removed_at: Mapped[str | None] = mapped_column(Text)
    removed_by: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    seen_by: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    seen_at: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, server_default='0')
    __table_args__ = (
        Index("ix_handover_items_handover_id_kind_ref_id", 'handover_id', 'kind', 'ref_id'),
    )


class HandoverComment(Base):
    __tablename__ = "handover_comments"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    handover_id: Mapped[str] = mapped_column(Text, ForeignKey("handovers.id", ondelete="CASCADE"))
    item_id: Mapped[str | None] = mapped_column(Text, ForeignKey("handover_items.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    author_name: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    reply_to_id: Mapped[str | None] = mapped_column(Text, ForeignKey("handover_comments.id", ondelete="SET NULL"))
    __table_args__ = (
        Index("ix_handover_comments_handover_id_created_at", 'handover_id', 'created_at'),
    )


class ShiftLogComment(Base):
    __tablename__ = "shift_log_comments"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    entry_id: Mapped[str] = mapped_column(Text, ForeignKey("shift_log_entries.id", ondelete="RESTRICT"))
    author_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    author_name: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        Index("ix_shift_log_comments_entry_id", 'entry_id'),
    )


class SiteHandoverSettings(Base):
    __tablename__ = "site_handover_settings"
    site_id: Mapped[str] = mapped_column(Text, ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True)
    ack_within_minutes: Mapped[int] = mapped_column(Integer, server_default='30')
    escalate_to_role: Mapped[str] = mapped_column(Text, server_default='supervisor')
    signoff_reminder_minutes: Mapped[int] = mapped_column(Integer, server_default='15')
