"""SQL tests for migration 0017_evaluation_daily.sql (issue #22): the
evaluation_daily table (RLS, grants, primary key index) and
summarize_evaluation_days (summaries written, then exactly the summarised
rows deleted, verified, all or nothing; idempotent; late rows merged;
missing values and absent domains kept missing; model versions kept; each
pond's newest day kept). The summaries are compared with
koi/storage/evaluation_daily.py, which MemoryStorage uses.

Same rules as test_sql_rpcs.py: local database only, every test rolled
back, skipped when no local database answers.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import pytest

from koi.storage.base import parse_timestamp
from koi.storage.evaluation_daily import DOMAIN_TABLES, merge_summary, summarize_group

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND, OTHER = 9422, 9423
TZ = "Asia/Singapore"
BEFORE = "2026-03-01"  # every test row below is on a day before it, or now()
SUMMARY_FIELDS = ("row_count", "first_evaluated_at", "last_evaluated_at", "status_counts", "metrics",
                  "model_versions")


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
        cur.execute("select to_regclass('public.evaluation_daily') is not null as ok")
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
        yield cur
    conn.rollback()


def _chem(db, at: str, pond: int = POND, tan: float = 0.1, ph: float | None = 0.2, status: str = "Green",
          version: str | None = "0.1.0+gaaa") -> None:
    db.execute("insert into public.pond_chemistry_evaluations (userid, evaluated_at, status, category, tan_ppm, "
               "no2_ppm, no3_ppm, ph_reactivity, advisory, model_version) "
               "values (%s, %s, %s, 'Normal', %s, 0, 5, %s, 'ok', %s)", (pond, at, status, tan, ph, version))


def _evap(db, at: str, pond: int = POND, loss: float | None = 1.0, days: int | None = 3) -> None:
    db.execute("insert into public.pond_evaporation_evaluations (userid, evaluated_at, status, category, "
               "loss_litres, days_to_topup) values (%s, %s, 'Green', 'Normal', %s, %s)", (pond, at, loss, days))


def _algae(db, at: str, pond: int = POND, green: float | None = 0.03) -> None:
    db.execute("insert into public.pond_algae_evaluations (userid, evaluated_at, status, category, green_ratio) "
               "values (%s, %s, 'Green', 'clear', %s)", (pond, at, green))


def _recent(db, pond: int = POND) -> None:
    """Rows now, so the old days are not the pond's newest."""
    _chem(db, datetime.now().astimezone().isoformat(), pond=pond)
    _evap(db, datetime.now().astimezone().isoformat(), pond=pond)
    _algae(db, datetime.now().astimezone().isoformat(), pond=pond)


def _summarize(db, before: str = BEFORE, max_days: int = 31) -> dict:
    db.execute("set local role service_role")
    db.execute("select public.summarize_evaluation_days(%s::date, %s, %s) as r", (before, TZ, max_days))
    result = db.fetchone()["r"]
    db.execute("reset role")
    return result


def _detail(db, domain: str, pond: int = POND) -> list[dict]:
    db.execute(f"select * from public.{DOMAIN_TABLES[domain]} where userid = %s order by evaluated_at, id", (pond,))
    return [{k: float(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, datetime) else v
             for k, v in r.items()} for r in db.fetchall()]


def _daily(db, pond: int = POND) -> list[dict]:
    db.execute("select * from public.evaluation_daily where pond_id = %s order by local_date, domain", (pond,))
    return db.fetchall()


