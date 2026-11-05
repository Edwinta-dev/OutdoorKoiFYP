"""The forward environments and projection helper shared by API and poller."""
import logging

from koi.models.forecast_env import (
    CACHED_HORIZON_DAYS,
    algae_environment,
    days_from_projection,
    evaporation_environment,
    first_not_none,
)


def test_first_not_none_keeps_zero():
    assert first_not_none(None, 0, 5) == 0
    assert first_not_none(None, None) is None


def test_evaporation_environment_fills_missing_fields_from_live_values():
    env = evaporation_environment([{"air_temp_c": 33.0}, {}], 30.5, 70.0, 1.5)
    assert [(d.air_temp_c, d.relative_humidity_pct, d.wind_speed_ms) for d in env] == [
        (33.0, 70.0, 1.5), (30.5, 70.0, 1.5)]


def test_evaporation_environment_without_forecast_days_is_one_live_day_with_defaults():
    env = evaporation_environment([], None, None, None)
    assert len(env) == 1
    assert (env[0].air_temp_c, env[0].relative_humidity_pct, env[0].wind_speed_ms) == (30.0, 80.0, 2.0)


def test_algae_environment_scales_lux_and_indexes_nitrate():
    env = algae_environment([{"lux_multiplier": 0.5}, {"air_temp_c": 31.0}], 1000.0, 29.0, [4.0], 9.0)
    assert [(d.lux, d.temp_c, d.no3_ppm) for d in env] == [(500.0, 29.0, 4.0), (850.0, 31.0, 9.0)]
    assert len(algae_environment([], 1000.0, 29.0, [], 9.0)) == 1


def test_days_from_projection_returns_the_key_or_none_and_logs(caplog):
    assert CACHED_HORIZON_DAYS == 21
    assert days_from_projection(lambda: {"k": 3}, "k", "failed") == 3
    assert days_from_projection(lambda: {}, "k", "failed") is None

    def boom():
        raise RuntimeError("no history")

    with caplog.at_level(logging.WARNING):
        assert days_from_projection(boom, "k", "projection_failed", pond_id=7) is None
    assert any("projection_failed" in r.getMessage() or getattr(r, "event", "") == "projection_failed"
               for r in caplog.records)
