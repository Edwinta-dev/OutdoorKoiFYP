"""Response models for every digital twin endpoint.

These describe what the API sends; docs/api/openapi.yaml is generated
from them (koi/api/openapi.py). The engine payloads (assessments,
forecasts, rating results) are open models: they name every field a
client may read, Dart included, and allow the engines to add fields. The
/v1 dashboard models are closed: the response has exactly these fields.

Where a model mirrors a Dart class in
MobileUI/mobile_app/lib/utils/digital_twin_api.dart it has the same name,
and tests/api/test_contract.py checks that every key the Dart class reads
is a property of the schema.

Times in the dashboard are ISO 8601 instants in UTC. Units are named in
each field's description, and value fields are null whenever there is no
trustworthy value: nothing is substituted.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Open(BaseModel):
    """An engine payload: the listed fields, plus any the engine adds."""

    model_config = ConfigDict(extra="allow")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------
# Errors (koi/errors.py)
# ---------------------------------------------------------------------
class ErrorDetail(_Closed):
    code: str = Field(description="Stable snake_case code a client can branch on.")
    message: str = Field(description="Plain text for a person.")
    details: dict[str, Any] = Field(description="Machine-readable context; {} when there is none.")


class ErrorEnvelope(_Open):
    """Every non-2xx response."""

    error: ErrorDetail


# ---------------------------------------------------------------------
# Assessments (engine to_dict() or the latest evaluation row)
# ---------------------------------------------------------------------
class _Assessment(_Open):
    status: str = Field(description="Green, Amber or Red.")
    category: str
    advisory: Optional[str] = None
    evaluated_at: Optional[str] = Field(default=None, description="Set on a stored evaluation row.")
    id: Optional[int] = None
    userid: Optional[int] = None


class WaterChemistryAssessment(_Assessment):
    tan_ppm: Optional[float] = None
    no2_ppm: Optional[float] = None
    no3_ppm: Optional[float] = None
    ph_reactivity: Optional[float] = None
    reactivity_trend: Optional[float] = None
    tds_trend: Optional[float] = None
    sensor_warnings: Optional[list[Any]] = None
    add_hardener_now: Optional[bool] = None
    risk_score: Optional[int] = Field(default=None, description="In a fresh assessment only; no column stores it.")


class EvaporationAssessment(_Assessment):
    loss_litres: Optional[float] = None
    loss_pct: Optional[float] = None
    evaporation_mm_per_day: Optional[float] = None
    loss_litres_per_day: Optional[float] = None
    water_temp_c: Optional[float] = None
    feed_cap_grams: Optional[float] = None
    feed_note: Optional[str] = None
    days_to_topup: Optional[int] = None
    topup_now: Optional[bool] = None


class AlgaeAssessment(_Assessment):
    green_ratio: Optional[float] = None
    watch_threshold: Optional[float] = None
    action_threshold: Optional[float] = None
    threshold_mode: Optional[str] = None
    growth_rate_per_day: Optional[float] = None
    intrinsic_rate_per_day: Optional[float] = None
    rate_source: Optional[str] = None
    confidence: Optional[str] = None
    sample_count: Optional[int] = None
    days_to_scrub: Optional[int] = None
    scrub_now: Optional[bool] = None
    calibrated: Optional[bool] = None
    camera_drift: Optional[str] = None
    label_count: Optional[int] = None


class HypoxiaFlag(_Open):
    """Night-time low-oxygen risk (koi/models/hypoxia.py)."""

    level: Literal["unknown", "none", "watch", "high"]
    flagged: bool
    dark: Optional[bool]
    lux: Optional[float]
    water_temp_c: Optional[float]
    aeration: Optional[bool]
    algae_high: Optional[bool]
    watch_temp_c: float
    high_temp_c: float
    night_lux_threshold: float
    raised_by: list[str]
    explanation: str
    advice: list[str]


class EventOutcome(_Open):
    """What the twin did with the event (koi/models/event_ledger.py)."""

    event_id: str = Field(description="The event's UUID; legacy:<n> when the request had none.")
    status: Literal["applied", "duplicate", "deleted"] = Field(description=(
        "duplicate: this event_id was already applied, nothing changed. deleted: it was applied and later "
        "deleted from the log, nothing changed."))
    replayed: bool = Field(description="The event was in the past and the chemistry model was replayed from "
                                       "the checkpoint before it.")
    replayed_from: Optional[str] = Field(description="The replay's checkpoint time; null for the twin's start "
                                                     "or when there was no replay.")
    placed_late: bool = Field(description="The event was older than every checkpoint and was applied at the "
                                          "current state instead of at its time.")


class EventAssessments(_Open):
    """All three fresh assessments after an event."""

    chemistry: WaterChemistryAssessment
    evaporation: EvaporationAssessment
    algae: Optional[AlgaeAssessment] = Field(description="null until the camera has sent a usable frame.")
    event: Optional[EventOutcome] = None


class AllAssessments(_Open):
    chemistry: Optional[WaterChemistryAssessment]
    evaporation: Optional[EvaporationAssessment]
    algae: Optional[AlgaeAssessment]
    hypoxia: HypoxiaFlag


# ---------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------
class DailyStats(_Open):
    avg: float
    min: Optional[float] = None
    max: Optional[float] = None
    sample_days: int


class ForecastDay(_Open):
    days_from_now: int
    tan_ppm: float
    no2_ppm: float
    no3_ppm: float
    risk_score: int
    nitrite_override: Optional[bool] = None
    temp_c_assumed: Optional[float] = None
    lux_assumed: Optional[float] = None


class WaterChemistryForecast(_Open):
    trajectory: list[ForecastDay]
    first_watch_days_from_now: Optional[int] = None
    first_high_risk_days_from_now: Optional[int] = None
    first_nitrite_days_from_now: Optional[int] = None
    predicted_action_days_from_now: Optional[int] = None
    caveat: Optional[str] = None
    avg_daily_tan_mg: float
    temp_baseline: DailyStats
    lux_baseline: Optional[DailyStats]


class EvaporationDay(_Open):
    days_from_now: int
    evaporation_mm: float
    rain_offset_mm: Optional[float] = None
    net_loss_litres: Optional[float] = None
    cumulative_loss_litres: float
    cumulative_loss_pct: float
    water_temp_c_assumed: Optional[float] = None
    feed_cap_grams: Optional[float] = None
    feed_note: Optional[str] = None


class TdsCrossCheck(_Open):
    verdict: Literal["corroborated", "under_predicted", "over_predicted", "insufficient_data"]
    confidence: Optional[str] = None
    observed_tds_slope_ppm_per_day: Optional[float] = None
    predicted_tds_slope_ppm_per_day: Optional[float] = None


class EvaporationForecast(_Open):
    trajectory: list[EvaporationDay]
    predicted_topup_days_from_now: Optional[int] = None
    first_watch_days_from_now: Optional[int] = None
    first_action_days_from_now: Optional[int] = None
    days_using_real_forecast: Optional[int] = None
    assumed_depth_m: Optional[float] = None
    surface_area_m2: Optional[float] = None
    volume_litres: float
    avg_evaporation_mm_per_day: Optional[float] = None
    avg_loss_litres_per_day: float
    starting_loss_pct: Optional[float] = None
    last_topup_at: Optional[str] = None
    tds_cross_check: TdsCrossCheck


class AlgaeDay(_Open):
    days_from_now: int
    green_ratio: float
    growth_rate_per_day: Optional[float] = None
    favourability: Optional[float] = None
    lux_assumed: Optional[float] = None
    temp_c_assumed: Optional[float] = None
    no3_ppm_assumed: Optional[float] = None


class AlgaeThresholds(_Open):
    watch: float
    action: float
    baseline: Optional[float] = None
    mode: str


class ScrubBenefit(_Open):
    days_bought: Optional[int] = None
    post_scrub_green_ratio: Optional[float] = None


class CameraDriftVerdict(_Open):
    verdict: Literal["stable", "drift_possible", "drift_suspected", "insufficient_data"]
    detail: Optional[str] = None
    samples: Optional[int] = None


class AlgaeForecast(_Open):
    trajectory: list[AlgaeDay]
    current_green_ratio: Optional[float] = None
    predicted_scrub_days_from_now: Optional[int] = None
    first_watch_days_from_now: Optional[int] = None
    first_action_days_from_now: Optional[int] = None
    thresholds: AlgaeThresholds
    rate_source: Literal["fitted_from_camera", "measured_declining", "literature_fallback"]
    intrinsic_rate_per_day: Optional[float] = None
    realised_rate_per_day: Optional[float] = None
    confidence: Optional[str] = None
    sample_count: Optional[int] = None
    obstructed_sample_count: Optional[int] = None
    days_using_real_forecast: Optional[int] = None
    last_scrub_at: Optional[str] = None
    scrub_benefit: Optional[ScrubBenefit] = None
    latest_image_url: Optional[str] = None
    assimilated_camera_frames: Optional[int] = None


# ---------------------------------------------------------------------
# Algae ratings
# ---------------------------------------------------------------------
class AlgaeRating(_Open):
    id: int
    image_id: Optional[int] = None
    image_url: Optional[str] = None
    severity: Optional[str] = Field(default=None, description="null for an obstruction report.")
    is_obstructed: Optional[bool] = None
    green_ratio_at_rating: Optional[float] = None
    rated_at: Optional[str] = None
    image_captured_at: Optional[str] = None
    notes: Optional[str] = None


class ImageRow(_Open):
    id: int
    created_at: str
    green_ratio: Optional[float] = None
    imageURL: Optional[str] = None


class AlgaeCalibration(_Open):
    label_counts: dict[str, int]
    labels_needed: dict[str, int]
    thresholds: AlgaeThresholds
    class_targets: dict[str, float]
    camera_drift: CameraDriftVerdict
    green_ratio: Optional[float] = None


class AlgaeRatingContext(_Open):
    ratings: list[AlgaeRating]
    latest_rating: Optional[AlgaeRating]
    calibration: AlgaeCalibration
    latest_image: Optional[ImageRow]
    severity_levels: list[str]


class AlgaeRatingResult(_Open):
    rating_id: Optional[int]
    rated_image_id: Optional[int]
    image_inferred: bool
    green_ratio_before: Optional[float] = None
    green_ratio_after: Optional[float] = None
    removed_frames: Optional[int] = None
    label_counts: Optional[dict[str, int]] = None
    warning: Optional[str] = Field(default=None, description="Only when the rating could not be stored.")
    assessments: EventAssessments


class AlgaeRatingUndoResult(_Open):
    undone: bool
    green_ratio: Optional[float] = None
    thresholds: Optional[AlgaeThresholds] = None
    label_counts: Optional[dict[str, int]] = None
    assessments: EventAssessments


# ---------------------------------------------------------------------
# Pond profile and camera mask
# ---------------------------------------------------------------------
class PondProfileValues(_Open):
    effective_from: Optional[str] = None
    volume_l: float
    depth_m: Optional[float] = None
    biomass_g: float
    fish_type: Optional[str] = None
    fish_count: Optional[int] = None
    tap_tds_ppm: Optional[float] = None
    tap_nitrate_ppm: Optional[float] = None
    aeration: Optional[bool] = None


class PondProfileRow(PondProfileValues):
    id: int
    pond_id: int
    source: Optional[str] = None
    created_at: Optional[str] = None


class PondProfileResponse(_Closed):
    pond_id: int
    source: Literal["pond_profile", "userdata"]
    current: PondProfileValues
    history: list[PondProfileRow]
    added: Optional[PondProfileRow] = Field(default=None, description="The row a PUT stored.")


class CameraMaskVersion(_Open):
    pond_id: int
    mask_version: int
    mask: list[list[float]]
    created_at: Optional[str] = None


class CameraMaskResponse(_Closed):
    pond_id: int
    mask: Optional[list[list[float]]] = Field(description="null: the whole frame is analysed.")
    mask_version: Optional[int]
    updated_at: Optional[str]
    versions: list[CameraMaskVersion]


# ---------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------
class Health(_Open):
    status: Literal["ok"]
    domains: list[str]


class Ready(_Open):
    status: Literal["ready"]
    storage: dict[str, Any]
    poller: dict[str, Any]
    reasons: list[str]


# ---------------------------------------------------------------------
# GET /v1/ponds/{pond}/dashboard (koi/api/dashboard.py)
# ---------------------------------------------------------------------
Freshness = Literal["ok", "stale", "missing", "invalid"]


class SensorReading(_Closed):
    """The newest SensorData row of one channel. Every channel carries its
    own times; nothing is copied from another channel."""

    channel: Literal["ph", "tds", "water_temp", "lux"]
    status: Freshness = Field(description="ok: fresh; stale: older than fresh_for_seconds; missing: no row; "
                                          "invalid: the stored value is not a usable measurement.")
    value: Optional[float] = Field(description="null unless status is ok or stale.")
    unit: str = Field(description="pH, ppm, degC or lux.")
    sensor_type: Optional[str] = Field(description="SensorData.sensor_type of the row (pH, TDS, temp, LUX).")
    source_row_id: Optional[int] = Field(description="SensorData.id of the row.")
    ingested_at: Optional[datetime] = Field(
        description="SensorData.created_at: when the database stored the row, not when the probe sampled.")
    sample_time: Optional[datetime] = Field(description="When the probe sampled. Always null until the node "
                                                        "sends a sample time (issue #17).")
    sample_time_basis: Literal["unknown"] = Field(description="unknown: the node sends no sample time yet.")
    fresh_until: Optional[datetime] = Field(description="ingested_at plus fresh_for_seconds; status turns stale "
                                                        "after it.")
    note: Optional[str] = None


class DashboardReadings(_Closed):
    kind: Literal["observed"] = "observed"
    fresh_for_seconds: int = Field(description="How long after ingested_at a reading counts as fresh.")
    ph: SensorReading
    tds: SensorReading
    water_temp: SensorReading
    lux: SensorReading


class DashboardAssessments(_Closed):
    kind: Literal["model_estimate"] = "model_estimate"
    chemistry: Optional[WaterChemistryAssessment] = Field(description="Latest stored evaluation; null before "
                                                                       "the first poll or event.")
    evaporation: Optional[EvaporationAssessment]
    algae: Optional[AlgaeAssessment]
    hypoxia: HypoxiaFlag = Field(description="From fresh lux and water temperature readings only; unknown "
                                             "when either is not fresh.")


class StationAssignment(_Closed):
    """UserData.ClosestStations; null where the pond has no assignment."""

    air_temperature: Optional[str]
    rainfall: Optional[str]
    wind_speed: Optional[str]
    two_hour_area: Optional[str]
    twenty_four_hour_region: Optional[str]


class WeatherValue(_Closed):
    """One NEA station reading from the latest cache (weather_telemetry),
    from the pond's assigned station for that metric only."""

    metric: Literal["air_temperature", "rainfall", "wind_speed"]
    status: Literal["ok", "stale", "missing"]
    value: Optional[float]
    unit: str = Field(description="degC; mm over the 5 minutes ending at observed_at; knot.")
    station_id: Optional[str]
    observed_at: Optional[datetime] = Field(
        description="Provider time of the reading (weather_telemetry.source_times); null for a cache row "
                    "written before migration 0009.")
    ingested_at: Optional[datetime] = Field(description="When the cache row was last written (updated_at).")
    fresh_until: Optional[datetime] = Field(
        description="observed_at (or ingested_at when observed_at is null) plus one hour.")
    note: Optional[str] = None


