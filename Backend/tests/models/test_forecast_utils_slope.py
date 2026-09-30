"""forecast_utils.slope_per_day: the TDS trend behind the evaporation
cross-check (moved from the old state_store module)."""
import pytest

from koi.models.forecast_utils import slope_per_day


def _rows(values, dates):
    return [{"avg_value": v, "record_date": d} for v, d in zip(values, dates, strict=True)]


def test_slope_uses_real_elapsed_days_across_gaps():
    rows = _rows([220.0, 221.4, 224.2], ["2026-08-01", "2026-08-03", "2026-08-07"])
    assert slope_per_day(rows) == pytest.approx(0.7)


def test_slope_needs_three_points_over_at_least_a_day():
    assert slope_per_day(_rows([1.0, 2.0], ["2026-08-01", "2026-08-02"])) is None
    same_day = ["2026-08-01T01:00:00Z", "2026-08-01T05:00:00Z", "2026-08-01T09:00:00Z"]
    assert slope_per_day(_rows([1.0, 2.0, 3.0], same_day)) is None


def test_slope_skips_rows_without_a_value_or_a_readable_date():
    rows = _rows([220.0, None, 220.7, 221.4, 999.0], ["2026-08-01", "2026-08-02", "2026-08-02", "2026-08-03", "junk"])
    assert slope_per_day(rows) == pytest.approx(0.7)
