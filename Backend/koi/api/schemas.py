"""Request models for every digital twin endpoint.

Bodies are validated strictly: a number sent as a string, or true for a
count, is a wrong type rather than something to coerce. Query strings
arrive as text, so their models convert "21" to 21 but still reject
"abc". Unknown keys are ignored, so an older or newer app build can send
fields this service does not use. A failed validation raises
pydantic.ValidationError, which koi.errors returns as a 400 with one
entry per invalid field.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev

# An event timestamp outside this window is rejected. The past limit is
# relaxed once backdated replay exists (the event ledger issue).
MAX_EVENT_AGE = timedelta(days=7)
MAX_EVENT_LEAD = timedelta(minutes=5)

MAX_HORIZON_DAYS = 60
SEVERITY_CHOICES = ae.SEVERITY_LEVELS + ["obstruction"]

# The clock timestamps are checked against; tests replace it.
utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)  # noqa: E731


class _Body(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _Query(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EventBody(_Body):
    """Fields every /events/* body shares. fish_type and fish_count only
    matter the first time a pond is modelled (see _default_config_for)."""

    user_id: int = Field(gt=0)
    timestamp: Optional[datetime] = None
    fish_type: Optional[str] = Field(default=None, min_length=1, max_length=64)
    fish_count: Optional[int] = Field(default=None, ge=0, le=10_000)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: object) -> Optional[datetime]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise PydanticCustomError("string_type", "timestamp must be an ISO 8601 string")
        return parse_event_time(value)

    def event_time(self) -> datetime:
        """The event's time; server time when the body has no timestamp."""
        return self.timestamp if self.timestamp is not None else utc_now()


def parse_event_time(value: str) -> datetime:
    """An ISO 8601 event timestamp as an aware datetime (no zone means
    UTC), checked against the accepted window."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PydanticCustomError(
            "timestamp_format", "timestamp {value!r} is not an ISO 8601 date and time, "
            "for example 2026-08-20T09:30:00Z", {"value": value}) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = utc_now()
    if dt < now - MAX_EVENT_AGE:
        raise PydanticCustomError(
            "timestamp_too_old", "timestamp is more than {days} days in the past; events older than "
            "that cannot be logged yet", {"days": MAX_EVENT_AGE.days})
    if dt > now + MAX_EVENT_LEAD:
        raise PydanticCustomError(
            "timestamp_in_future", "timestamp is more than {minutes} minutes in the future; check the "
            "phone's clock", {"minutes": int(MAX_EVENT_LEAD.total_seconds() // 60)})
    return dt


class FeedingEvent(EventBody):
    food_grams: float = Field(ge=0, le=10_000)
    protein_percent: float = Field(ge=0, le=100)


class VolumeEvent(EventBody):
    """Water change or top-up: at least one of the two volumes."""

    volume_percent: Optional[float] = Field(default=None, gt=0, le=100)
    volume_litres: Optional[float] = Field(default=None, gt=0, le=1_000_000)

    @model_validator(mode="after")
    def _needs_a_volume(self):
        if self.volume_percent is None and self.volume_litres is None:
            raise PydanticCustomError("volume_missing", "volume_percent or volume_litres is required")
        return self


class AlgalScrubEvent(EventBody):
    scrub_type: str = Field(default="unspecified", min_length=1, max_length=64)


class AlgaeRatingEvent(EventBody):
    severity: str
    image_id: Optional[int] = Field(default=None, gt=0)
    green_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("severity")
    @classmethod
    def _known_severity(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in SEVERITY_CHOICES:
            raise PydanticCustomError("severity_unknown", "severity must be one of {choices}",
                                      {"choices": ", ".join(SEVERITY_CHOICES)})
        return value


class AlgaeRatingUndo(_Body):
    user_id: int = Field(gt=0)
    rating_id: Optional[int] = Field(default=None, gt=0)


class ForecastQuery(_Query):
    horizon_days: int = Field(default=21, ge=1, le=MAX_HORIZON_DAYS)


class EvaporationForecastQuery(_Query):
    horizon_days: int = Field(default=14, ge=1, le=MAX_HORIZON_DAYS)
    depth_m: float = Field(default=ev.DEFAULT_POND_DEPTH_M, gt=0, le=10)


__all__ = ["AlgaeRatingEvent", "AlgaeRatingUndo", "AlgalScrubEvent", "EvaporationForecastQuery", "EventBody",
           "FeedingEvent", "ForecastQuery", "MAX_EVENT_AGE", "MAX_EVENT_LEAD", "VolumeEvent", "parse_event_time"]