def _normal(value: Any, key: str = "") -> Any:
    """Numbers as floats and times as instants, so the database's and
    Python's spellings compare equal."""
    if isinstance(value, dict):
        return {k: _normal(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_normal(v) for v in value]
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and key.endswith("_at"):
        return parse_timestamp(value)
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return float(value)
    return value


def _summary(row: dict) -> dict:
    return _normal({k: row[k] for k in SUMMARY_FIELDS})


# --- the table -----------------------------------------------------------

def test_retention_table_has_rls_on_and_no_policies(db):
    db.execute("select relrowsecurity from pg_class where oid = 'public.evaluation_daily'::regclass")
    assert db.fetchone()["relrowsecurity"] is True
    db.execute("select count(*) as n from pg_policies where schemaname = 'public' and tablename = 'evaluation_daily'")
    assert db.fetchone()["n"] == 0


def test_retention_table_grants_are_backend_only(db):
    db.execute("select grantee, string_agg(privilege_type, ',' order by privilege_type) as p "
               "from information_schema.role_table_grants where table_schema = 'public' "
               "and table_name = 'evaluation_daily' and grantee in ('anon', 'authenticated', 'service_role') "
               "group by grantee")
    grants = {r["grantee"]: r["p"] for r in db.fetchall()}
    assert set(grants) == {"service_role"}, "anon and authenticated have no access"
    assert {"DELETE", "INSERT", "SELECT", "UPDATE"} <= set(grants["service_role"].split(","))
    for role in ("anon", "authenticated"):
        db.execute("select has_function_privilege(%s, "
                   "'public.summarize_evaluation_days(date, text, integer)', 'execute') as ok", (role,))
        assert db.fetchone()["ok"] is False
    db.execute("select has_function_privilege('service_role', "
               "'public.summarize_evaluation_days(date, text, integer)', 'execute') as ok")
    assert db.fetchone()["ok"] is True


def test_retention_anon_cannot_run_or_read(db):
    db.execute("set local role anon")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("select public.summarize_evaluation_days(%s::date, %s, 7)", (BEFORE, TZ))


def test_retention_anon_cannot_read_the_table(db):
    db.execute("set local role anon")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("select * from public.evaluation_daily")


def test_retention_primary_key_covers_pond_domain_and_date(db):
    db.execute("select pg_get_indexdef(i.indexrelid) as def, i.indisprimary from pg_index i "
               "where i.indrelid = 'public.evaluation_daily'::regclass")
    indexes = db.fetchall()
    assert any(r["indisprimary"] and "(pond_id, domain, local_date)" in r["def"] for r in indexes)


def test_retention_table_refuses_bad_rows(db):
    db.execute("insert into public.evaluation_daily (pond_id, domain, local_date, time_zone, row_count, "
               "first_evaluated_at, last_evaluated_at, status_counts, metrics, model_versions) values "
               "(%s, 'chemistry', '2026-01-01', %s, 1, now(), now(), '{}', '{}', '[]')", (POND, TZ))
    db.execute("savepoint s")
    with pytest.raises(errors.UniqueViolation):
        db.execute("insert into public.evaluation_daily (pond_id, domain, local_date, time_zone, row_count, "
                   "first_evaluated_at, last_evaluated_at, status_counts, metrics, model_versions) values "
                   "(%s, 'chemistry', '2026-01-01', %s, 1, now(), now(), '{}', '{}', '[]')", (POND, TZ))
    for bad in ("domain = 'weather'", "row_count = 0", "metrics = '[]'", "model_versions = '{}'",
                "first_evaluated_at = last_evaluated_at + interval '1 hour'"):
        db.execute("rollback to savepoint s")
        with pytest.raises(errors.CheckViolation):
            db.execute(f"update public.evaluation_daily set {bad} where pond_id = %s", (POND,))
    db.execute("rollback to savepoint s")


# --- the function --------------------------------------------------------

def test_retention_summary_matches_python_and_deletes_the_rows(db):
    _chem(db, "2026-01-10T01:00:00+00:00", tan=0.3, ph=None, status="Amber", version=None)
    _chem(db, "2026-01-10T05:00:00+00:00", tan=0.1, ph=0.4, version="0.1.0+gaaa")
    _chem(db, "2026-01-10T09:00:00+00:00", tan=0.2, ph=None, version="0.1.0+gbbb")
    _evap(db, "2026-01-10T01:00:00+00:00", loss=None, days=None)
    _evap(db, "2026-01-10T02:00:00+00:00", loss=2.5, days=None)
    _recent(db)
    expected = {d: summarize_group(d, _detail(db, d)[:-1]) for d in ("chemistry", "evaporation")}

    result = _summarize(db)
    assert result == {"days": ["2026-01-10"], "rows": 5, "summaries": 2}
    days = {r["domain"]: r for r in _daily(db)}
    assert set(days) == {"chemistry", "evaporation"}, "no algae rows that day: no algae summary"
    for domain, row in days.items():
        assert str(row["local_date"]) == "2026-01-10" and row["time_zone"] == TZ
        assert _summary(row) == _normal(expected[domain])
    # Missing all day stays missing, never zero.
    assert days["evaporation"]["metrics"]["days_to_topup"] == {
        "n": 0, "first": None, "first_at": None, "last": None, "last_at": None, "min": None, "max": None}
    assert days["chemistry"]["metrics"]["ph_reactivity"]["n"] == 1
    assert [v["model_version"] for v in days["chemistry"]["model_versions"]] == [None, "0.1.0+gaaa", "0.1.0+gbbb"]
    # Only the summarised rows went; today's stay.
    assert len(_detail(db, "chemistry")) == 1 and len(_detail(db, "evaporation")) == 1
    assert len(_detail(db, "algae")) == 1


def test_retention_is_idempotent(db):
    _chem(db, "2026-01-10T01:00:00+00:00")
    _chem(db, "2026-01-11T01:00:00+00:00")
    _recent(db)
    _summarize(db)
    summaries, rows = _daily(db), _detail(db, "chemistry")
    assert _summarize(db) == {"days": [], "rows": 0, "summaries": 0}
    assert _daily(db) == summaries and _detail(db, "chemistry") == rows


def test_retention_uses_the_pond_local_day(db):
    # 16:30 UTC on 10 January is 00:30 on 11 January in Singapore.
    _chem(db, "2026-01-10T15:59:00+00:00")
    _chem(db, "2026-01-10T16:30:00+00:00")
    _recent(db)
    _summarize(db)
    assert [(str(r["local_date"]), r["row_count"]) for r in _daily(db)] == [("2026-01-10", 1), ("2026-01-11", 1)]


def test_retention_keeps_each_ponds_newest_day(db):
    _chem(db, "2026-01-10T01:00:00+00:00", pond=OTHER, tan=0.1)
    _chem(db, "2026-01-11T01:00:00+00:00", pond=OTHER, tan=0.2)
    _algae(db, "2026-01-11T01:00:00+00:00", pond=OTHER)
    _summarize(db)
    assert [(str(r["local_date"]), r["domain"]) for r in _daily(db, OTHER)] == [("2026-01-10", "chemistry")]
    assert [r["tan_ppm"] for r in _detail(db, "chemistry", OTHER)] == [0.2]
    assert len(_detail(db, "algae", OTHER)) == 1


def test_retention_takes_the_oldest_days_first_in_batches(db):
    for day in range(10, 15):
        _chem(db, f"2026-01-{day}T01:00:00+00:00")
    _recent(db)
    assert _summarize(db, max_days=2)["days"] == ["2026-01-10", "2026-01-11"]
    assert _summarize(db, max_days=2)["days"] == ["2026-01-12", "2026-01-13"]
    assert _summarize(db, max_days=2) == {"days": ["2026-01-14"], "rows": 1, "summaries": 1}
    assert _summarize(db, max_days=2)["days"] == []


def test_retention_merges_a_late_row_into_its_summary(db):
    _chem(db, "2026-01-10T02:00:00+00:00", tan=0.2, version="v1")
    _recent(db)
    _summarize(db)
    [first] = _daily(db)
    _chem(db, "2026-01-10T01:00:00+00:00", tan=0.4, status="Red", version="v0")
    _chem(db, "2026-01-10T05:00:00+00:00", tan=0.15, ph=None, version="v1")
    late = summarize_group("chemistry", _detail(db, "chemistry")[:2])
    assert _summarize(db)["rows"] == 2

    [merged] = _daily(db)
    expected = merge_summary(_summary_input(first), late)
    assert _summary(merged) == _normal({k: expected[k] for k in SUMMARY_FIELDS})
    assert merged["row_count"] == 3 and merged["summarized_at"] == first["summarized_at"]
    assert [(v["model_version"], v["rows"]) for v in merged["model_versions"]] == [("v0", 1), ("v1", 2)]


def _summary_input(row: dict) -> dict:
    """A stored summary as merge_summary takes it (times as ISO text)."""
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()}


