"""Both storage adapters use the same kit columns, pond filters and ordering."""
from datetime import datetime, timezone

import pytest
from test_supabase_storage import _storage

from koi.storage import MemoryStorage, StorageError

AT = datetime(2026, 8, 20, tzinfo=timezone.utc)


def test_kit_memory_order_isolation_copy_and_evaluation_tie():
    storage = MemoryStorage()
    storage.add_rows("kit_readings", [{"pond": 7, "id": 20, "taken_at": AT.isoformat()},
                                      {"pond": 8, "id": 30}, {"pond": 7, "id": 10, "taken_at": AT.isoformat()},
                                      {"pond": 7, "id": 5}])
    rows = storage.fetch_kit_readings(7)
    assert [r["id"] for r in rows] == [5, 10, 20]
    assert rows[0]["estimates"] is None
    rows[0]["ph"] = 999
    assert storage.fetch_kit_readings(7)[0]["ph"] is None
    storage.add_rows("pond_chemistry_evaluations", [
        {"id": 10, "userid": 7, "evaluated_at": AT.isoformat(), "tan_ppm": 1},
        {"id": 11, "userid": 8, "evaluated_at": AT.isoformat(), "tan_ppm": 8},
        {"id": 9, "userid": 7, "evaluated_at": AT.isoformat(), "tan_ppm": 9}])
    assert storage.fetch_chemistry_evaluation_at(7, AT)["tan_ppm"] == 1
    assert storage.fetch_chemistry_evaluation_at(7, AT.replace(hour=1)) is None


def test_kit_supabase_insert_list_and_exact_evaluation_queries():
    row = {"id": 1, "pond": 7, "taken_at": AT.isoformat(), "ph": 7.4}
    storage, client = _storage(data={"kit_readings": [row], "pond_chemistry_evaluations": [{"id": 4}]})
    assert storage.insert_kit_reading(7, {"ph": 7.4}) == row
    assert client.queries[-1].calls == [("insert", ({"ph": 7.4, "pond": 7},), {})]
    assert storage.fetch_kit_readings(7) == [row]
    assert client.queries[-1].calls == [
        ("select", ("*",), {}), ("eq", ("pond", 7), {}), ("order", ("taken_at",), {"nullsfirst": True}),
        ("order", ("id",), {}), ("range", (0, 999), {})]
    assert storage.fetch_chemistry_evaluation_at(7, AT)["id"] == 4
    assert client.queries[-1].calls == [
        ("select", ("*",), {}), ("eq", ("userid", 7), {}), ("eq", ("evaluated_at", AT.isoformat()), {}),
        ("order", ("id",), {"desc": True}), ("limit", (1,), {})]


def test_kit_supabase_pages_all_readings():
    storage, client = _storage(data={"kit_readings": [{"id": i} for i in range(1001)]})
    original_table = client.table

    def table(name):
        query = original_table(name)

        def execute():
            start, end = next(args for method, args, _ in query.calls if method == "range")
            return type("Res", (), {"data": client.data[name][start:end + 1]})()

        query.execute = execute
        return query

    client.table = table
    assert len(storage.fetch_kit_readings(7)) == 1001
    assert len(client.queries) == 2
    assert client.queries[-1].calls[-1] == ("range", (1000, 1999), {})


@pytest.mark.parametrize("operation", ["insert_kit_reading", "fetch_kit_readings", "fetch_chemistry_evaluation_at"])
def test_kit_supabase_storage_errors(operation):
    storage, _ = _storage(error=ConnectionError("offline"))
    with pytest.raises(StorageError, match=operation):
        if operation == "insert_kit_reading":
            storage.insert_kit_reading(7, {})
        elif operation == "fetch_kit_readings":
            storage.fetch_kit_readings(7)
        else:
            storage.fetch_chemistry_evaluation_at(7, AT)
