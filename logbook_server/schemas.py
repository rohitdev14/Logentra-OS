from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SyncBody(BaseModel):
    equipmentFields: list[dict[str, Any]] | None = None
    equipment: list[dict[str, Any]] | None = None
    templates: list[dict[str, Any]] | None = None
    records: list[dict[str, Any]] | None = None
    paramPresets: dict[str, list] | None = None


class ReadingIn(BaseModel):
    tag: str | None = None
    equipment_id: str | None = None
    parameter: str
    value: float
    ts: str | None = None
    date: str | None = None
    time: str | None = None
    remarks: str | None = None
    entered_by: str | None = None
    source: str | None = "api"
    entry_id: str | None = None
    unit: str | None = None
    label: str | None = None
    id: str | None = None
    annot_cause: str | None = None
    annot_action: str | None = None
    annot_by: str | None = None
    annot_at: str | None = None


class BulkReadingsIn(BaseModel):
    readings: list[ReadingIn]
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
