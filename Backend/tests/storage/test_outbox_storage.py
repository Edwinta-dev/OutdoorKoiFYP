"""Issue #36: both storage adapters write, list and mark
notification_outbox rows the same way (migration 0024)."""
from datetime import datetime, timedelta, timezone

import pytest
from test_supabase_storage import _storage

from koi.storage import MemoryStorage, StorageError
from koi.storage.base import NOTIFICATION_COLUMNS

AT = datetime(2026, 10, 9, tzinfo=timezone.utc)


def entry(key="status_red:chemistry", at=AT, **values):
    return {"kind": "status_red", "severity": "critical", "title": "Water chemistry: High Risk",
            "body": "Consider a partial water change.", "data": {"domain": "chemistry"}, "dedupe_key": key,
            "created_at": at.isoformat(), **values}


def test_outbox_memory_enqueue_cool_down_isolation_and_copy():
    storage = MemoryStorage(clock=lambda: AT)
    first = storage.enqueue_notification(7, entry(), 3600)
    assert set(first) == set(NOTIFICATION_COLUMNS) and first["pond"] == 7
    assert first["created_at"] == AT.isoformat() and first["sent_at"] is None and first["error"] is None
    assert storage.enqueue_notification(7, entry(at=AT + timedelta(minutes=59)), 3600) is None
    assert storage.enqueue_notification(8, entry(), 3600) is not None, "another pond is separate"
    assert storage.enqueue_notification(7, entry("status_red:algae"), 3600) is not None, "another key is separate"
    assert storage.enqueue_notification(7, entry(at=AT + timedelta(hours=1)), 3600) is not None, \
        "exactly one cool-down later is allowed"
    rows = storage.fetch_notifications(7)
    assert [r["dedupe_key"] for r in rows] == ["status_red:chemistry", "status_red:algae", "status_red:chemistry"]
    rows[0]["data"]["domain"] = "changed"
    assert storage.fetch_notifications(7)[0]["data"] == {"domain": "chemistry"}
    assert len(storage.fetch_notifications(7, limit=1)) == 1


def test_outbox_memory_marks_results_and_filters_unsent():
    storage = MemoryStorage(clock=lambda: AT)
    a = storage.enqueue_notification(7, entry("a"), 0)
    b = storage.enqueue_notification(7, entry("b"), 0)
    storage.enqueue_notification(7, entry("c"), 0)
    storage.record_notification_result(7, a["id"], AT.isoformat(), None)
    storage.record_notification_result(7, b["id"], None, "no device token")
    storage.record_notification_result(8, b["id"], AT.isoformat(), None)  # another pond's id: no change
    assert [r["dedupe_key"] for r in storage.fetch_notifications(7, unsent_only=True)] == ["c"]
    rows = {r["dedupe_key"]: r for r in storage.fetch_notifications(7)}
    assert rows["a"]["sent_at"] == AT.isoformat() and rows["b"]["error"] == "no device token"
    assert rows["b"]["sent_at"] is None


@pytest.mark.parametrize("values, cooldown", [
    ({"kind": "email"}, 0), ({"severity": "urgent"}, 0), ({"title": "  "}, 0), ({"body": ""}, 0),
    ({"dedupe_key": ""}, 0), ({"data": [1]}, 0), ({}, -1)])
def test_outbox_memory_rejects_what_the_table_rejects(values, cooldown):
    with pytest.raises(StorageError, match="enqueue_notification"):
        MemoryStorage(clock=lambda: AT).enqueue_notification(7, entry(**values), cooldown)


def test_outbox_supabase_calls():
    stored = {"id": 1, "pond": 7, **entry(), "sent_at": None, "error": None}
    storage, client = _storage(data={"rpc:enqueue_notification": stored, "notification_outbox": [stored]})
    assert storage.enqueue_notification(7, entry(), 43200) == stored
    assert client.queries[-1].calls == [("rpc", ("enqueue_notification", {
        "p_pond": 7, "p_kind": "status_red", "p_severity": "critical", "p_title": "Water chemistry: High Risk",
        "p_body": "Consider a partial water change.", "p_data": {"domain": "chemistry"},
        "p_dedupe_key": "status_red:chemistry", "p_created_at": AT.isoformat(), "p_cooldown_seconds": 43200}), {})]
    assert storage.fetch_notifications(7, unsent_only=True, limit=5) == [stored]
    assert client.queries[-1].calls == [
        ("select", (", ".join(NOTIFICATION_COLUMNS),), {}), ("eq", ("pond", 7), {}),
        ("is_", ("sent_at", "null"), {}), ("is_", ("error", "null"), {}), ("order", ("created_at",), {}),
        ("order", ("id",), {}), ("limit", (5,), {})]
    storage.record_notification_result(7, 1, AT.isoformat(), None)
    assert client.queries[-1].calls == [("update", ({"sent_at": AT.isoformat(), "error": None},), {}),
                                        ("eq", ("id", 1), {}), ("eq", ("pond", 7), {})]


def test_outbox_supabase_suppressed_enqueue_is_none():
    storage, _ = _storage(data={"rpc:enqueue_notification": None})
    assert storage.enqueue_notification(7, entry(), 60) is None


@pytest.mark.parametrize("operation", ["enqueue_notification", "fetch_notifications", "record_notification_result"])
def test_outbox_supabase_storage_errors(operation):
    storage, _ = _storage(error=ConnectionError("offline"))
    calls = {"enqueue_notification": lambda: storage.enqueue_notification(7, entry(), 60),
             "fetch_notifications": lambda: storage.fetch_notifications(7),
             "record_notification_result": lambda: storage.record_notification_result(7, 1, None, "x")}
    with pytest.raises(StorageError, match=operation):
        calls[operation]()
