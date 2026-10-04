"""Issue #14: python -m koi.dev, its --check, and the local database
profile's guards. The memory profile runs here in full (real HTTP servers
on free local ports); the local database profile against a running
Supabase stack is in tests/sql/test_sql_dev_profile.py.

Run from Backend/: python -m pytest tests/dev
"""
import functools
import subprocess
import types

import pytest

from koi.dev import __main__ as dev_main
from koi.dev import local_db
from koi.dev.stack import comparable, contract_errors, differences
from koi.settings import Settings


def _settings(**values):
    return Settings(_env_file=None, **values)


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch):
    # Never read a developer's Backend/.env in a test.
    monkeypatch.setattr(dev_main, "Settings", functools.partial(Settings, _env_file=None))
    for name in ("KOI_DEV_SUPABASE_URL", "KOI_DEV_SUPABASE_SERVICE_ROLE_KEY", "KOI_DEV_DB_URL"):
        monkeypatch.delenv(name, raising=False)


def test_check_passes_on_the_memory_profile(capsys):
    assert dev_main.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "PASS GET /v1/ponds/1/dashboard (200, matches the OpenAPI document)" in out
    assert "PASS pond 1 dashboard content (three assessments, every reading fresh)" in out
    assert "PASS worker first cycle (pond 1 ok, pond 2 ok)" in out
    assert "PASS GET camera / (200)" in out
    assert "FAIL" not in out
    assert out.strip().endswith("0 failed (memory profile)")


