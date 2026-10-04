"""Night-time hypoxia risk flag (issue #28): the rule in
koi/models/hypoxia.py, its settings, the poller computing it each cycle
and /assessment/all carrying it.

Run from Backend/: python -m pytest -q -k hypoxia
"""
import itertools

import pytest
from pydantic import ValidationError

from conftest import ALGAE_ASSESSMENT, DASHBOARD_PAYLOAD, USER, add_upload, api_client, make_settings, make_storage
from koi.api import create_app
from koi.models.engine import NIGHT_LUX_THRESHOLD
from koi.models.hypoxia import (
    ADVICE,
    HypoxiaThresholds,
    algae_is_high,
    assess_hypoxia,
)
from koi.worker import poller

NIGHT = 5.0
DAY = 22755.0


def flag(lux=NIGHT, temp=30.5, aeration=True, algae_high=False, thresholds=None):
    return assess_hypoxia(lux=lux, water_temp_c=temp, aeration=aeration, algae_high=algae_high,
                          thresholds=thresholds)


# --- the rule -----------------------------------------------------------------

def test_hypoxia_night_threshold_is_the_chemistry_engines():
    assert NIGHT_LUX_THRESHOLD == 20.0
    assert flag(lux=19.9).dark is True and flag(lux=19.9).level == "watch"
    assert flag(lux=20.0).dark is False and flag(lux=20.0).level == "none"


def test_hypoxia_default_levels_are_30_and_32_c():
    t = HypoxiaThresholds()
    assert (t.watch_temp_c, t.high_temp_c) == (30.0, 32.0)
    assert flag(temp=29.9).level == "none"
    assert flag(temp=30.0).level == "watch"
    assert flag(temp=31.9).level == "watch"
    assert flag(temp=32.0).level == "high"


# Every combination of light, temperature band, aeration (off, on,
# not recorded) and algae (high, not high, no assessment). Level rule:
# daylight or water below 30 C is "none"; otherwise 30 C gives watch,
# 32 C high, and aeration off and high algae each raise one level,
# capped at high.
COMBINATIONS = list(itertools.product(
    [("day", DAY), ("night", NIGHT)],
    [("cool", 28.0), ("watch", 30.5), ("high", 33.0)],
    [False, True, None],
    [True, False, None],
))


def _expected(light, band, aeration, algae_high):
    if light == "day" or band == "cool":
        return "none", []
    raised = (["no_aeration"] if aeration is False else []) + (["high_algae"] if algae_high else [])
    base = 1 if band == "watch" else 2
    return ("none", "watch", "high")[min(base + len(raised), 2)], raised


@pytest.mark.parametrize("light,band,aeration,algae_high", COMBINATIONS,
                         ids=lambda v: v[0] if isinstance(v, tuple) else str(v))
def test_hypoxia_every_combination(light, band, aeration, algae_high):
    result = flag(lux=light[1], temp=band[1], aeration=aeration, algae_high=algae_high)
    level, raised = _expected(light[0], band[0], aeration, algae_high)
    assert result.level == level
    assert result.raised_by == raised
    assert result.dark is (light[0] == "night")
    assert result.flagged is (level != "none")
    assert result.advice == (ADVICE if result.flagged else [])
    assert result.explanation


def test_hypoxia_watch_is_raised_once_per_factor_and_capped_at_high():
    assert flag(temp=30.5, aeration=True, algae_high=False).level == "watch"
    assert flag(temp=30.5, aeration=False, algae_high=False).level == "high"
    assert flag(temp=30.5, aeration=True, algae_high=True).level == "high"
    assert flag(temp=30.5, aeration=False, algae_high=True).level == "high"
    assert flag(temp=33.0, aeration=False, algae_high=True).level == "high"


def test_hypoxia_unknown_without_a_reading():
    for lux, temp in [(None, 31.0), (NIGHT, None), (None, None)]:
        result = flag(lux=lux, temp=temp)
        assert result.level == "unknown" and not result.flagged and result.advice == []
        assert "cannot be checked" in result.explanation