class UvIndex(_Closed):
    """NEA's hourly UV index for the current Singapore hour."""

    status: Literal["ok", "missing", "night_derived"] = Field(
        description="ok: NEA reported this hour; missing: daylight hour with no report; night_derived: no "
                    "report at night, value 0 by convention, not a measurement.")
    value: Optional[float]
    unit: Literal["UV index"] = "UV index"
    hour_start: datetime = Field(description="Start of the current Singapore hour.")
    daylight: bool = Field(description="07:00 to 19:00 Singapore time (a fixed convention, not sunrise).")
    ingested_at: Optional[datetime]
    note: Optional[str] = None


class WeatherNow(_Closed):
    kind: Literal["observed"] = "observed"
    air_temperature: WeatherValue
    rainfall: WeatherValue
    wind_speed: WeatherValue
    uv_index: UvIndex


class ForecastPeriod(_Closed):
    """A forecast from the latest cache (weather_forecasts)."""

    status: Literal["ok", "expired", "missing"] = Field(
        description="expired: valid_to has passed; missing: no cached forecast for the assignment.")
    slot_id: Optional[str]
    text: Optional[str]
    code: Optional[str]
    valid_from: Optional[datetime]
    valid_to: Optional[datetime]
    issued_at: Optional[datetime] = Field(description="Provider issue time (source_issued_at); null for a row "
                                                      "written before migration 0009.")
    ingested_at: Optional[datetime] = Field(description="When the cache row was written (updated_at); not the "
                                                        "issue time.")


