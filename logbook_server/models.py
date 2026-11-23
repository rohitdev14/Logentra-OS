from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, Text, UniqueConstraint
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
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
