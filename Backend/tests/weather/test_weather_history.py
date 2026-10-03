"""As-of selection, rainfall totals with coverage and UV status over
weather history rows (koi/weather/history.py)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from koi.weather import history

UTC = timezone.utc
T0 = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)  # 00:00 on 2 Oct in Singapore


def _issuance(id_: int, issued: str | None, fetched: str, updated: str | None = None, **extra) -> dict:
    return {"id": id_, "product": "4day", "slot_id": "2026-10-04", "issued_at": issued, "source_updated_at": updated,
            "fetched_at": fetched, "valid_from": "2026-10-03T16:00:00+00:00", "valid_to": "2026-10-04T16:00:00+00:00",
            "payload": {"id": id_}, **extra}


ISSUANCES = [
    _issuance(1, "2026-10-01T21:30:00+00:00", "2026-10-01T21:35:00+00:00"),
    _issuance(2, "2026-10-02T09:30:00+00:00", "2026-10-02T09:31:00+00:00"),
    _issuance(3, None, "2026-10-02T12:00:00+00:00"),  # issue time not supplied
]


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_forecast_as_of_never_uses_one_issued_later():
    assert history.select_forecast_as_of(ISSUANCES, _at("2026-10-01T21:29:59+00:00")) is None
    assert history.select_forecast_as_of(ISSUANCES, _at("2026-10-02T09:00:00+00:00"))["id"] == 1
    assert history.select_forecast_as_of(ISSUANCES, _at("2026-10-02T09:30:00+00:00"))["id"] == 2


def test_unknown_issue_time_is_usable_only_from_its_fetch_time():
    assert history.select_forecast_as_of(ISSUANCES, _at("2026-10-02T11:59:00+00:00"))["id"] == 2
    found = history.select_forecast_as_of(ISSUANCES, _at("2026-10-02T12:00:00+00:00"))
    assert found["id"] == 3 and found["available_at"] == "2026-10-02T12:00:00+00:00"


def test_revision_time_delays_availability():
    revised = [_issuance(4, "2026-10-02T09:30:00+00:00", "2026-10-02T10:01:00+00:00",
                         updated="2026-10-02T10:00:00+00:00")]
    assert history.select_forecast_as_of(revised, _at("2026-10-02T09:45:00+00:00")) is None
    assert history.select_forecast_as_of(revised, _at("2026-10-02T10:00:00+00:00"))["id"] == 4


def test_ties_are_broken_deterministically():
    same = [_issuance(5, "2026-10-02T09:30:00+00:00", "2026-10-02T09:31:00+00:00"),
            _issuance(6, "2026-10-02T09:30:00+00:00", "2026-10-02T09:32:00+00:00"),
            _issuance(7, None, "2026-10-02T09:30:00+00:00")]
    for order in (same, list(reversed(same))):
        assert history.select_forecast_as_of(order, _at("2026-10-02T10:00:00+00:00"))["id"] == 6


def test_valid_at_filters_by_validity_window():
    as_of = _at("2026-10-03T00:00:00+00:00")
    assert history.select_forecast_as_of(ISSUANCES, as_of, valid_at=_at("2026-10-04T00:00:00+00:00"))["id"] == 3
    assert history.select_forecast_as_of(ISSUANCES, as_of, valid_at=_at("2026-10-05T00:00:00+00:00")) is None


def _rain(start: datetime, minutes: int, value: float, station: str = "S43", **extra) -> dict:
    row = {"series": "station", "station_id": station, "metric": "rainfall", "semantics": "interval_total",
           "unit": "mm", "value": value, "observed_from": start.isoformat(),
           "observed_to": (start + timedelta(minutes=minutes)).isoformat(), "id": 0}
    return {**row, **extra}


def test_rainfall_sums_disjoint_intervals_over_a_full_day():
    rows = [_rain(T0 + timedelta(minutes=5 * i), 5, 0.1) for i in range(288)]
    start, end = history.local_day_window(date(2026, 10, 2))
    result = history.rainfall_total(rows, "S43", start, end)
    assert result["total_mm"] == pytest.approx(28.8)
    assert (result["coverage"], result["status"], result["missing"]) == (1.0, "complete", [])
    assert result["intervals"] == 288


def test_rainfall_reports_gaps_instead_of_zero():
    rows = [_rain(T0, 5, 1.0), _rain(T0 + timedelta(hours=12), 5, 2.0)]
    result = history.rainfall_total(rows, "S43", T0, T0 + timedelta(hours=24))
    assert result["total_mm"] == 3.0 and result["status"] == "partial"
    assert result["coverage"] == pytest.approx(600 / 86400, abs=1e-6)
    assert result["missing"][0] == {"from": "2026-10-01T16:05:00+00:00", "to": "2026-10-02T04:00:00+00:00"}
    assert result["missing"][-1]["to"] == "2026-10-02T16:00:00+00:00"


def test_rainfall_without_data_is_none_not_zero():
    result = history.rainfall_total([], "S43", T0, T0 + timedelta(hours=24))
    assert (result["total_mm"], result["status"], result["coverage"]) == (None, "no_data", 0.0)
    assert result["missing"] == [{"from": T0.isoformat(), "to": (T0 + timedelta(hours=24)).isoformat()}]


def test_rainfall_never_adds_overlaps_cumulatives_or_other_stations():
    rows = [
        _rain(T0, 5, 1.0),
        _rain(T0, 5, 1.0, id=1),                     # the same interval from another fetch/source
        _rain(T0, 60, 4.0),                          # an hourly total overlapping the 5-minute one
        _rain(T0, 0, 50.0, semantics="cumulative_total"),
        _rain(T0, 5, 0.5, unit="cm"),
        _rain(T0 + timedelta(minutes=5), 5, 0.5, station="S06"),
        _rain(T0 - timedelta(minutes=2), 5, 7.0),    # crosses the window start
    ]
    result = history.rainfall_total(rows, "S43", T0, T0 + timedelta(hours=1))
    assert result["total_mm"] == 1.0
    assert result["overlapping_skipped"] == 2
    assert result["intervals"] == 1


def test_rainfall_as_of_excludes_later_intervals():
    rows = [_rain(T0 + timedelta(minutes=5 * i), 5, 0.2) for i in range(12)]
    result = history.rainfall_total(rows, "S43", T0, T0 + timedelta(hours=1), as_of=T0 + timedelta(minutes=30))
    assert result["total_mm"] == pytest.approx(1.2)
    assert result["missing"] == [{"from": "2026-10-01T16:30:00+00:00", "to": "2026-10-01T17:00:00+00:00"}]


def test_rainfall_rejects_an_empty_window():
    with pytest.raises(ValueError):
        history.rainfall_total([], "S43", T0, T0)


def test_observations_as_of_respects_observed_time():
    rows = [{"series": "station", "station_id": "S43", "metric": "air_temperature", "value": v,
             "observed_from": t, "observed_to": t, "id": i}
            for i, (t, v) in enumerate([("2026-10-02T02:05:00+00:00", 30.7), ("2026-10-02T02:04:00+00:00", 30.6),
                                        ("2026-10-02T02:06:00+00:00", 30.8)])]
    found = history.observations_as_of(rows, "S43", "air_temperature", T0, T0 + timedelta(days=1),
                                       _at("2026-10-02T02:05:00+00:00"))
    assert [r["value"] for r in found] == [30.6, 30.7]


def test_local_day_window_is_singapore_midnight_to_midnight():
    start, end = history.local_day_window(date(2026, 10, 2))
    assert (start.astimezone(UTC), end.astimezone(UTC)) == (T0, T0 + timedelta(days=1))


def _uv(hour_utc: int, value: float) -> dict:
    end = datetime(2026, 10, 2, hour_utc, tzinfo=UTC)
    return {"metric": "uv_index", "value": value, "observed_from": (end - timedelta(hours=1)).isoformat(),
            "observed_to": end.isoformat(), "fetched_at": end.isoformat()}


def test_uv_status_measured_missing_and_night():
    rows = [_uv(1, 3), _uv(2, 5)]  # hours ending 09:00 and 10:00 Singapore
    assert history.uv_reading_status(rows, datetime(2026, 10, 2, 2, 30, tzinfo=UTC))["value"] == 5
    missing = history.uv_reading_status(rows, datetime(2026, 10, 2, 6, 0, tzinfo=UTC))  # 14:00, no reading
    assert (missing["value"], missing["status"]) == (None, "missing")
    night = history.uv_reading_status(rows, datetime(2026, 10, 2, 14, 0, tzinfo=UTC))  # 22:00
    assert (night["value"], night["status"]) == (None, "night")
