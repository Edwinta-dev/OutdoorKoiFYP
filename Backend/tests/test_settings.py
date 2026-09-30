"""koi.settings: every environment value in one typed object."""
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from koi.settings import Settings

ENV_NAMES = ["KOI_ENV", "SUPABASE_URL", "SUPABASE_SERVICEROLE_KEY", "KOI_TIMEZONE",
             "KOI_POLL_INTERVAL_MINUTES", "KOI_CORS_ORIGINS", "POND_IMAGE_BUCKET",
             "DEVICE_TOKEN", "TEST_MODE"]


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_match_the_old_hard_coded_values(clean_env):
    s = Settings(_env_file=None)
    assert s.env == "production"
    assert s.poll_interval_minutes == 15          # poller.POLL_INTERVAL_MINUTES
    assert s.timezone == "Asia/Singapore"         # imageSchedule.TZ
    assert s.tz == ZoneInfo("Asia/Singapore")
    assert s.pond_image_bucket == "imageAnalysisBucket"
    assert s.cors_origins == ("*",)               # CORS(app) allowed any origin
    assert s.device_token.get_secret_value() == ""
    assert s.test_mode is False
    assert s.poller_in_api_process is False


def test_reads_the_existing_environment_variable_names(clean_env):
    clean_env.setenv("KOI_ENV", "development")
    clean_env.setenv("SUPABASE_URL", "https://example.invalid")
    clean_env.setenv("SUPABASE_SERVICEROLE_KEY", "k")
    clean_env.setenv("KOI_TIMEZONE", "UTC")
    clean_env.setenv("KOI_POLL_INTERVAL_MINUTES", "5")
    clean_env.setenv("KOI_CORS_ORIGINS", "https://a.example, https://b.example")
    clean_env.setenv("POND_IMAGE_BUCKET", "frames")
    clean_env.setenv("DEVICE_TOKEN", "t")
    clean_env.setenv("TEST_MODE", "1")
    s = Settings(_env_file=None)
    assert s.env == "development"
    assert s.poller_in_api_process is True
    assert s.supabase_url == "https://example.invalid"
    assert s.supabase_servicerole_key.get_secret_value() == "k"
    assert s.tz == ZoneInfo("UTC")
    assert s.poll_interval_minutes == 5
    assert s.cors_origins == ("https://a.example", "https://b.example")
    assert s.pond_image_bucket == "frames"
    assert s.device_token.get_secret_value() == "t"
    assert s.test_mode is True


def test_env_file_is_read_and_environment_wins(clean_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("POND_IMAGE_BUCKET=from_file\nKOI_POLL_INTERVAL_MINUTES=7\n", encoding="utf-8")
    clean_env.setenv("KOI_POLL_INTERVAL_MINUTES", "9")
    s = Settings(_env_file=env_file)
    assert s.pond_image_bucket == "from_file"
    assert s.poll_interval_minutes == 9


def test_secrets_are_not_printed(clean_env):
    s = Settings(_env_file=None, supabase_servicerole_key="very-secret", device_token="xyzzy-42")
    assert "very-secret" not in repr(s)
    assert "xyzzy-42" not in repr(s)


@pytest.mark.parametrize("field, value", [
    ("env", "staging"),
    ("poll_interval_minutes", 0),
    ("timezone", "Not/AZone"),
])
def test_rejects_invalid_values(clean_env, field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
