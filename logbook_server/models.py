from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from logbook_server.database import Base


class FieldDefinition(Base):
    __tablename__ = "field_definitions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False, default="text")
    options: Mapped[str | None] = mapped_column(Text)
    locked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    values: Mapped[list[EquipmentFieldValue]] = relationship(back_populates="field")


class Equipment(Base):
    __tablename__ = "equipment"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tag: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    site_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sites.id", ondelete="SET NULL"), index=True)
    area_id: Mapped[str | None] = mapped_column(Text, ForeignKey("areas.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    values: Mapped[list[EquipmentFieldValue]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan"
    )
    parameters: Mapped[list[Parameter]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan"
    )
    readings: Mapped[list[Reading]] = relationship(back_populates="equipment")
    records: Mapped[list[Record]] = relationship(back_populates="equipment")


class EquipmentFieldValue(Base):
    __tablename__ = "equipment_field_values"
    equipment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("equipment.id", ondelete="CASCADE"), primary_key=True
    )
    field_id: Mapped[str] = mapped_column(
        Text, ForeignKey("field_definitions.id", ondelete="CASCADE"), primary_key=True
    )
    value: Mapped[str | None] = mapped_column(Text)
    equipment: Mapped[Equipment] = relationship(back_populates="values")
    field: Mapped[FieldDefinition] = relationship(back_populates="values")


