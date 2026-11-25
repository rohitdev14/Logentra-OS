"""Shift scheduling and roster API."""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from logentra_server import shifts
from logentra_server.models import Role
from logentra_server.shift_models import (
    Crew, CrewMember, RosterEntry, ShiftDefinition, ShiftLogCategory, ShiftPattern, SiteHandoverSettings,
)
from logentra_server.utils import uid, utcnow_iso


class PatternIn(BaseModel):
    site_id: str
    name: str = Field(min_length=1, max_length=120)
    timezone: str = Field(max_length=100)


class PatternPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=100)


class DefinitionIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    start_time: time
    duration_minutes: int = Field(ge=1, le=1440)
    sort_order: int = 0

    @field_validator("start_time")
    @classmethod
    def clock_minutes(cls, value):
        if value.tzinfo or value.second or value.microsecond:
            raise ValueError("Enter a local time in HH:MM format")
        return value

    def column_values(self):
        return {**self.model_dump(), "start_time": self.start_time.isoformat(timespec="minutes")}


class CrewIn(BaseModel):
    site_id: str
    name: str = Field(min_length=1, max_length=120)


class CrewPatch(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberIn(BaseModel):
    user_id: str
    is_lead: bool = False


class RosterIn(BaseModel):
    shift_definition_id: str
    date: date
    crew_id: str
    area_id: str | None = None
    lead_user_id: str | None = None


class RosterRepeat(BaseModel):
    first_date: date
    last_date: date
    interval_days: int = Field(7, ge=1, le=90)
    repetitions: int = Field(1, ge=1, le=52)
    area_id: str | None = None


class HandoverSettingsIn(BaseModel):
    ack_within_minutes: int = Field(30, ge=1, le=1440)
    escalate_to_role: str = Field("supervisor", min_length=1, max_length=100)
    signoff_reminder_minutes: int = Field(15, ge=0, le=1440)


class CategoryIn(BaseModel):
    site_id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    sort_order: int = 0
    active: bool = True


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sort_order: int | None = None
    active: bool | None = None


def settings_for(db, site_id):
    row = db.get(SiteHandoverSettings, site_id)
    return HandoverSettingsIn.model_validate(shifts.columns(row)) if row else HandoverSettingsIn()


def build_router(get_db, get_principal):
    router = APIRouter(prefix="/api", tags=["shifts"])

    def reader(principal=Depends(get_principal)):
        principal.require("shiftlog.read")
        return principal

    def manager(principal=Depends(get_principal)):
        principal.require("shifts.manage")
        return principal

    def pattern_for(db, identifier, principal):
        row = shifts.load(db, ShiftPattern, identifier, "Shift pattern")
        shifts.site_for(db, row.site_id, principal)
        return row

    def crew_for(db, identifier, principal):
        row = shifts.load(db, Crew, identifier, "Crew")
        shifts.site_for(db, row.site_id, principal)
        return row

    @router.get("/shift-patterns")
    def patterns(db: Session = Depends(get_db), principal=Depends(reader)):
        query = db.query(ShiftPattern)
        if principal.site_id:
            query = query.filter_by(site_id=principal.site_id)
        return [shifts.columns(row) for row in query.order_by(ShiftPattern.name)]

    @router.post("/shift-patterns", status_code=201)
    def add_pattern(body: PatternIn, db: Session = Depends(get_db), principal=Depends(manager)):
        return shifts.create_pattern(db, principal, body.model_dump())

    @router.patch("/shift-patterns/{identifier}")
    def patch_pattern(identifier: str, body: PatternPatch, db: Session = Depends(get_db),
                      principal=Depends(manager)):
        row = pattern_for(db, identifier, principal)
        if body.timezone is not None:
            shifts.timezone_for(body.timezone)
            row.timezone = body.timezone
        if body.name is not None:
            row.name = body.name
        row.updated_at = utcnow_iso()
        return shifts.changed(db, principal, row, "shift_pattern")

    @router.get("/shift-patterns/{identifier}/definitions")
    def definitions(identifier: str, db: Session = Depends(get_db), principal=Depends(reader)):
        pattern_for(db, identifier, principal)
        return [shifts.columns(row) for row in db.query(ShiftDefinition).filter_by(pattern_id=identifier)
                .order_by(ShiftDefinition.sort_order, ShiftDefinition.start_time)]

    @router.post("/shift-patterns/{identifier}/definitions", status_code=201)
    def add_definition(identifier: str, body: DefinitionIn, db: Session = Depends(get_db),
                       principal=Depends(manager)):
        pattern_for(db, identifier, principal)
        row = ShiftDefinition(id=uid("definition"), pattern_id=identifier, **body.column_values())
        db.add(row)
        return shifts.changed(db, principal, row, "shift_definition")

    @router.patch("/shift-patterns/{identifier}/definitions/{definition_id}")
    def patch_definition(identifier: str, definition_id: str, body: DefinitionIn,
                         db: Session = Depends(get_db), principal=Depends(manager)):
        pattern_for(db, identifier, principal)
        row = shifts.load(db, ShiftDefinition, definition_id, "Shift definition")
        if row.pattern_id != identifier:
            raise HTTPException(404, "Shift definition not found")
        for field, value in body.column_values().items():
            setattr(row, field, value)
        return shifts.changed(db, principal, row, "shift_definition")

    @router.get("/crews")
    def crews(db: Session = Depends(get_db), principal=Depends(manager)):
        query = db.query(Crew)
        if principal.site_id:
            query = query.filter_by(site_id=principal.site_id)
        return [shifts.columns(row) for row in query.order_by(Crew.name)]

    @router.post("/crews", status_code=201)
    def add_crew(body: CrewIn, db: Session = Depends(get_db), principal=Depends(manager)):
        shifts.site_for(db, body.site_id, principal)
        row = Crew(id=uid("crew"), **body.model_dump())
        db.add(row)
        return shifts.changed(db, principal, row, "crew")

    @router.patch("/crews/{identifier}")
    def patch_crew(identifier: str, body: CrewPatch, db: Session = Depends(get_db), principal=Depends(manager)):
        row = crew_for(db, identifier, principal)
        row.name = body.name
        return shifts.changed(db, principal, row, "crew")

    @router.get("/crews/{identifier}/members")
    def members(identifier: str, db: Session = Depends(get_db), principal=Depends(manager)):
        crew_for(db, identifier, principal)
        return [shifts.columns(row) for row in db.query(CrewMember).filter_by(crew_id=identifier)]

    @router.post("/crews/{identifier}/members")
    @router.patch("/crews/{identifier}/members")
    def add_member(identifier: str, body: MemberIn, db: Session = Depends(get_db), principal=Depends(manager)):
        crew = crew_for(db, identifier, principal)
        shifts.validate_crew_user(db, body.user_id, crew.site_id, principal.area_id)
        row = db.get(CrewMember, (identifier, body.user_id))
        if not row:
            row = CrewMember(crew_id=identifier, user_id=body.user_id)
            db.add(row)
        row.is_lead = body.is_lead
        shifts.audited(db, principal, "shifts.settings_changed", "crew", identifier, body.model_dump())
        db.flush()
        result = shifts.columns(row)
        db.commit()
        return result

    @router.delete("/crews/{identifier}/members/{user_id}")
    def remove_member(identifier: str, user_id: str, db: Session = Depends(get_db), principal=Depends(manager)):
        crew_for(db, identifier, principal)
        row = shifts.load(db, CrewMember, (identifier, user_id), "Crew member")
        db.delete(row)
        shifts.audited(db, principal, "shifts.settings_changed", "crew", identifier, {"removed_user_id": user_id})
        db.commit()
        return {"ok": True}

    @router.get("/roster")
    def roster(start: date = Query(alias="from"), end: date = Query(alias="to"),
               area: str | None = None, db: Session = Depends(get_db), principal=Depends(reader)):
        if (end - start).days not in range(367):
            raise HTTPException(422, "Choose a date range of at most 367 days")
        query = shifts.roster_query(db, principal)
        if area:
            query = query.filter(RosterEntry.area_id == area)
        return [shifts.columns(row) for row in query.filter(RosterEntry.date.between(start.isoformat(), end.isoformat()))
                .order_by(RosterEntry.date)]

    @router.put("/roster")
    def write_roster(body: list[RosterIn], db: Session = Depends(get_db), principal=Depends(manager)):
        if not 1 <= len(body) <= 500:
            raise HTTPException(422, "Provide between 1 and 500 roster entries")
        result = [shifts.columns(shifts.upsert_roster(db, principal, item.model_dump(mode="json"))) for item in body]
        db.commit()
        return result

    @router.post("/roster/repeat")
    def repeat_roster(body: RosterRepeat, db: Session = Depends(get_db), principal=Depends(manager)):

        if not 0 <= (body.last_date - body.first_date).days < body.interval_days:
            raise HTTPException(422, "The source dates must fit inside the repeat interval")
        query = shifts.roster_query(db, principal).filter(
            RosterEntry.date.between(body.first_date.isoformat(), body.last_date.isoformat())
        )
        if body.area_id:
            query = query.filter(RosterEntry.area_id == body.area_id)
        source = query.all()
        if len(source) * body.repetitions > 2000:
            raise HTTPException(422, "Repeat at most 2,000 roster entries at a time")
        count = 0
        for index in range(1, body.repetitions + 1):
            for row in source:
                item = shifts.columns(row)
                item.pop("id")
                item["date"] = (date.fromisoformat(row.date) + timedelta(days=body.interval_days * index)).isoformat()
                shifts.upsert_roster(db, principal, item)
                count += 1
        db.commit()
        return {"count": count}

    @router.get("/sites/{identifier}/handover-settings")
    def get_settings(identifier: str, db: Session = Depends(get_db), principal=Depends(manager)):
        shifts.site_for(db, identifier, principal)
        return settings_for(db, identifier)

    @router.put("/sites/{identifier}/handover-settings")
    def put_settings(identifier: str, body: HandoverSettingsIn, db: Session = Depends(get_db),
                     principal=Depends(manager)):
        shifts.site_for(db, identifier, principal)
        if not db.query(Role.id).filter_by(name=body.escalate_to_role).first():
            raise HTTPException(422, "Choose an existing role")
        row = db.get(SiteHandoverSettings, identifier)
        if not row:
            row = SiteHandoverSettings(site_id=identifier)
            db.add(row)
        for field, value in body.model_dump().items():
            setattr(row, field, value)
        return shifts.changed(db, principal, row)

    @router.get("/shift-log-categories")
    def categories(site: str | None = None, db: Session = Depends(get_db), principal=Depends(reader)):
        query = db.query(ShiftLogCategory)
        site = site or principal.site_id
        if site:
            shifts.site_for(db, site, principal)
            query = query.filter(or_(ShiftLogCategory.site_id == site, ShiftLogCategory.site_id.is_(None)))
        return [shifts.columns(row) for row in query.order_by(ShiftLogCategory.sort_order, ShiftLogCategory.name)]

    @router.post("/shift-log-categories", status_code=201)
    def add_category(body: CategoryIn, db: Session = Depends(get_db), principal=Depends(manager)):
        if body.site_id:
            shifts.site_for(db, body.site_id, principal)
        else:
            principal.require_global()
        row = ShiftLogCategory(id=uid("shift_cat"), **body.model_dump())
        db.add(row)
        return shifts.changed(db, principal, row, "shift_log_category")

    @router.patch("/shift-log-categories/{identifier}")
    def patch_category(identifier: str, body: CategoryPatch, db: Session = Depends(get_db),
                       principal=Depends(manager)):
        row = shifts.load(db, ShiftLogCategory, identifier, "Log category")
        if row.site_id:
            shifts.site_for(db, row.site_id, principal)
        else:
            principal.require_global()
        for field, value in body.model_dump(exclude_none=True).items():
            setattr(row, field, value)
        return shifts.changed(db, principal, row, "shift_log_category")

    @router.get("/shifts/current")
    def current(area: str | None = None, db: Session = Depends(get_db), principal=Depends(reader)):
        rows = shifts.current_shifts(db, datetime.now(timezone.utc), principal, area)
        db.flush()
        result = [shifts.describe_shift(db, row) for row in rows]
        db.commit()
        return {"items": result}

    return router
