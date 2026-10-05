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
from functools import cache
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator
from pydantic_core import PydanticCustomError

from koi.camera import mask as camera_mask
from koi.models import algae_engine as ae
from koi.models import event_ledger

# An event timestamp outside this window is rejected. The past limit is
# the event ledger's checkpoint window: an event within it is replayed
# into its place (koi/models/event_ledger.py).
MAX_EVENT_AGE = event_ledger.WINDOW
MAX_EVENT_LEAD = timedelta(minutes=5)

MAX_HORIZON_DAYS = 60
SEVERITY_CHOICES = ae.SEVERITY_LEVELS + ["obstruction"]

# The clock timestamps are checked against; tests replace it.
utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)  # noqa: E731


class _Body(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _Query(BaseModel):
    model_config = ConfigDict(extra="ignore")


class KitReadingBody(_Body):
    user_id: int = Field(gt=0)
    taken_at: Optional[datetime] = Field(default=None, description="Sample time; null means unknown, no comparison.")
    kit: Optional[str] = Field(default=None, max_length=200)
    ammonia_mg_l: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False,
                                         description="Total ammonia (TAN), mg/L; not free NH3.")
    nitrite_mg_l: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False, description="Nitrite, mg/L.")
    nitrate_mg_l: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False, description="Nitrate, mg/L.")
    ph: Optional[float] = Field(default=None, ge=0, le=14, allow_inf_nan=False)
    kh_dkh: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False, description="Carbonate hardness, dKH.")
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("taken_at", mode="before")
    @classmethod
    def _taken_at(cls, value: object) -> Optional[datetime]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("taken_at must be an ISO 8601 string")
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("taken_at must include a time zone")
        dt = dt.astimezone(timezone.utc)
        if dt > utc_now():
            raise ValueError("taken_at cannot be in the future")
        return dt


class EventBody(_Body):
    """Fields every /events/* body shares. Fish type and count are not
    taken from a request: they come from the pond's profile
    (PUT /v1/ponds/{pond}/profile). Older app builds still send fish_type
    and fish_count; they are ignored like any unknown key."""

    user_id: int = Field(gt=0)
    timestamp: Optional[datetime] = None
    event_id: Optional[str] = Field(default=None, description=(
        "The event's UUID, the same value the app writes to pondInterventions.event_id. An event_id the pond "
        "has already applied is not applied again. Optional for older app builds."))

    @field_validator("event_id", mode="before")
    @classmethod
    def _parse_event_id(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        try:
            if not isinstance(value, str):
                raise ValueError
            return event_ledger.normalise_event_id(value)
        except ValueError:
            raise PydanticCustomError("event_id_format", "event_id must be a UUID, for example "
                                      "3f2b8c1e-7d4a-4e5b-9c6d-0a1b2c3d4e5f") from None

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
            "timestamp_too_old", "timestamp is more than {days} days in the past; the model keeps "
            "{days} days of history to place an event in", {"days": MAX_EVENT_AGE.days})
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


class SaltEvent(EventBody):
    salt_grams: float = Field(gt=0, allow_inf_nan=False, description="Added salt mass in grams.")
    notes: Optional[str] = Field(default=None, max_length=1000)


class FilterCleanEvent(EventBody):
    notes: Optional[str] = Field(default=None, max_length=1000)


class AlgalScrubEvent(EventBody):
    scrub_type: str = Field(default="unspecified", min_length=1, max_length=64)


class PromptAnswer(_Body):
    user_id: int = Field(gt=0)
    answer: Literal["water_change", "top_up", "algal_scrub", "feeding", "salt", "filter_clean",
                    "none of these", "dismiss"]
    volume_percent: Optional[float] = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    volume_litres: Optional[float] = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)
    salt_grams: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    food_grams: Optional[float] = Field(default=None, ge=0, le=10_000, allow_inf_nan=False)
    protein_percent: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    scrub_type: str = Field(default="unspecified", min_length=1, max_length=64)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _event_amounts(self):
        models = {"water_change": VolumeEvent, "top_up": VolumeEvent, "salt": SaltEvent,
                  "feeding": FeedingEvent, "filter_clean": FilterCleanEvent, "algal_scrub": AlgalScrubEvent}
        if self.answer in models:
            on_pond_path(models[self.answer]).model_validate(self.model_dump(exclude_none=True))
        return self


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


class ProfileUpdate(_Body):
    """PUT /v1/ponds/{pond}/profile: the pond's whole profile from
    effective_from (default: now). A field left out is unknown, not
    carried over. effective_from is an ISO 8601 instant; without an
    offset it is UTC."""

    effective_from: Optional[datetime] = None
    volume_l: float = Field(gt=0, le=1_000_000)
    depth_m: Optional[float] = Field(default=None, gt=0, le=10)
    biomass_g: float = Field(ge=0, le=10_000_000)
    fish_type: Optional[str] = Field(default=None, min_length=1, max_length=64)
    fish_count: Optional[int] = Field(default=None, ge=0, le=10_000)
    tap_tds_ppm: Optional[float] = Field(default=None, ge=0, le=5_000)
    tap_nitrate_ppm: Optional[float] = Field(default=None, ge=0, le=500)
    aeration: Optional[bool] = None

    @field_validator("effective_from", mode="before")
    @classmethod
    def _parse_effective_from(cls, value: object) -> Optional[datetime]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise PydanticCustomError("string_type", "effective_from must be an ISO 8601 string")
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise PydanticCustomError(
                "timestamp_format", "effective_from {value!r} is not an ISO 8601 date and time, "
                "for example 2026-08-20T09:30:00Z", {"value": value}) from None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    def effective_time(self) -> datetime:
        """effective_from, or server time when the body has none."""
        return self.effective_from if self.effective_from is not None else utc_now()


class CameraMaskUpdate(_Body):
    """PUT /v1/ponds/{pond}/camera/mask: the pond's water mask, a polygon
    of [x, y] points as fractions (0..1) of the frame's width and height,
    origin top-left (koi/camera/mask.py). Each save is a new mask version."""

    polygon: list[list[float]]

    @field_validator("polygon")
    @classmethod
    def _valid_polygon(cls, value: list[list[float]]) -> list[list[float]]:
        try:
            return camera_mask.validate_polygon(value)
        except ValueError as exc:
            raise PydanticCustomError("mask_invalid", "{reason}", {"reason": str(exc)}) from None


@cache
def on_pond_path(model: type[BaseModel]) -> type[BaseModel]:
    """model for a /v1/ponds/{pond}/... route, where the path names the
    pond: user_id may be left out, and when sent must equal the path's
    pond (checked by the route)."""
    return create_model(
        f"{model.__name__}OnPond", __base__=model,
        user_id=(Optional[int], Field(default=None, gt=0, description="Optional; when sent it must be the "
                                                                      "pond in the path.")))


class EvaporationForecastQuery(_Query):
    """depth_m, when given, replaces the profile's depth for this one
    projection; otherwise the profile's depth (or the default depth when
    the profile has none) is used."""

    horizon_days: int = Field(default=14, ge=1, le=MAX_HORIZON_DAYS)
    depth_m: Optional[float] = Field(default=None, gt=0, le=10)


__all__ = ["AlgaeRatingEvent", "AlgaeRatingUndo", "AlgalScrubEvent", "CameraMaskUpdate", "EvaporationForecastQuery",
           "EventBody", "FeedingEvent", "ForecastQuery", "MAX_EVENT_AGE", "MAX_EVENT_LEAD", "ProfileUpdate",
           "VolumeEvent", "on_pond_path", "parse_event_time"]