def test_retention_refuses_a_day_not_yet_finished(db):
    db.execute("set local role service_role")
    db.execute("select ((now() at time zone %s)::date + 1)::text as d", (TZ,))
    tomorrow = db.fetchone()["d"]
    with pytest.raises(errors.RaiseException):
        db.execute("select public.summarize_evaluation_days(%s::date, %s, 7)", (tomorrow, TZ))


def test_retention_rolls_back_when_a_delete_fails(db):
    _chem(db, "2026-01-10T01:00:00+00:00")
    _recent(db)
    before = _detail(db, "chemistry")
    db.execute("create function pg_temp.refuse() returns trigger language plpgsql as "
               "$$ begin raise exception 'delete refused'; end $$")
    db.execute("create trigger refuse before delete on public.pond_chemistry_evaluations "
               "for each row execute function pg_temp.refuse()")
    db.execute("savepoint s")
    with pytest.raises(errors.RaiseException):
        _summarize(db)
    db.execute("rollback to savepoint s")
    assert _daily(db) == [] and _detail(db, "chemistry") == before


def test_retention_rolls_back_when_the_delete_count_does_not_match(db):
    """A row that silently survives the delete fails the check, and the
    summary written before it is rolled back too."""
    _chem(db, "2026-01-10T01:00:00+00:00")
    _chem(db, "2026-01-10T02:00:00+00:00")
    _recent(db)
    before = _detail(db, "chemistry")
    db.execute("create function pg_temp.keep_one() returns trigger language plpgsql as "
               "$$ begin if old.evaluated_at = '2026-01-10T02:00:00+00:00' then return null; end if; "
               "return old; end $$")
    db.execute("create trigger keep_one before delete on public.pond_chemistry_evaluations "
               "for each row execute function pg_temp.keep_one()")
    db.execute("savepoint s")
    with pytest.raises(errors.RaiseException, match="2 rows read"):
        _summarize(db)
    db.execute("rollback to savepoint s")
    assert _daily(db) == [] and _detail(db, "chemistry") == before


def test_retention_leaves_model_inputs_alone(db):
    db.execute("""insert into public."SensorData" ("userID", sensor_type, data1, created_at) """
               "values (%s, 'PH', 7.2, '2026-01-10T01:00:00+00:00')", (POND,))
    db.execute("""insert into public."pondInterventions" ("userID", event_type, food_grams, event_timestamp) """
               "values (%s, 'FEEDING', 10, '2026-01-10T01:00:00+00:00')", (POND,))
    _chem(db, "2026-01-10T01:00:00+00:00")
    _recent(db)
    _summarize(db)
    db.execute("""select (select count(*) from public."SensorData" where "userID" = %s) as s, """
               """(select count(*) from public."pondInterventions" where "userID" = %s) as i""", (POND, POND))
    assert db.fetchone() == {"s": 1, "i": 1}
