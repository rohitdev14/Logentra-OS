from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, FiniteFloat


class SyncBody(BaseModel):
    equipmentFields: list[dict[str, Any]] | None = None
    equipment: list[dict[str, Any]] | None = None
    templates: list[dict[str, Any]] | None = None
    records: list[dict[str, Any]] | None = None
    paramPresets: dict[str, list] | None = None


ReadingSource = Literal["manual", "qr", "import", "api", "mqtt", "modbus", "demo"]
ReadingQuality = Literal["good", "uncertain", "bad", "substituted"]


class ReadingIn(BaseModel):
    tag: str | None = None
    equipment_id: str | None = None
    parameter: str
    value: FiniteFloat
    ts: str | None = None
    timestamp: str | None = None
    date: str | None = None
    time: str | None = None
    remarks: str | None = None
    entered_by: str | None = None
    source: ReadingSource | None = Field("api", description="Origin of the reading. Modbus is reserved for the edge connector.")
    entry_id: str | None = None
    unit: str | None = Field(None, description="Recorded unit. Omit to snapshot the equipment parameter's unit. No conversion is performed.")
    quality: ReadingQuality | None = Field(None, description="Defaults to good when omitted.")
    idempotency_key: str | None = Field(None, description="Optional retry key unique per equipment, at most 1024 UTF-8 bytes. Reusing it for different data returns 409.")
    label: str | None = None
    id: str | None = None
    annot_cause: str | None = None
    annot_action: str | None = None
    annot_by: str | None = None
    annot_at: str | None = None


class BulkReadingsIn(BaseModel):
    readings: list[ReadingIn] = Field(max_length=10000)
    create_missing_equipment: bool = False


class AnnotationIn(BaseModel):
    cause: str | None = ""
    action: str | None = ""
    by: str | None = ""
    at: str | None = None


class WebhookIn(BaseModel):
    url: str
    secret: str | None = ""
    events: list[str] = Field(default_factory=lambda: ["out_of_range"])
    enabled: bool = True



class ReplayIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