class GeneralForecast(ForecastPeriod):
    temperature_low_c: Optional[float]
    temperature_high_c: Optional[float]
    humidity_low_pct: Optional[float]
    humidity_high_pct: Optional[float]
    wind_low_kmh: Optional[float]
    wind_high_kmh: Optional[float]


class OutlookDay(_Closed):
    """One day of NEA's 4-day outlook, from today (Singapore) onward."""

    date: date
    weekday: Optional[str]
    text: Optional[str]
    code: Optional[str]
    temperature_low_c: Optional[float]
    temperature_high_c: Optional[float]
    humidity_low_pct: Optional[float]
    humidity_high_pct: Optional[float]
    wind_low_kmh: Optional[float]
    wind_high_kmh: Optional[float]
    issued_at: Optional[datetime]
    ingested_at: Optional[datetime]


class DashboardForecast(_Closed):
    kind: Literal["projection"] = "projection"
    two_hour: ForecastPeriod
    twenty_four_hour_regional: ForecastPeriod
    twenty_four_hour_general: GeneralForecast
    outlook: list[OutlookDay]


class Dashboard(_Closed):
    """Everything the dashboard screen shows, in one response."""

    pond_id: int
    api_version: Literal["v1"] = "v1"
    stations: StationAssignment
    readings: DashboardReadings
    assessments: DashboardAssessments
    next_actions: list[dict[str, Any]] = Field(description="Empty until the action ladder issue defines it.")
    weather: WeatherNow
    forecast: DashboardForecast


__all__ = [name for name, value in list(globals().items())
           if isinstance(value, type) and issubclass(value, BaseModel) and value.__module__ == __name__
           and not name.startswith("_")]
