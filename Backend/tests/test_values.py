"""The shared value coercions in koi.values."""
import math
from datetime import datetime, timedelta, timezone

import pytest

from koi.values import finite_number, parse_timestamp


def test_parse_timestamp_accepts_z_offset_naive_and_datetime():
    assert parse_timestamp("2026-10-01T02:00:00Z") == datetime(2026, 10, 1, 2, tzinfo=timezone.utc)
    assert parse_timestamp("2026-10-01T10:00:00+08:00") == datetime(2026, 10, 1, 2, tzinfo=timezone.utc)
    naive = parse_timestamp("2026-10-01T02:00:00")
    assert naive.utcoffset() == timedelta(0)
    assert parse_timestamp(datetime(2026, 10, 1, 2)).tzinfo is timezone.utc


def test_parse_timestamp_rejects_garbage():
    with pytest.raises(ValueError):
        parse_timestamp("not a time")


@pytest.mark.parametrize("value", [None, True, "1.5", [], math.nan, math.inf, -math.inf])
def test_finite_number_rejects_non_numbers(value):
    assert finite_number(value) is None


def test_finite_number_accepts_int_and_float():
    assert finite_number(3) == 3.0 and isinstance(finite_number(3), float)
    assert finite_number(-2.5) == -2.5
