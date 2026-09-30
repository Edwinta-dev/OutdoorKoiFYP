"""Tests for tools/check.py. Run with: python -m pytest tools"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check  # noqa: E402

PY = sys.executable


def test_missing_tool_is_skip_and_does_not_fail(capsys):
    r = check.Runner(strict=False)
    r.skip("backend: ruff", "ruff")
    assert capsys.readouterr().out.strip() == "SKIP backend: ruff (ruff not found)"
    assert not r.failed


def test_missing_tool_fails_under_strict():
    r = check.Runner(strict=True)
    result = r.skip("backend: ruff", "ruff")
    assert result.status == "FAIL"
    assert r.failed


def test_run_records_pass_with_timing_and_summary(capsys):
    r = check.Runner(strict=False)
    result = r.run("ok", [PY, "-c", "print('3 passed, 0 failed')"], Path.cwd(),
                   summarise=lambda out: check.last_match(r"\d+ passed, \d+ failed", out))
    assert result.status == "PASS"
    assert result.detail == "3 passed, 0 failed"
    assert "PASS ok (3 passed, 0 failed) [" in capsys.readouterr().out
    assert not r.failed


def test_run_records_fail_on_non_zero_exit():
    r = check.Runner(strict=False)
    result = r.run("bad", [PY, "-c", "import sys; sys.exit(3)"], Path.cwd())
    assert result.status == "FAIL"
    assert result.detail == "exit 3"
    assert r.failed


def test_run_records_fail_when_command_cannot_start():
    r = check.Runner(strict=False)
    result = r.run("gone", [str(Path.cwd() / "no-such-program-xyz")], Path.cwd())
    assert result.status == "FAIL"


def test_firmware_without_tools_skips_every_step(monkeypatch):
    monkeypatch.setattr(check.shutil, "which", lambda name: None)
    r = check.Runner(strict=False)
    check.check_firmware(r)
    assert [x.status for x in r.results] == ["SKIP"] * (1 + len(check.SKETCHES))
    assert r.results[0].detail == "g++ not found"
    assert all(x.detail == "arduino-cli not found" for x in r.results[1:])


def test_mobile_without_flutter_skips(monkeypatch):
    monkeypatch.setattr(check.shutil, "which", lambda name: None)
    r = check.Runner(strict=False)
    check.check_mobile(r)
    assert {x.status for x in r.results} == {"SKIP"}
    assert all(x.detail == "flutter not found" for x in r.results)


@pytest.mark.parametrize("strict, expected", [(False, 0), (True, 1)])
def test_main_exit_code_follows_strict_for_missing_tools(monkeypatch, strict, expected):
    monkeypatch.setattr(check.shutil, "which", lambda name: None)
    argv = ["mobile"] + (["--strict"] if strict else [])
    assert check.main(argv) == expected


def test_main_exit_code_non_zero_on_any_fail(monkeypatch):
    def failing_suite(r):
        r.run("bad", [PY, "-c", "import sys; sys.exit(1)"], Path.cwd())

    monkeypatch.setitem(check.SUITES, "mobile", failing_suite)
    assert check.main(["mobile"]) == 1


def test_pytest_count_pattern():
    out = "....\n334 assertions passed\n69 passed, 2 skipped in 0.60s\n"
    assert check.last_match(check.PYTEST_COUNTS, out) == "69 passed, 2 skipped"
