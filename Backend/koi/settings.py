"""Every environment value the backend reads, in one typed object.

Values come from environment variables, then from Backend/.env (gitignored;
copy Backend/.env.example). Environment variables win over .env. Nothing
else in the package reads os.environ.

Tests build Settings(_env_file=None, ...) so a developer's real .env is
never loaded during a test run.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from koi.models.hypoxia import HypoxiaThresholds
from koi.models.sensor_inputs import IngestConfig
from koi.models.tds_prompts import PromptConfig

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        frozen=True,
    )

    # development starts the poller inside the API process (python -m
    # koi.api); production and test never do.
    env: Literal["development", "production", "test"] = Field(
        default="production", validation_alias=AliasChoices("KOI_ENV", "env"))

    # Where the services keep their data: "supabase" (the live project) or
    # "memory" (in-process tables, empty at start and lost on exit).
    storage: Literal["supabase", "memory"] = Field(
        default="supabase", validation_alias=AliasChoices("KOI_STORAGE", "storage"))

    # Supabase project URL and the secret (service-role) key. Server-side
    # only; never put the key in the app or firmware.
    supabase_url: str = Field(default="", validation_alias=AliasChoices("SUPABASE_URL", "supabase_url"))
    supabase_servicerole_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("SUPABASE_SERVICEROLE_KEY", "supabase_servicerole_key"))

    # Local time zone for the camera's daylight capture schedule.
    timezone: str = Field(default="Asia/Singapore", validation_alias=AliasChoices("KOI_TIMEZONE", "timezone"))

    # Minutes between environmental polls of every active pond.
    poll_interval_minutes: int = Field(
        default=15, gt=0, validation_alias=AliasChoices("KOI_POLL_INTERVAL_MINUTES", "poll_interval_minutes"))

    # Ponds the worker polls at the same time (one thread each, still one
    # lock per pond).
    worker_threads: int = Field(
        default=4, gt=0, validation_alias=AliasChoices("KOI_WORKER_THREADS", "worker_threads"))

    # Sensor ingestion (koi/models/sensor_inputs.py, migration 0014).
    # Expected minutes between the sensor node's uploads; a channel's
    # newest reading counts as fresh for two of them.
    sensor_cadence_minutes: int = Field(
        default=15, gt=0, validation_alias=AliasChoices("KOI_SENSOR_CADENCE_MINUTES", "sensor_cadence_minutes"))
    # How far before the newest ingested insert time each scan starts, to
    # find a row committed after a newer one.
    sensor_ingest_overlap_minutes: int = Field(
        default=60, gt=0,
        validation_alias=AliasChoices("KOI_SENSOR_INGEST_OVERLAP_MINUTES", "sensor_ingest_overlap_minutes"))
    # Most SensorData rows one pond ingests per cycle; the rest wait for
    # the next cycle.
    sensor_ingest_batch_rows: int = Field(
        default=2000, gt=0,
        validation_alias=AliasChoices("KOI_SENSOR_INGEST_BATCH_ROWS", "sensor_ingest_batch_rows"))

    # TDS owner questions (issue #35). Off until the owner enables them.
    tds_prompts: bool = Field(default=False, validation_alias=AliasChoices("KOI_TDS_PROMPTS", "tds_prompts"))
    tds_prompt_min_step_ppm: float = Field(
        default=20.0, gt=0, allow_inf_nan=False,
        validation_alias=AliasChoices("KOI_TDS_PROMPT_MIN_STEP_PPM", "tds_prompt_min_step_ppm"))
    tds_prompt_confidence_threshold: float = Field(
        default=0.8, ge=0, le=1, allow_inf_nan=False,
        validation_alias=AliasChoices("KOI_TDS_PROMPT_CONFIDENCE_THRESHOLD", "tds_prompt_confidence_threshold"))
    tds_prompt_cooldown_hours: float = Field(
        default=24.0, ge=0, allow_inf_nan=False,
        validation_alias=AliasChoices("KOI_TDS_PROMPT_COOLDOWN_HOURS", "tds_prompt_cooldown_hours"))

    # Evaluation retention (koi/worker/retention.py, migration 0017): the
    # worker's daily job folds the detailed evaluation rows of every local
    # day older than this many days into evaluation_daily and deletes them.
    evaluation_retention_days: int = Field(
        default=30, gt=0,
        validation_alias=AliasChoices("KOI_EVALUATION_RETENTION_DAYS", "evaluation_retention_days"))

    # Origins allowed to call either Flask app from a browser. Comma
    # separated in the environment; "*" allows any origin.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = Field(
        default=("*",), validation_alias=AliasChoices("KOI_CORS_ORIGINS", "cors_origins"))

    # Camera service. The bucket must match POND_IMAGE_BUCKET in the app's
    # env/*.json. An empty device token leaves uploads unauthenticated.
    pond_image_bucket: str = Field(
        default="imageAnalysisBucket", validation_alias=AliasChoices("POND_IMAGE_BUCKET", "pond_image_bucket"))
    device_token: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("DEVICE_TOKEN", "device_token"))

    # Feature flag: short fixed camera sleep times for bench testing.
    test_mode: bool = Field(default=False, validation_alias=AliasChoices("TEST_MODE", "test_mode"))

    # Camera state machine (issue #38): consecutive frames with a green-ratio
    # rise above 0.05 to enter the dynamic schedule, consecutive stable
    # frames to leave it, and the rise levels (comma separated, ascending)
    # past which the dynamic interval steps from 2 h to 1 h to 30 min.
    camera_dynamic_enter_frames: int = Field(
        default=2, gt=0, validation_alias=AliasChoices("CAMERA_DYNAMIC_ENTER_FRAMES", "camera_dynamic_enter_frames"))
    camera_dynamic_exit_frames: int = Field(
        default=3, gt=0, validation_alias=AliasChoices("CAMERA_DYNAMIC_EXIT_FRAMES", "camera_dynamic_exit_frames"))
    camera_dynamic_rate_levels: Annotated[tuple[float, float], NoDecode] = Field(
        default=(0.10, 0.20),
        validation_alias=AliasChoices("CAMERA_DYNAMIC_RATE_LEVELS", "camera_dynamic_rate_levels"))
    # Obstruction off-ramp (issue #80): consecutive obstructed frames within
    # the tolerance of their mean that restart the baseline there (0 turns
    # the off-ramp off, so only a return near the old baseline clears).
    camera_obstruction_confirm_frames: int = Field(
        default=3, ge=0,
        validation_alias=AliasChoices("CAMERA_OBSTRUCTION_CONFIRM_FRAMES", "camera_obstruction_confirm_frames"))
    camera_obstruction_tolerance: float = Field(
        default=0.05, gt=0, lt=1,
        validation_alias=AliasChoices("CAMERA_OBSTRUCTION_TOLERANCE", "camera_obstruction_tolerance"))

    # Digital twin API authentication (issue #11). "required" verifies the
    # Supabase access token on every pond request; "disabled" trusts the
    # caller's user_id and is refused unless env is development.
    auth: Literal["required", "disabled"] = Field(
        default="required", validation_alias=AliasChoices("KOI_AUTH", "auth"))

    # How access tokens are verified. HS256 checks the signature with the
    # project's JWT secret; ES256 and RS256 with the public key from the
    # JWKS URL (the project's signing keys). The issuer defaults to
    # SUPABASE_URL + /auth/v1.
    jwt_algorithm: Literal["HS256", "ES256", "RS256"] = Field(
        default="HS256", validation_alias=AliasChoices("KOI_JWT_ALGORITHM", "jwt_algorithm"))
    supabase_jwt_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("SUPABASE_JWT_SECRET", "supabase_jwt_secret"))
    supabase_jwks_url: str = Field(
        default="", validation_alias=AliasChoices("SUPABASE_JWKS_URL", "supabase_jwks_url"))
    jwt_issuer: str = Field(default="", validation_alias=AliasChoices("KOI_JWT_ISSUER", "jwt_issuer"))
    jwt_audience: str = Field(
        default="authenticated", validation_alias=AliasChoices("KOI_JWT_AUDIENCE", "jwt_audience"))

    # NEA weather ingestion (python -m koi.weather, issue #24). The key is
    # optional (sent as x-api-key); without it the public rate limit
    # applies. History is kept for the listed stations and 2-hour forecast
    # areas plus those assigned to ponds; "*" keeps every one. The latest
    # caches are filled for every station and area either way.
    nea_api_base_url: str = Field(
        default="https://api-open.data.gov.sg/v2/real-time/api",
        validation_alias=AliasChoices("NEA_API_BASE_URL", "nea_api_base_url"))
    nea_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("NEA_API_KEY", "nea_api_key"))
    nea_min_request_interval_seconds: float = Field(
        default=1.0, ge=0,
        validation_alias=AliasChoices("NEA_MIN_REQUEST_INTERVAL_SECONDS", "nea_min_request_interval_seconds"))
    nea_max_attempts: int = Field(
        default=5, gt=0, validation_alias=AliasChoices("NEA_MAX_ATTEMPTS", "nea_max_attempts"))
    weather_history_stations: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(), validation_alias=AliasChoices("WEATHER_HISTORY_STATIONS", "weather_history_stations"))
    weather_history_areas: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(), validation_alias=AliasChoices("WEATHER_HISTORY_AREAS", "weather_history_areas"))

    # Night-time hypoxia flag (issue #28, koi/models/hypoxia.py): water
    # temperature in C at or above which a dark pond is flagged "watch",
    # and "high". The watch level must be below the high level.
    hypoxia_watch_temp_c: float = Field(
        default=30.0, validation_alias=AliasChoices("KOI_HYPOXIA_WATCH_TEMP_C", "hypoxia_watch_temp_c"))
    hypoxia_high_temp_c: float = Field(
        default=32.0, validation_alias=AliasChoices("KOI_HYPOXIA_HIGH_TEMP_C", "hypoxia_high_temp_c"))

    # Error tracking (issue #66, koi/error_tracking.py). Empty DSN: off.
    # The environment defaults to KOI_ENV and the release to koi@<package
    # version>; set SENTRY_RELEASE to name a deploy (a git commit, say).
    sentry_dsn: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("SENTRY_DSN", "sentry_dsn"))
    sentry_environment: str = Field(
        default="", validation_alias=AliasChoices("SENTRY_ENVIRONMENT", "sentry_environment"))
    sentry_release: str = Field(
        default="", validation_alias=AliasChoices("SENTRY_RELEASE", "sentry_release"))

    # Local development stack, local database profile (python -m koi.dev
    # --profile supabase, docs/dev.md): the local Supabase stack's API URL,
    # service-role key and database URL. Empty: taken from `supabase status
    # -o env`. Anything not on this machine is refused.
    dev_supabase_url: str = Field(
        default="", validation_alias=AliasChoices("KOI_DEV_SUPABASE_URL", "dev_supabase_url"))
    dev_supabase_service_role_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("KOI_DEV_SUPABASE_SERVICE_ROLE_KEY", "dev_supabase_service_role_key"))
    dev_db_url: str = Field(default="", validation_alias=AliasChoices("KOI_DEV_DB_URL", "dev_db_url"))

    # Lowest level written to the JSON log on stderr.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", validation_alias=AliasChoices("KOI_LOG_LEVEL", "log_level"))

    @field_validator("cors_origins", "weather_history_stations", "weather_history_areas", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(o.strip() for o in value.split(",") if o.strip())
        return value

    @field_validator("camera_dynamic_rate_levels", mode="before")
    @classmethod
    def _split_levels(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(v.strip() for v in value.split(",") if v.strip())
        return value

    @field_validator("camera_dynamic_rate_levels")
    @classmethod
    def _ascending_levels(cls, value: tuple[float, float]) -> tuple[float, float]:
        if not 0 < value[0] < value[1]:
            raise ValueError("camera_dynamic_rate_levels must be two ascending positive numbers")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _hypoxia_levels_ascending(self) -> "Settings":
        if not self.hypoxia_watch_temp_c < self.hypoxia_high_temp_c:
            raise ValueError("KOI_HYPOXIA_WATCH_TEMP_C must be below KOI_HYPOXIA_HIGH_TEMP_C")
        return self

    @model_validator(mode="after")
    def _auth_bypass_only_in_development(self) -> "Settings":
        if self.auth == "disabled" and self.env != "development":
            raise ValueError(f"KOI_AUTH=disabled is only allowed with KOI_ENV=development, not {self.env!r}")
        return self

    @property
    def resolved_jwt_issuer(self) -> str:
        """The issuer access tokens must carry: KOI_JWT_ISSUER, else the
        Supabase project's auth URL, else empty (refused at API start)."""
        if self.jwt_issuer:
            return self.jwt_issuer
        return f"{self.supabase_url.rstrip('/')}/auth/v1" if self.supabase_url else ""

    @property
    def hypoxia_thresholds(self) -> HypoxiaThresholds:
        return HypoxiaThresholds(self.hypoxia_watch_temp_c, self.hypoxia_high_temp_c)

    @property
    def tds_prompt_config(self) -> PromptConfig:
        return PromptConfig(self.tds_prompts, self.tds_prompt_min_step_ppm,
                            self.tds_prompt_confidence_threshold, self.tds_prompt_cooldown_hours)

    @property
    def sensor_ingest(self) -> IngestConfig:
        return IngestConfig(self.sensor_cadence_minutes, self.sensor_ingest_overlap_minutes,
                            self.sensor_ingest_batch_rows)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def poller_in_api_process(self) -> bool:
        """Only development keeps the old app.py behaviour of running the
        poller inside the API process."""
        return self.env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read once from the environment and .env."""
    return Settings()
