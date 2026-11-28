"""Time-zone-aware roster and shift lifecycle."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from logentra_server import authz
from logentra_server import service
from logentra_server.models import Area, User
from logentra_server.shift_models import Crew, CrewMember, RosterEntry, Shift, ShiftDefinition, ShiftPattern
from logentra_server.sitetime import INVALID_TIMEZONE
from logentra_server.utils import utcnow_iso, uid


def columns(row):
    return {col.key: getattr(row, col.key) for col in row.__table__.columns}


def audited(db, principal, action, kind, identifier, detail=None):
    service.audit(db, action, actor=principal.username if principal else "system",
                  user_id=principal.user_id if principal else None, entity_type=kind,
                  entity_id=identifier, detail=detail)


def utc_text(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timezone_for(value):
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, INVALID_TIMEZONE) from None


scoped = authz.require_location


def site_for(db, site_id, principal):
    return authz.require_object(db, principal, "site", site_id, "access")


def load(db, model, identifier, label):
    row = db.get(model, identifier)
    if row is None:
        raise HTTPException(404, f"{label} not found")
    return row


def validate_area(db, site_id, area_id, principal):
    scoped(principal, site_id, area_id)
    if area_id and load(db, Area, area_id, "Area").site_id != site_id:
        raise HTTPException(422, "Area belongs to a different site")


def validate_crew_user(db, user_id, site, area=None):
    user = load(db, User, user_id, "User")
    if not user.is_active or (user.site_id and user.site_id != site) or (
        user.area_id and area and user.area_id != area
    ):
        raise HTTPException(422, "Choose an active crew member in scope")
    return user


def upsert_roster(db, principal, values):
    definition = load(db, ShiftDefinition, values["shift_definition_id"], "Shift definition")
    pattern = load(db, ShiftPattern, definition.pattern_id, "Shift pattern")
    validate_area(db, pattern.site_id, values["area_id"], principal)
    crew = load(db, Crew, values["crew_id"], "Crew")
    if crew.site_id != pattern.site_id:
        raise HTTPException(422, "Crew belongs to a different site")
    if values["lead_user_id"]:
        validate_crew_user(db, values["lead_user_id"], pattern.site_id, values["area_id"])
    row = db.query(RosterEntry).filter_by(shift_definition_id=definition.id,
                                         date=values["date"], area_id=values["area_id"]).one_or_none()
    if row:
        row.crew_id = crew.id
        row.lead_user_id = values["lead_user_id"]
    else:
        row = RosterEntry(id=uid("roster"), **values)
        db.add(row)
    audited(db, principal, "roster.changed", "roster", row.id, values)
    db.flush()
    return row


def roster_query(db, principal):
    query = db.query(RosterEntry).join(ShiftDefinition, ShiftDefinition.id == RosterEntry.shift_definition_id).join(
        ShiftPattern, ShiftPattern.id == ShiftDefinition.pattern_id
    )
    if principal.site_id:
        query = query.filter(ShiftPattern.site_id == principal.site_id)
    if principal.area_id:
        query = query.filter(RosterEntry.area_id == principal.area_id)
    return query


def slots(db, first, last, *, area=None, site=None):
    query = db.query(RosterEntry, ShiftDefinition, ShiftPattern).join(
        ShiftDefinition, ShiftDefinition.id == RosterEntry.shift_definition_id
    ).join(ShiftPattern, ShiftPattern.id == ShiftDefinition.pattern_id).filter(RosterEntry.date.between(first, last))
    if area is not None:
        query = query.filter(RosterEntry.area_id == area)
    if site is not None:
        query = query.filter(ShiftPattern.site_id == site)
    for roster, definition, pattern in query:
        local = datetime.combine(date.fromisoformat(roster.date), time.fromisoformat(definition.start_time),
                                 timezone_for(pattern.timezone))
        start = local.astimezone(timezone.utc)
        end = start + timedelta(minutes=definition.duration_minutes)
        yield roster, definition, pattern, start, end


def materialize(db, slot, now, principal=None):
    roster, definition, pattern, start, end = slot
    current = db.query(Shift).filter_by(shift_definition_id=definition.id, date=roster.date,
                                       area_id=roster.area_id).one_or_none()
    if current and current.status != "planned":
        return current
    lead = roster.lead_user_id
    if not lead:
        candidates = db.query(User).join(CrewMember, CrewMember.user_id == User.id).filter(
            CrewMember.crew_id == roster.crew_id, CrewMember.is_lead.is_(True), User.is_active == 1,
        ).order_by(User.username)
        lead = next((user.id for user in candidates if (not user.area_id or user.area_id == roster.area_id)
                     and (not user.site_id or user.site_id == pattern.site_id)), None)
    started = start <= now
    if current:
        current.starts_at, current.ends_at = utc_text(start), utc_text(end)
        current.crew_id, current.lead_user_id = roster.crew_id, lead
        if started:
            current.status = "active"
            current.opened_at = utc_text(now)
            audited(db, principal, "shift.started", "shift", current.id)
        return current
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else postgres_insert
    current = db.execute(insert(Shift).values(
        id=uid("shift"), site_id=pattern.site_id, area_id=roster.area_id,
        shift_definition_id=definition.id, date=roster.date, starts_at=utc_text(start), ends_at=utc_text(end),
        crew_id=roster.crew_id, lead_user_id=lead, status="active" if started else "planned",
        opened_at=utc_text(now) if started else None,
    ).on_conflict_do_nothing().returning(Shift)).scalar_one_or_none()
    if current is None:
        return db.query(Shift).filter_by(shift_definition_id=definition.id, date=roster.date,
                                         area_id=roster.area_id).one()
    if started:
        audited(db, principal, "shift.started", "shift", current.id)
    return current


def current_shifts(db, now, principal=None, area=None):
    day = now.date()
    first = (day - timedelta(days=3)).isoformat()
    last = (day + timedelta(days=2)).isoformat()
    instant = utc_text(now)
    query = db.query(Shift).filter(Shift.starts_at <= instant, Shift.ends_at > instant, Shift.status != "planned")
    if area:
        query = query.filter(Shift.area_id == area)
    query = service.apply_scope(query, Shift, principal)
    matches = {row.id: row for row in query}
    existing_slots = {(row.shift_definition_id, row.date, row.area_id) for row in matches.values()}
    for slot in slots(db, first, last, area=area, site=principal.site_id if principal else None):
        roster, definition, pattern, start, end = slot
        if principal and not principal.allows_scope(pattern.site_id, roster.area_id):
            continue
        slot_key = (definition.id, roster.date, roster.area_id)
        if slot_key not in existing_slots and start <= now < end:
            row = materialize(db, slot, now, principal)
            matches[row.id] = row
    return sorted(matches.values(), key=lambda row: (row.area_id or "", row.starts_at), reverse=True)


def next_shift(db, outgoing, now):
    end = datetime.fromisoformat(outgoing.ends_at)
    first = (end.date() - timedelta(days=2)).isoformat()
    last = (end.date() + timedelta(days=2)).isoformat()
    matches = [slot for slot in slots(db, first, last, area=outgoing.area_id, site=outgoing.site_id)
               if slot[0].area_id == outgoing.area_id and end <= slot[3] < end + timedelta(days=1)]
    return materialize(db, min(matches, key=lambda slot: slot[3]), now) if matches else None


def describe_shift(db, row):
    definition = db.get(ShiftDefinition, row.shift_definition_id)
    pattern = db.get(ShiftPattern, definition.pattern_id)
    area = db.get(Area, row.area_id) if row.area_id else None
    crew = db.get(Crew, row.crew_id)
    lead = db.get(User, row.lead_user_id) if row.lead_user_id else None
    return {**columns(row), "timezone": pattern.timezone, "name": definition.name,
            "area_name": area.name if area else "", "crew_name": crew.name,
            "lead_name": (lead.display_name or lead.username) if lead else ""}


def changed(db, principal, row, entity="shift_settings"):
    audited(db, principal, "shifts.settings_changed", entity,
            getattr(row, "id", None) or getattr(row, "site_id", None))
    db.flush()
    result = columns(row)
    db.commit()
    return result


def create_pattern(db, principal, values):
    site_for(db, values["site_id"], principal)
    timezone_for(values["timezone"])
    now = utcnow_iso()
    row = ShiftPattern(id=uid("pattern"), created_at=now, updated_at=now, **values)
    db.add(row)
    return changed(db, principal, row, "shift_pattern")
