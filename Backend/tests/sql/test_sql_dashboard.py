"""SQL tests for pond_dashboard_sources (migration 0012), the rows behind
GET /v1/ponds/{pond}/dashboard.

The chain checked here, for every pond in tests/dashboard_scenarios.py:
real SQL rows -> pond_dashboard_sources -> SupabaseStorage.fetch_dashboard_sources
(its RPC call run against the local database) -> koi.api.dashboard ->
the response model -> the committed Dart fixture that the app's parser
reads (MobileUI/mobile_app/test/pond_dashboard_test.dart). The same rows
through MemoryStorage give the same response.

Local Supabase stack only, each test rolled back; skipped when no local
database answers, as in CI.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")
from dashboard_scenarios import SCENARIOS, dashboard_for, memory_storage, seed_sql  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from conftest import make_settings  # noqa: E402
from koi.storage.supabase_storage import SupabaseStorage  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
DART_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "MobileUI", "mobile_app",
                            "test", "fixtures", "v1_dashboard.json")


def _connect() -> Any:
    host = urlparse(DB_URL).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        pytest.fail(f"KOI_TEST_DB_URL must point at a local database, not {host}")
    try:
        return psycopg.connect(DB_URL, connect_timeout=2, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"no local database at {DB_URL} ({exc.__class__.__name__}); run `supabase start`")


@pytest.fixture(scope="module")
def conn() -> Iterator[Any]:
    c = _connect()
    with c.cursor() as cur:
        cur.execute("select to_regprocedure('public.pond_dashboard_sources(bigint)') is not null as ok")
        row = cur.fetchone()
    c.rollback()
    if not row["ok"]:
        c.close()
        pytest.skip("local database is not at the current migrations; run `supabase db reset`")
    yield c
    c.close()


@pytest.fixture
def db(conn: Any) -> Iterator[Any]:
    with conn.cursor() as cur:
        seed_sql(cur)
        yield cur
    conn.rollback()


class _Rpc:
    def __init__(self, cur: Any, name: str, params: dict):
        self.cur, self.name, self.params = cur, name, params

    def execute(self) -> Any:
        assert self.name == "pond_dashboard_sources", self.name
        self.cur.execute("select public.pond_dashboard_sources(%(p_pond_id)s) as r", self.params)
        return type("Result", (), {"data": self.cur.fetchone()["r"]})()


class _LocalClient:
    """The one supabase-py call fetch_dashboard_sources makes, run
    through the test's local connection."""

    def __init__(self, cur: Any):
        self.cur = cur

    def rpc(self, name: str, params: dict) -> _Rpc:
        return _Rpc(self.cur, name, params)


def _sql_storage(db: Any) -> SupabaseStorage:
    return SupabaseStorage(make_settings(storage="supabase"), client=_LocalClient(db))  # type: ignore[arg-type]


def _sources(db: Any, pond: int) -> dict:
    db.execute("select public.pond_dashboard_sources(%s) as r", (pond,))
    return db.fetchone()["r"]


@pytest.mark.parametrize("name, pond, now", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_sql_dashboard_matches_memory_storage(db, name, pond, now):
    from_sql = dashboard_for(_sql_storage(db), pond, now)
    from_memory = dashboard_for(memory_storage(), pond, now)
    assert from_sql == from_memory


@pytest.mark.parametrize("name, pond, now", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_sql_dashboard_is_what_the_dart_parser_reads(db, name, pond, now):
    with open(DART_FIXTURE, encoding="utf-8") as f:
        fixture = json.load(f)
    assert dashboard_for(_sql_storage(db), pond, now) == fixture[name]


def test_sql_sources_hold_only_the_ponds_rows(db):
    complete, other = _sources(db, 9101), _sources(db, 9106)
    assert {r["id"] // 100 for r in complete["readings"]} == {9101}
    assert {r["id"] // 100 for r in other["readings"]} == {9106}
    # One row per sensor_type, the newest: the older 9101 cycle is not returned.
    assert sorted(r["id"] for r in complete["readings"]) == [910111, 910112, 910113, 910114]
    assert {t["station_id"] for t in complete["telemetry"]} == {"S43"}
    assert {t["station_id"] for t in other["telemetry"]} == {"S24"}
    assert {(f["forecast_type"], f["slot_id"]) for f in other["forecasts"] if f["forecast_type"] in ("2hr", "24hr")} \
        == {("2hr", "Changi"), ("24hr", "GENERAL")}


def test_sql_same_instant_readings_resolve_by_id(db):
    ph = [r for r in _sources(db, 9106)["readings"] if r["sensor_type"] == "pH"]
    assert [r["id"] for r in ph] == [910602]


def test_sql_unassigned_station_is_not_borrowed(db):
    sources = _sources(db, 9104)
    # Only the cache row of the assigned station, not its legacy_import row
    # (which has a rainfall key) and not S43's.
    assert [(t["station_id"], t["metric_type"]) for t in sources["telemetry"]] == [("S50", "realtime_sensor")]
    assert [f for f in sources["forecasts"] if f["forecast_type"] == "2hr"] == []


def test_sql_unknown_pond_has_no_rows(db):
    sources = _sources(db, 9999)
    assert sources["pond_exists"] is False
    assert sources["readings"] == [] and sources["telemetry"] == []


def test_sql_dashboard_sources_is_read_only_and_backend_only(db):
    db.execute("select p.provolatile, p.prosecdef from pg_proc p where p.oid = "
               "'public.pond_dashboard_sources(bigint)'::regprocedure")
    row = db.fetchone()
    assert row["provolatile"] == "s" and row["prosecdef"] is False
    for role, allowed in (("anon", False), ("authenticated", False), ("service_role", True)):
        db.execute("select has_function_privilege(%s, 'public.pond_dashboard_sources(bigint)', 'execute') as ok",
                   (role,))
        assert db.fetchone()["ok"] is allowed, role


def test_sql_sensor_upload_grants_unchanged(db):
    # The sensor sketch inserts into SensorData with the publishable key.
    db.execute("select has_table_privilege('anon', 'public.\"SensorData\"', 'insert') as ok")
    assert db.fetchone()["ok"] is True
