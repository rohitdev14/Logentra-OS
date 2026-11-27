"""Store log book readings as UTC and infer the timezone of each site."""

import json
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from alembic import context, op

from logentra_server.sitetime import local_to_utc_text
from logentra_server.utils import uid, utcnow_iso

revision = "0011_site_timezone"
down_revision = "0010_record_approval"
branch_labels = None
depends_on = None

SOURCES = "readings.source IN ('manual', 'import', 'demo')"
EQUIPMENT_SCOPE = """
    FROM equipment LEFT JOIN sites ON sites.id = equipment.site_id
"""


def valid_zone(name):
    if name:
        try:
            return ZoneInfo(name).key
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return None


def convert_postgres(db, zone, fallback):
    scope = f"""
        {EQUIPMENT_SCOPE}
        WHERE equipment.id = readings.equipment_id AND {SOURCES}
          AND COALESCE(sites.timezone, :fallback) = :zone
    """
    params = {"zone": zone, "fallback": fallback}
    db.execute(sa.text(f"""
        INSERT INTO reading_timezone_backfill (reading_id, original_ts)
        SELECT readings.id, readings.ts FROM readings, equipment
        LEFT JOIN sites ON sites.id = equipment.site_id
        WHERE equipment.id = readings.equipment_id AND {SOURCES}
          AND COALESCE(sites.timezone, :fallback) = :zone
    """), params)
    stamp = "CAST(readings.ts AS timestamp)"
    # PostgreSQL normally chooses the later instant for a fall-back fold.
    # Use the previous day's offset when it also maps back to this wall time.
    earlier = f"""(({stamp} AT TIME ZONE 'UTC') +
        ((({stamp} - interval '1 day') AT TIME ZONE :zone) -
         (({stamp} - interval '1 day') AT TIME ZONE 'UTC')))"""
    converted = f"""(CASE WHEN ({earlier} AT TIME ZONE :zone) = {stamp}
        THEN {earlier} ELSE {stamp} AT TIME ZONE :zone END) AT TIME ZONE 'UTC'"""
    return db.execute(sa.text(f"""
        UPDATE readings SET ts = to_char({converted}, 'YYYY-MM-DD"T"HH24:MI:SS')
        {scope}
    """), params).rowcount


def convert_sqlite(db, fallback):
    counts = {}
    last = None
    while True:
        cursor = "AND readings.id > :last" if last is not None else ""
        rows = db.execute(sa.text(f"""
            SELECT readings.id, readings.ts, COALESCE(sites.timezone, :fallback) AS zone
            FROM readings JOIN equipment ON equipment.id = readings.equipment_id
            LEFT JOIN sites ON sites.id = equipment.site_id
            WHERE {SOURCES} AND COALESCE(sites.timezone, :fallback) NOT IN ('UTC', 'Etc/UTC')
                {cursor}
            ORDER BY readings.id LIMIT 5000
        """), {"last": last, "fallback": fallback}).all()
        if not rows:
            return counts
        db.execute(sa.text("""
            INSERT INTO reading_timezone_backfill (reading_id, original_ts) VALUES (:id, :ts)
        """), [{"id": identifier, "ts": ts} for identifier, ts, _ in rows])
        updates = []
        for identifier, ts, name in rows:
            date, clock = ts.split("T", 1)
            updates.append({"id": identifier, "ts": local_to_utc_text(date, clock, ZoneInfo(name))})
            counts[name] = counts.get(name, 0) + 1
        db.execute(sa.text("UPDATE readings SET ts = :ts WHERE id = :id"), updates)
        last = rows[-1].id


def convert_readings(db, fallback):
    if db.dialect.name == "postgresql":
        zones = set(db.execute(sa.text("SELECT DISTINCT timezone FROM sites WHERE timezone IS NOT NULL")).scalars())
        counts = {}
        for zone in sorted((zones | {fallback}) - {"UTC", "Etc/UTC"}):
            count = convert_postgres(db, zone, fallback)
            if count:
                counts[zone] = count
        return counts
    return convert_sqlite(db, fallback)


def upgrade():
    op.add_column("sites", sa.Column("timezone", sa.Text()))
    # Keep the original wall time for downgrade, including DST gaps and later timezone edits.
    op.create_table("reading_timezone_backfill",
                    sa.Column("reading_id", sa.Text(), sa.ForeignKey("readings.id", ondelete="CASCADE"), primary_key=True),
                    sa.Column("original_ts", sa.Text(), nullable=False))
    db = op.get_bind()
    configured = (
        valid_zone(context.config.attributes.get("install_timezone"))
        or valid_zone(os.environ.get("LOGENTRA_TIMEZONE"))
    )
    sites = db.execute(sa.text("""
        SELECT sites.id, CASE WHEN COUNT(DISTINCT shift_patterns.timezone) = 1
            THEN MIN(shift_patterns.timezone) END
        FROM sites LEFT JOIN shift_patterns ON shift_patterns.site_id = sites.id
        GROUP BY sites.id
    """)).all()
    for site_id, pattern_zone in sites:
        zone = valid_zone(pattern_zone) or configured
        if zone:
            db.execute(sa.text("UPDATE sites SET timezone = :zone WHERE id = :id"),
                       {"zone": zone, "id": site_id})
    counts = convert_readings(db, configured or "UTC")
    db.execute(sa.text("""
        INSERT INTO audit_log (id, ts, actor, action, entity_type, detail)
        VALUES (:id, :ts, 'migration', 'readings.timezone_backfill', 'reading', :detail)
    """), {"id": uid("aud"), "ts": utcnow_iso(), "detail": json.dumps(counts)})


def downgrade():
    op.execute("""
        UPDATE readings SET ts = reading_timezone_backfill.original_ts
        FROM reading_timezone_backfill WHERE reading_timezone_backfill.reading_id = readings.id
    """)
    op.drop_table("reading_timezone_backfill")
    op.drop_column("sites", "timezone")