def test_dev_settings_trust_the_path_and_never_use_the_live_project(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://live-project.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICEROLE_KEY", "not-a-real-key")
    monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.invalid/1")
    settings = dev_main.dev_settings("memory")
    assert (settings.env, settings.auth, settings.storage) == ("development", "disabled", "memory")
    assert settings.supabase_url == "" and settings.supabase_servicerole_key.get_secret_value() == ""
    assert settings.sentry_dsn.get_secret_value() == ""


def test_supabase_profile_without_a_local_stack_is_skip(monkeypatch, capsys):
    def unavailable(settings):
        raise local_db.LocalStackUnavailable("no database")

    monkeypatch.setattr(local_db, "discover", unavailable)
    assert dev_main.main(["--profile", "supabase", "--check"]) == dev_main.EXIT_SKIP
    assert capsys.readouterr().out.startswith("SKIP local Supabase stack not available: no database")


def test_supabase_profile_refuses_a_remote_database(monkeypatch, capsys):
    monkeypatch.setenv("KOI_DEV_SUPABASE_URL", "https://live-project.supabase.co")
    monkeypatch.setenv("KOI_DEV_SUPABASE_SERVICE_ROLE_KEY", "not-a-real-key")
    monkeypatch.setenv("KOI_DEV_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
    assert dev_main.main(["--profile", "supabase", "--check"]) == 1
    assert "must be on this machine" in capsys.readouterr().out


def test_seed_only_needs_the_supabase_profile():
    with pytest.raises(SystemExit):
        dev_main.main(["--seed-only"])


# ---------------------------------------------------------------------
# Finding the local stack
# ---------------------------------------------------------------------
def test_discover_reads_supabase_status(monkeypatch):
    monkeypatch.setattr(local_db.shutil, "which", lambda name: "/usr/bin/supabase")
    output = ('Stopped services: [supabase_vector]\nAPI_URL="http://127.0.0.1:54321"\n'
              'DB_URL="postgresql://postgres:postgres@127.0.0.1:54322/postgres"\nSERVICE_ROLE_KEY="local-key"\n')
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(local_db.subprocess, "run", run)
    stack = local_db.discover(_settings())
    assert stack == local_db.LocalStack("http://127.0.0.1:54321", "local-key",
                                        "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
    assert calls == [["/usr/bin/supabase", "status", "-o", "env"]]


def test_discover_prefers_the_environment_and_accepts_the_docker_host(monkeypatch):
    monkeypatch.setattr(local_db.subprocess, "run", lambda *a, **k: pytest.fail("supabase status was run"))
    monkeypatch.setenv("KOI_DEV_SUPABASE_URL", "http://host.docker.internal:54321")
    monkeypatch.setenv("KOI_DEV_SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setenv("KOI_DEV_DB_URL", "postgresql://postgres:postgres@host.docker.internal:54322/postgres")
    stack = local_db.discover(_settings())
    assert (stack.api_url, stack.service_role_key) == ("http://host.docker.internal:54321", "k")


@pytest.mark.parametrize("api, db", [
    ("https://abcd.supabase.co", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"),
    ("http://127.0.0.1:54321", "postgresql://postgres:pw@db.abcd.supabase.co:5432/postgres"),
])
def test_discover_refuses_anything_not_local(api, db):
    settings = _settings(dev_supabase_url=api, dev_supabase_service_role_key="k", dev_db_url=db)
    with pytest.raises(ValueError, match="must be on this machine"):
        local_db.discover(settings)


def test_discover_without_cli_or_environment_says_what_to_set(monkeypatch):
    monkeypatch.setattr(local_db.shutil, "which", lambda name: None)
    with pytest.raises(local_db.LocalStackUnavailable, match="KOI_DEV_SUPABASE_URL"):
        local_db.discover(_settings())


def test_discover_when_the_stack_is_stopped(monkeypatch):
    monkeypatch.setattr(local_db.shutil, "which", lambda name: "/usr/bin/supabase")
    monkeypatch.setattr(local_db.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout=""))
    with pytest.raises(local_db.LocalStackUnavailable, match="supabase start"):
        local_db.discover(_settings())


class _Conn:
    def __init__(self, versions):
        self.versions = versions

    def execute(self, sql):
        return [(v,) for v in self.versions]


def test_missing_migrations_lists_files_not_applied():
    files = sorted(p.name for p in local_db.MIGRATIONS.glob("*.sql"))
    applied = [name.split("_", 1)[0] for name in files]
    assert local_db.missing_migrations(_Conn(applied)) == []
    assert local_db.missing_migrations(_Conn(applied[:-1])) == [files[-1]]


def test_json_values_are_sent_as_text():
    assert local_db._value({"a": 1}) == '{"a": 1}'
    assert local_db._value(["base", 0.1]) == '["base", 0.1]'
    assert local_db._value("base") == "base" and local_db._value(None) is None


def test_seed_order_inserts_parents_first():
    order = local_db.INSERT_ORDER
    assert order.index("UserData") < order.index("pond_profile")
    deletes = [t for t, _ in local_db.POND_TABLES]
    assert deletes.index("pond_profile") < deletes.index("UserData")
    assert deletes.index("algae_severity_ratings") < deletes.index("imageTable")


# ---------------------------------------------------------------------
# Check helpers
# ---------------------------------------------------------------------
def test_contract_errors_catch_a_body_that_breaks_the_document():
    assert contract_errors("/v1/ponds/{pond}/dashboard", 200, {"pond_id": 1})
    assert contract_errors("/v1/ponds/{pond}/dashboard", 418, None) == ["status 418 is not documented"]


def test_comparable_drops_only_the_rows_own_id_and_time():
    body = {"assessments": {"chemistry": {"id": 4, "evaluated_at": "t", "status": "Green"}, "algae": None}}
    assert comparable(body) == {"assessments": {"chemistry": {"status": "Green"}, "algae": None}}
    assert body["assessments"]["chemistry"]["id"] == 4


def test_differences_name_the_path():
    assert differences({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]}) == ["/a/1/b: 2 != 3"]
    assert differences({"a": 1}, {"a": 1}) == []


def test_python_m_koi_dev_help_runs():
    import sys

    out = subprocess.run([sys.executable, "-m", "koi.dev", "--help"], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL)
    assert out.returncode == 0 and "--check" in out.stdout
