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

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(o.strip() for o in value.split(",") if o.strip())
        return value

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value

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
