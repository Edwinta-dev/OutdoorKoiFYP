"""
local_time.py

Calendar-day boundaries for the models. A pond's "day" runs from local
midnight to local midnight in the pond's time zone, the same definition
the database's aggregate_daily_sensor_data() uses
((created_at AT TIME ZONE 'Asia/Singapore')::date). Instants stay
timezone-aware UTC everywhere else; only the calendar date is local.

A calendar date is not an overnight episode: 20:00 on day D and 06:00 on
D+1 fall on different dates. A model that needs one key per night must
define its own night-window key rather than bend the calendar day.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

DEFAULT_POND_TIME_ZONE = "Asia/Singapore"


@lru_cache(maxsize=16)
def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def as_aware(t: datetime) -> datetime:
    """t unchanged if it carries a zone; a naive t is taken as UTC, as
    storage.base.parse_timestamp does for database values."""
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def local_date(t: datetime, time_zone: str = DEFAULT_POND_TIME_ZONE) -> date:
    """The calendar date of instant t in the pond's time zone."""
    return as_aware(t).astimezone(zone(time_zone)).date()


def local_day_key(t: datetime, time_zone: str = DEFAULT_POND_TIME_ZONE) -> str:
    """ISO date (YYYY-MM-DD, zero-padded) of t's local calendar day. Matches
    daily_sensor_averages.record_date for a sample with the same created_at."""
    return local_date(t, time_zone).isoformat()
