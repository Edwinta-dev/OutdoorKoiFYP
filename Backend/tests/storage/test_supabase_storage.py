"""koi.storage.SupabaseStorage against a recording stand-in for the
supabase client. No network: the client is injected."""
import pytest

from conftest import ALGAE_ASSESSMENT, make_settings
from koi.storage import StaleSnapshotError, StorageError, SupabaseStorage


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.calls = []
        db.queries.append(self)

    def __getattr__(self, method):
        def record(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            return self
        return record

    def execute(self):
        if self.db.error is not None:
            raise self.db.error
        return type("Res", (), {"data": self.db.data.get(self.table, [])})()


class _Bucket:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def upload(self, path, file, file_options):
        self.db.uploads.append((self.name, path, len(file), file_options["content-type"]))

    def get_public_url(self, path):
        return f"https://storage.invalid/{self.name}/{path}?"


class FakeClient:
    def __init__(self, data=None, error=None):
        self.data = data or {}
        self.error = error
        self.queries = []
        self.uploads = []
        self.storage = type("Storage", (), {"from_": lambda _s, name: _Bucket(self, name)})()

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        q = _Query(self, f"rpc:{name}")
        q.calls.append(("rpc", (name, params), {}))
        return q


def _storage(**kwargs):
    client = FakeClient(**kwargs)
    return SupabaseStorage(make_settings(storage="supabase"), client=client), client


def test_driver_errors_become_storage_errors_naming_the_operation():
    cause = ConnectionError("network down")
    storage, _ = _storage(error=cause)
    with pytest.raises(StorageError, match="fetch_latest_evaluation failed: network down") as info:
        storage.fetch_latest_evaluation(1)
    assert info.value.operation == "fetch_latest_evaluation"
    assert info.value.__cause__ is cause


def test_malformed_rows_become_storage_errors():
    storage, _ = _storage(data={"pond_chemistry_state": [{"not_snapshot": 1}]})
    with pytest.raises(StorageError, match="load_engine_snapshot"):
        storage.load_engine_snapshot(1)


def test_missing_credentials_are_reported_on_first_use():
    storage = SupabaseStorage(make_settings(storage="supabase"))
    with pytest.raises(StorageError, match="SUPABASE_URL and SUPABASE_SERVICEROLE_KEY"):
        storage.save_engine_snapshot(1, {})


def test_snapshot_load_and_versioned_save():
    storage, client = _storage(data={"pond_chemistry_state": [{"snapshot": {"version": 2}, "snapshot_version": 6}],
                                     "rpc:save_pond_snapshot": 7})
    assert storage.load_engine_snapshot(4) == {"version": 2}
    assert storage.load_engine_state(4) == ({"version": 2}, 6)
    assert storage.fetch_snapshot_version(4) == 6
    assert storage.save_engine_snapshot(4, {"version": 3}, base_version=6) == 7
    assert client.queries[-1].calls[0] == (
        "rpc", ("save_pond_snapshot", {"p_user_id": 4, "p_snapshot": {"version": 3}, "p_base_version": 6}), {})


def test_rows_from_before_the_version_column_read_as_version_1():
    storage, _ = _storage(data={"pond_chemistry_state": [{"snapshot": {"version": 2}}]})
    assert storage.load_engine_state(4) == ({"version": 2}, 1)
    assert storage.fetch_snapshot_version(4) == 1
    empty, _ = _storage()
    assert empty.load_engine_state(4) is None and empty.fetch_snapshot_version(4) == 0


def test_stale_save_raises_stale_snapshot_error():
    storage, _ = _storage(data={"rpc:save_pond_snapshot": None})
    with pytest.raises(StaleSnapshotError) as info:
        storage.save_engine_snapshot(4, {}, base_version=2)
    assert info.value.operation == "save_engine_snapshot" and info.value.base_version == 2


def test_lease_take_and_release():
    storage, client = _storage(data={"rpc:take_worker_lease": True})
    assert storage.take_lease("poller", "host:1", 1800) is True
    assert client.queries[-1].calls[0] == (
        "rpc", ("take_worker_lease", {"p_name": "poller", "p_holder": "host:1", "p_ttl_seconds": 1800}), {})
    refused, _ = _storage(data={"rpc:take_worker_lease": False})
    assert refused.take_lease("poller", "host:2", 1800) is False
    storage.release_lease("poller", "host:1")
    assert client.queries[-1].table == "worker_lease"
    assert client.queries[-1].calls == [("delete", (), {}), ("eq", ("name", "poller"), {}),
                                        ("eq", ("holder", "host:1"), {})]


def test_algae_evaluation_insert_keeps_only_table_columns():
    storage, client = _storage()
    storage.push_algae_evaluation(4, ALGAE_ASSESSMENT)
    [(method, (row,), _)] = client.queries[-1].calls
    assert method == "insert" and row["userid"] == 4
    assert "label_count" not in row


def test_ratings_come_back_oldest_first():
    storage, client = _storage(data={"algae_severity_ratings": [{"id": 3}, {"id": 2}]})
    assert [r["id"] for r in storage.fetch_algae_ratings(4, limit=2)] == [2, 3]
    assert ("order", ("rated_at",), {"desc": True}) in client.queries[-1].calls


def test_active_pond_configs_skip_incomplete_onboarding():
    storage, _ = _storage(data={"UserData": [
        {"userID": 1, "volume": 800, "biomass": 1.5},
        {"userID": 2, "volume": None, "biomass": 1.0},
    ]})
    assert storage.fetch_active_pond_configs() == [
        {"volume_litres": 800.0, "estimated_biomass_grams": 1500.0, "user_id": 1}]


def test_daily_series_oldest_first_and_stats_averaged():
    rows = [{"avg_value": 3.0, "min_value": 1.0, "max_value": 5.0, "record_date": "2026-08-03"},
            {"avg_value": 1.0, "min_value": 0.5, "max_value": 2.0, "record_date": "2026-08-02"}]
    storage, _ = _storage(data={"daily_sensor_averages": rows})
    assert [r["record_date"] for r in storage.fetch_daily_sensor_series(4, "TDS")] == ["2026-08-02", "2026-08-03"]
    assert storage.fetch_daily_sensor_stats(4, "TDS") == {"avg": 2.0, "min": 0.5, "max": 5.0, "sample_days": 2}


def test_dashboard_payload_calls_the_rpc():
    storage, client = _storage(data={"rpc:get_bundled_dashboard_payload": {"raw_sensor": {}}})
    assert storage.fetch_dashboard_payload(4) == {"raw_sensor": {}}
    assert client.queries[-1].calls[0] == ("rpc", ("get_bundled_dashboard_payload", {"p_user_id": 4}), {})


def test_upload_image_strips_the_trailing_query_marker():
    storage, client = _storage()
    url = storage.upload_image("frames", "15/1_photo.jpg", b"jpeg")
    assert url == "https://storage.invalid/frames/15/1_photo.jpg"
    assert client.uploads == [("frames", "15/1_photo.jpg", 4, "image/jpeg")]


def test_worker_status_is_upserted_by_name_and_read_back():
    row = {"name": "poller", "holder": "w1", "cycle_started_at": "a", "cycle_finished_at": "b",
           "cycle_duration_sec": 1.5, "last_success_at": None, "ponds": {}}
    storage, client = _storage(data={"worker_status": [row],
                                     "worker_lease": [{"name": "poller", "holder": "w1", "expires_at": "c"}],
                                     "pond_chemistry_state": [{"user_id": 455, "updated_at": "d"}]})
    storage.record_worker_status("poller", {k: v for k, v in row.items() if k != "name"})
    upsert = client.queries[-1]
    assert upsert.table == "worker_status"
    assert upsert.calls[0] == ("upsert", (row,), {"on_conflict": "name"})
    assert storage.fetch_worker_status("poller") == row
    assert storage.fetch_lease("poller")["holder"] == "w1"
    assert storage.fetch_snapshot_times() == {"455": "d"}


def test_worker_status_read_failure_is_a_storage_error():
    storage, _ = _storage(error=RuntimeError("down"))
    with pytest.raises(StorageError, match="fetch_worker_status"):
        storage.fetch_worker_status("poller")


def test_auth_pond_for_account_filters_userdata_by_auth_uid():
    uid = "6c1f0c4e-5b1a-4a39-9d0e-455000000455"
    storage, client = _storage(data={"UserData": [{"userID": 455}]})
    assert storage.fetch_pond_id_for_account(uid) == 455
    calls = client.queries[-1].calls
    assert ("select", ("userID",), {}) in calls
    assert ("eq", ("auth_uid", uid), {}) in calls
    unlinked, _ = _storage(data={"UserData": []})
    assert unlinked.fetch_pond_id_for_account(uid) is None


def test_auth_session_check_calls_the_rpc_and_needs_a_true_answer():
    storage, client = _storage(data={"rpc:auth_session_active": True})
    assert storage.is_session_active("s-1", "u-1") is True
    assert client.queries[-1].calls[0] == (
        "rpc", ("auth_session_active", {"p_session_id": "s-1", "p_user_id": "u-1"}), {})
    for answer in (False, None, [], "true"):
        refused, _ = _storage(data={"rpc:auth_session_active": answer})
        assert refused.is_session_active("s-1", "u-1") is False


def test_auth_session_check_errors_are_storage_errors():
    storage, _ = _storage(error=ConnectionError("down"))
    with pytest.raises(StorageError, match="is_session_active"):
        storage.is_session_active("s-1", "u-1")