class ParamPreset(Base):
    __tablename__ = "param_presets"
    __table_args__ = (UniqueConstraint("category", "key", name="uq_param_presets_cat_key"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text)
    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Parameter(Base):
    __tablename__ = "parameters"
    __table_args__ = (UniqueConstraint("equipment_id", "key", name="uq_parameters_eq_key"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    equipment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text)
    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    equipment: Mapped[Equipment] = relationship(back_populates="parameters")


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="custom")
    category: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    columns: Mapped[list[TemplateColumn]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="TemplateColumn.sort_order"
    )
    items: Mapped[list[TemplateItem]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="TemplateItem.sort_order"
    )


class TemplateColumn(Base):
    __tablename__ = "template_columns"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    template_id: Mapped[str] = mapped_column(
        Text, ForeignKey("templates.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False, default="text")
    options: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    template: Mapped[Template] = relationship(back_populates="columns")


class TemplateItem(Base):
    __tablename__ = "template_items"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    template_id: Mapped[str] = mapped_column(
        Text, ForeignKey("templates.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    hint: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    template: Mapped[Template] = relationship(back_populates="items")


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    template_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("templates.id", ondelete="SET NULL")
    )
    template_name: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(Text)
    equipment_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("equipment.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    equipment: Mapped[Equipment | None] = relationship(back_populates="records")
    columns: Mapped[list[RecordColumn]] = relationship(
        back_populates="record", cascade="all, delete-orphan", order_by="RecordColumn.sort_order"
    )
    items: Mapped[list[RecordItem]] = relationship(
        back_populates="record", cascade="all, delete-orphan", order_by="RecordItem.sort_order"
    )
    signoff: Mapped[Signoff | None] = relationship(
        back_populates="record", cascade="all, delete-orphan", uselist=False
    )
    punches: Mapped[list[Punch]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )


class RecordColumn(Base):
    __tablename__ = "record_columns"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    record_id: Mapped[str] = mapped_column(
        Text, ForeignKey("records.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False, default="text")
    options: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    record: Mapped[Record] = relationship(back_populates="columns")


class RecordItem(Base):
    __tablename__ = "record_items"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    record_id: Mapped[str] = mapped_column(
        Text, ForeignKey("records.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    hint: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    record: Mapped[Record] = relationship(back_populates="items")
    values: Mapped[list[RecordValue]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class RecordValue(Base):
    __tablename__ = "record_values"
    item_id: Mapped[str] = mapped_column(
        Text, ForeignKey("record_items.id", ondelete="CASCADE"), primary_key=True
    )
    column_id: Mapped[str] = mapped_column(
        Text, ForeignKey("record_columns.id", ondelete="CASCADE"), primary_key=True
    )
    value: Mapped[str | None] = mapped_column(Text)
    item: Mapped[RecordItem] = relationship(back_populates="values")


class Signoff(Base):
    __tablename__ = "signoffs"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    record_id: Mapped[str] = mapped_column(
        Text, ForeignKey("records.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    signed_at: Mapped[str] = mapped_column(Text, nullable=False)
    record: Mapped[Record] = relationship(back_populates="signoff")


class Punch(Base):
    __tablename__ = "punches"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    record_id: Mapped[str] = mapped_column(
        Text, ForeignKey("records.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="Open")
    owner: Mapped[str | None] = mapped_column(Text)
    raised_at: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    record: Mapped[Record] = relationship(back_populates="punches")


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        Index("ix_readings_eq_param_ts", "equipment_id", "parameter_key", "ts"),
        Index("ix_readings_source", "source"),
        Index("ix_readings_oor_ts", "out_of_range", "ts"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    entry_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    equipment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    parameter_key: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)
    entered_by: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    limit_min: Mapped[float | None] = mapped_column(Float)
    limit_max: Mapped[float | None] = mapped_column(Float)
    out_of_range: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    equipment: Mapped[Equipment] = relationship(back_populates="readings")
    annotation: Mapped[Annotation | None] = relationship(
        back_populates="reading", cascade="all, delete-orphan", uselist=False
    )


class Annotation(Base):
    __tablename__ = "annotations"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    reading_id: Mapped[str] = mapped_column(
        Text, ForeignKey("readings.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    cause: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(Text)
    annotated_by: Mapped[str | None] = mapped_column(Text)
    annotated_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    reading: Mapped[Reading] = relationship(back_populates="annotation")


class OutboundWebhook(Base):
    __tablename__ = "outbound_webhooks"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    secret: Mapped[str | None] = mapped_column(Text)
    events: Mapped[str] = mapped_column(Text, nullable=False, default='["out_of_range"]')
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    deliveries: Mapped[list[WebhookDelivery]] = relationship(back_populates="webhook")


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    webhook_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("outbound_webhooks.id", ondelete="SET NULL")
    )
    event: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    webhook: Mapped[OutboundWebhook | None] = relationship(back_populates="deliveries")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    actor: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# v0.4 — sites, users, roles, work orders
# ---------------------------------------------------------------------------


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    areas: Mapped[list[Area]] = relationship(back_populates="site", cascade="all, delete-orphan")


class Area(Base):
    __tablename__ = "areas"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    site_id: Mapped[str] = mapped_column(Text, ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    site: Mapped[Site] = relationship(back_populates="areas")


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    built_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    permissions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    users: Mapped[list[User]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role_id: Mapped[str] = mapped_column(Text, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True)
    site_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sites.id", ondelete="SET NULL"), index=True)
    area_id: Mapped[str | None] = mapped_column(Text, ForeignKey("areas.id", ondelete="SET NULL"), index=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_login_at: Mapped[str | None] = mapped_column(Text)
    role: Mapped[Role] = relationship(back_populates="users")
    site: Mapped[Site | None] = relationship()
    area: Mapped[Area | None] = relationship()


class SessionToken(Base):
    __tablename__ = "session_tokens"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    last_used_at: Mapped[str | None] = mapped_column(Text)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[str | None] = mapped_column(Text, ForeignKey("roles.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="API key")
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    prefix: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_used_at: Mapped[str | None] = mapped_column(Text)
    revoked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WorkOrder(Base):
    __tablename__ = "work_orders"
    __table_args__ = (
        Index("ix_work_orders_status_due", "status", "due_at"),
        Index("ix_work_orders_equipment", "equipment_id"),
        Index("ix_work_orders_assignee", "assignee_id"),
        Index("ix_work_orders_created", "created_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open", index=True)
    priority: Mapped[str] = mapped_column(Text, nullable=False, default="medium")
    equipment_id: Mapped[str | None] = mapped_column(Text, ForeignKey("equipment.id", ondelete="SET NULL"))
    site_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sites.id", ondelete="SET NULL"))
    area_id: Mapped[str | None] = mapped_column(Text, ForeignKey("areas.id", ondelete="SET NULL"))
    assignee_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    created_by_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    due_at: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[str | None] = mapped_column(Text)
    linked_reading_id: Mapped[str | None] = mapped_column(Text, ForeignKey("readings.id", ondelete="SET NULL"))
    linked_annotation_id: Mapped[str | None] = mapped_column(Text, ForeignKey("annotations.id", ondelete="SET NULL"))
    linked_punch_id: Mapped[str | None] = mapped_column(Text, ForeignKey("punches.id", ondelete="SET NULL"))
    linked_record_id: Mapped[str | None] = mapped_column(Text, ForeignKey("records.id", ondelete="SET NULL"))
    parameter_key: Mapped[str | None] = mapped_column(Text)
    labour_minutes: Mapped[int | None] = mapped_column(Integer)
    labour_notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    comments: Mapped[list[WorkOrderComment]] = relationship(
        back_populates="work_order", cascade="all, delete-orphan", order_by="WorkOrderComment.created_at"
    )
    attachments: Mapped[list[WorkOrderAttachment]] = relationship(
        back_populates="work_order", cascade="all, delete-orphan"
    )
    labour_entries: Mapped[list[WorkOrderLabour]] = relationship(
        back_populates="work_order", cascade="all, delete-orphan", order_by="WorkOrderLabour.created_at"
    )


class WorkOrderComment(Base):
    __tablename__ = "work_order_comments"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    work_order_id: Mapped[str] = mapped_column(
        Text, ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    author: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    work_order: Mapped[WorkOrder] = relationship(back_populates="comments")


class WorkOrderAttachment(Base):
    __tablename__ = "work_order_attachments"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    work_order_id: Mapped[str] = mapped_column(
        Text, ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int | None] = mapped_column(Integer)
    storage_path: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    work_order: Mapped[WorkOrder] = relationship(back_populates="attachments")


class WorkOrderLabour(Base):
    __tablename__ = "work_order_labour"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    work_order_id: Mapped[str] = mapped_column(
        Text, ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    author: Mapped[str | None] = mapped_column(Text)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    worked_on: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    work_order: Mapped[WorkOrder] = relationship(back_populates="labour_entries")


class AutoWorkOrderRule(Base):
    __tablename__ = "auto_work_order_rules"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    equipment_id: Mapped[str | None] = mapped_column(Text, ForeignKey("equipment.id", ondelete="CASCADE"), index=True)
    parameter_key: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[str] = mapped_column(Text, nullable=False, default="medium")
    title_template: Mapped[str | None] = mapped_column(Text)
    description_template: Mapped[str | None] = mapped_column(Text)
    assignee_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# v0.5 — document approvals
# ---------------------------------------------------------------------------


class ApprovalRoute(Base):
    __tablename__ = "approval_routes"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str | None] = mapped_column(Text, index=True)
    site_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sites.id", ondelete="SET NULL"), index=True)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[list[ApprovalRouteStep]] = relationship(
        back_populates="route", cascade="all, delete-orphan", order_by="ApprovalRouteStep.sort_order"
    )


class ApprovalRouteStep(Base):
    __tablename__ = "approval_route_steps"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    route_id: Mapped[str] = mapped_column(
        Text, ForeignKey("approval_routes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assignee_type: Mapped[str] = mapped_column(Text, nullable=False, default="role")  # role | user
    role_name: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    label: Mapped[str | None] = mapped_column(Text)
    route: Mapped[ApprovalRoute] = relationship(back_populates="steps")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_status", "status"),
        Index("ix_documents_equipment", "equipment_id"),
        Index("ix_documents_updated", "updated_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str | None] = mapped_column(Text, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    equipment_id: Mapped[str | None] = mapped_column(Text, ForeignKey("equipment.id", ondelete="SET NULL"))
    record_id: Mapped[str | None] = mapped_column(Text, ForeignKey("records.id", ondelete="SET NULL"))
    work_order_id: Mapped[str | None] = mapped_column(Text, ForeignKey("work_orders.id", ondelete="SET NULL"))
    site_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sites.id", ondelete="SET NULL"))
    route_id: Mapped[str | None] = mapped_column(Text, ForeignKey("approval_routes.id", ondelete="SET NULL"))
    current_version_id: Mapped[str | None] = mapped_column(Text)
    current_step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_by: Mapped[str | None] = mapped_column(Text)
    submitted_by_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    submitted_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.version_no"
    )
    approval_steps: Mapped[list[DocumentApprovalStep]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentApprovalStep.sort_order"
    )
    comments: Mapped[list[DocumentComment]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentComment.created_at"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(Text)
    uploaded_by_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    document: Mapped[Document] = relationship(back_populates="versions")


class DocumentApprovalStep(Base):
    __tablename__ = "document_approval_steps"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[str | None] = mapped_column(Text, ForeignKey("document_versions.id", ondelete="SET NULL"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assignee_type: Mapped[str] = mapped_column(Text, nullable=False, default="role")
    role_name: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    label: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="waiting")
    actor_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    acted_at: Mapped[str | None] = mapped_column(Text)
    document: Mapped[Document] = relationship(back_populates="approval_steps")


class DocumentComment(Base):
    __tablename__ = "document_comments"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[str | None] = mapped_column(Text, ForeignKey("document_versions.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="comment")  # comment | system
    event: Mapped[str | None] = mapped_column(Text)
    author_id: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id", ondelete="SET NULL"))
    author: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    document: Mapped[Document] = relationship(back_populates="comments")