def test_hypoxia_explanation_is_plain_and_states_values():
    result = flag(lux=4.0, temp=32.4, aeration=False, algae_high=True)
    text = result.explanation
    assert "4 lux" in text and "32.4 °C" in text and "32.0 °C high level" in text
    assert "records no aeration" in text and "scrub level" in text
    assert "not measured" in text and "model estimate" in text
    assert any("aeration" in a.lower() for a in result.advice)
    assert any("evening feed" in a for a in result.advice)
    assert "not recorded" in flag(aeration=None).explanation
    assert "not recorded" not in flag(aeration=True).explanation
    assert "28.0 °C, below the 30.0 °C watch level" in flag(temp=28.0).explanation
    assert "22755 lux" in flag(lux=DAY).explanation


def test_hypoxia_configurable_levels():
    t = HypoxiaThresholds(watch_temp_c=28.0, high_temp_c=29.0)
    assert flag(temp=28.5, thresholds=t).level == "watch"
    assert flag(temp=29.0, thresholds=t).level == "high"
    assert flag(temp=27.9, thresholds=t).level == "none"
    with pytest.raises(ValueError):
        HypoxiaThresholds(watch_temp_c=32.0, high_temp_c=32.0)


def test_hypoxia_algae_high_reads_scrub_now_or_red_status():
    assert algae_is_high(None) is None
    assert algae_is_high({**ALGAE_ASSESSMENT, "scrub_now": True}) is True
    assert algae_is_high(ALGAE_ASSESSMENT) is False
    assert algae_is_high({"status": "Red"}) is True
    assert algae_is_high({"status": "Amber"}) is False


def test_hypoxia_flag_serialises():
    body = flag(aeration=False).to_dict()
    assert body["level"] == "high" and body["flagged"] is True
    assert body["watch_temp_c"] == 30.0 and body["high_temp_c"] == 32.0
    assert body["night_lux_threshold"] == NIGHT_LUX_THRESHOLD
    assert body["raised_by"] == ["no_aeration"] and body["advice"] == ADVICE


# --- settings -----------------------------------------------------------------

def test_hypoxia_settings_defaults_and_environment(monkeypatch):
    assert make_settings().hypoxia_thresholds == HypoxiaThresholds(30.0, 32.0)
    monkeypatch.setenv("KOI_HYPOXIA_WATCH_TEMP_C", "29")
    monkeypatch.setenv("KOI_HYPOXIA_HIGH_TEMP_C", "31.5")
    assert make_settings().hypoxia_thresholds == HypoxiaThresholds(29.0, 31.5)


def test_hypoxia_settings_reject_watch_not_below_high():
    with pytest.raises(ValidationError):
        make_settings(hypoxia_watch_temp_c=32.0, hypoxia_high_temp_c=31.0)


# --- poller and dashboard -----------------------------------------------------

def _app(raw_sensor, **settings):
    """The API over the fixture pond, with raw_sensor as both the payload's
    newest values (read by /assessment/all) and a node upload stored now
    (read by the poller)."""
    storage = make_storage()
    storage.set_dashboard_payload(USER, {**DASHBOARD_PAYLOAD, "raw_sensor": raw_sensor})
    add_upload(storage, raw_sensor)
    app = create_app(make_settings(**settings), storage=storage)
    app.config["TESTING"] = True
    return app, storage


def _night_reading(temp):
    return {**DASHBOARD_PAYLOAD["raw_sensor"], "LUX": 3, "temp": temp}


