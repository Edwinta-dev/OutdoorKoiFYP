"""Issue #32: both storage adapters store and list pond_calibration rows
with the same columns, pond filter and order."""
from datetime import datetime, timedelta, timezone

import pytest
from test_supabase_storage import _storage

from koi.storage import MemoryStorage, StorageError
from koi.storage.base import CALIBRATION_COLUMNS

AT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def row(parameter="evaporation_shelter_factor", at=AT, **values):
    return {"parameter": parameter, "status": "fitted", "value": 0.4, "lag_hours": None, "fitted_at": at.isoformat(),
            "effective_from": at.isoformat(), "sample_count": 3, "error": {"training_rmse": 1.0},
            "training_from": None, "training_to": None, "evaluation_from": None, "evaluation_to": None,
            "details": {}, "method_version": 1, **values}


def test_calibration_memory_insert_order_isolation_and_copy():
    storage = MemoryStorage()
    later = storage.insert_calibration(7, row(at=AT + timedelta(days=1)))
    first = storage.insert_calibration(7, row(id=99, pond_id=8, created_at="ignored"))
    storage.insert_calibration(8, row())
    assert first["pond_id"] == 7 and first["id"] != 99 and first["created_at"] != "ignored"
    rows = storage.fetch_calibrations(7)
    assert [r["id"] for r in rows] == [first["id"], later["id"]]
    assert set(rows[0]) == set(CALIBRATION_COLUMNS)
    rows[0]["value"] = 9.0
    assert storage.fetch_calibrations(7)[0]["value"] == 0.4


def test_calibration_supabase_insert_and_paged_list():
    stored = {"id": 1, "pond_id": 7, **row()}
    storage, client = _storage(data={"pond_calibration": [stored]})
    assert storage.insert_calibration(7, row(id=5, pond_id=8)) == stored
    assert client.queries[-1].calls == [("insert", ({**row(), "pond_id": 7},), {})]
    assert storage.fetch_calibrations(7) == [stored]
    assert client.queries[-1].calls == [
        ("select", ("*",), {}), ("eq", ("pond_id", 7), {}), ("order", ("effective_from",), {}),
        ("order", ("id",), {}), ("range", (0, 999), {})]


@pytest.mark.parametrize("operation", ["insert_calibration", "fetch_calibrations"])
def test_calibration_supabase_storage_errors(operation):
    storage, _ = _storage(error=ConnectionError("offline"))
    with pytest.raises(StorageError, match=operation):
        if operation == "insert_calibration":
            storage.insert_calibration(7, row())
        else:
            storage.fetch_calibrations(7)
