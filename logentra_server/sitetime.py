"""Convert between site wall-clock times and stored UTC timestamps."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
INVALID_TIMEZONE = "Enter a valid IANA timezone, such as Asia/Singapore"


def _zone(db, site) -> ZoneInfo:
    return ZoneInfo((site.timezone if site else None) or db.info.get("timezone") or "UTC")


def zone_for_site(db, site_id) -> ZoneInfo:
    from logentra_server.models import Site

    return _zone(db, db.get(Site, site_id) if site_id else None)


def zone_for_equipment(db, equipment) -> ZoneInfo:
    return _zone(db, equipment.site if equipment else None)


def local_to_utc_text(date_str: str, time_str: str, zone: ZoneInfo) -> str:
    # The pre-transition offset picks the first occurrence in a fold and rolls gaps forward.
    wall = datetime.fromisoformat(f"{date_str}T{time_str}")
    if wall.tzinfo is None:
        wall = wall.replace(tzinfo=zone, fold=0)
    return wall.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _as_utc(ts: str) -> datetime:
    value = datetime.fromisoformat(ts)
    return value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def utc_text_to_local(ts: str, zone: ZoneInfo) -> tuple[str, str]:
    local = _as_utc(ts).astimezone(zone)
    return local.date().isoformat(), local.strftime("%H:%M")


def utc_iso_z(ts: str) -> str:
    return _as_utc(ts).isoformat(timespec="seconds").replace("+00:00", "Z")


def local_day_bounds(date_str: str, zone: ZoneInfo) -> tuple[str, str]:
    """Return the start of a local day and the exclusive start of the next day."""
    day = date.fromisoformat(date_str)
    return (
        local_to_utc_text(day.isoformat(), "00:00", zone),
        local_to_utc_text((day + timedelta(days=1)).isoformat(), "00:00", zone),
    )


def today_local(zone: ZoneInfo) -> str:
    return datetime.now(zone).date().isoformat()