def test_hypoxia_computed_each_poll(caplog):
    app, storage = _app(_night_reading(31.0))
    registry = app.extensions["koi_registry"]
    with caplog.at_level("INFO"):
        poller._poll_user(registry, USER, storage.fetch_active_pond_configs()[0])
    [record] = [r for r in caplog.records if getattr(r, "koi_event", None) == "pond_polled"]
    assert record.koi_fields["hypoxia"] == "watch"
    assert record.koi_fields["hypoxia_raised_by"] == []

    storage.set_dashboard_payload(USER, {**DASHBOARD_PAYLOAD, "raw_sensor": _night_reading(27.0)})
    add_upload(storage, _night_reading(27.0))
    caplog.clear()
    with caplog.at_level("INFO"):
        poller._poll_user(registry, USER, storage.fetch_active_pond_configs()[0])
    [record] = [r for r in caplog.records if getattr(r, "koi_event", None) == "pond_polled"]
    assert record.koi_fields["hypoxia"] == "none"


def test_hypoxia_poll_uses_profile_aeration_and_configured_levels(caplog):
    app, storage = _app(_night_reading(29.0), hypoxia_watch_temp_c=28.0, hypoxia_high_temp_c=34.0)
    client = api_client(app)
    client.put(f"/v1/ponds/{USER}/profile", json={"volume_l": 4000.0, "biomass_g": 12000.0, "aeration": False})
    with caplog.at_level("INFO"):
        poller._poll_user(app.extensions["koi_registry"], USER, storage.fetch_active_pond_configs()[0])
    [record] = [r for r in caplog.records if getattr(r, "koi_event", None) == "pond_polled"]
    assert record.koi_fields["hypoxia"] == "high"
    assert record.koi_fields["hypoxia_raised_by"] == ["no_aeration"]


def test_hypoxia_in_the_dashboard_response():
    app, storage = _app(_night_reading(32.5))
    client = api_client(app)
    poller._poll_user(app.extensions["koi_registry"], USER, storage.fetch_active_pond_configs()[0])
    body = client.get(f"/assessment/all/{USER}").get_json()
    assert set(body) == {"chemistry", "evaporation", "algae", "hypoxia"}
    hypoxia = body["hypoxia"]
    assert hypoxia["level"] == "high" and hypoxia["flagged"] is True
    assert hypoxia["water_temp_c"] == 32.5 and hypoxia["lux"] == 3.0 and hypoxia["dark"] is True
    assert hypoxia["aeration"] is None and "not recorded" in hypoxia["explanation"]
    assert hypoxia["advice"] == ADVICE


def test_hypoxia_dashboard_reads_profile_and_cached_algae():
    app, storage = _app(_night_reading(30.5))
    client = api_client(app)
    client.put(f"/v1/ponds/{USER}/profile", json={"volume_l": 4000.0, "biomass_g": 12000.0, "aeration": True})
    storage.push_algae_evaluation(USER, {**ALGAE_ASSESSMENT, "status": "Red", "scrub_now": True})
    hypoxia = client.get(f"/assessment/all/{USER}").get_json()["hypoxia"]
    assert hypoxia["aeration"] is True and hypoxia["algae_high"] is True
    assert hypoxia["level"] == "high" and hypoxia["raised_by"] == ["high_algae"]


def test_hypoxia_dashboard_daytime_and_missing_reading():
    app, _ = _app(DASHBOARD_PAYLOAD["raw_sensor"])
    client = api_client(app)
    client.put(f"/v1/ponds/{USER}/profile", json={"volume_l": 4000.0, "biomass_g": 12000.0})
    hypoxia = client.get(f"/assessment/all/{USER}").get_json()["hypoxia"]
    assert hypoxia["level"] == "none" and hypoxia["dark"] is False and hypoxia["advice"] == []

    app, storage = _app({"pH": 7.6})
    client = api_client(app)
    client.put(f"/v1/ponds/{USER}/profile", json={"volume_l": 4000.0, "biomass_g": 12000.0})
    assert client.get(f"/assessment/all/{USER}").get_json()["hypoxia"]["level"] == "unknown"
    storage.failing.add("fetch_dashboard_payload")
    resp = client.get(f"/assessment/all/{USER}")
    assert resp.status_code == 200 and resp.get_json()["hypoxia"]["level"] == "unknown"
