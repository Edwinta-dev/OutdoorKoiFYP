"""Tests for tools/check.py. Run with: python -m pytest tools"""
import re
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


def test_database_suite_runs_only_when_named(monkeypatch):
    def failing_suite(r):
        r.run("bad", [PY, "-c", "import sys; sys.exit(1)"], Path.cwd())

    for name in check.SUITES:
        monkeypatch.setitem(check.SUITES, name, lambda r: None)
    monkeypatch.setitem(check.SEPARATE_SUITES, "database", failing_suite)
    assert check.main(["all"]) == 0
    assert check.main(["database"]) == 1


def test_database_without_docker_skips_every_step(monkeypatch):
    monkeypatch.setattr(check.shutil, "which", lambda name: None)
    r = check.Runner(strict=False)
    check.check_database(r)
    assert [x.status for x in r.results] == ["SKIP"] * 3
    assert all(x.detail == "docker not found" for x in r.results)


def test_database_without_local_stack_skips_and_fails_under_strict(monkeypatch):
    monkeypatch.setattr(check.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(check, "python_tool", lambda module: ["pytest"])
    monkeypatch.setattr(check, "local_stack_running", lambda: False)
    r = check.Runner(strict=False)
    check.check_database(r)
    assert [x.status for x in r.results] == ["SKIP"] * 3
    assert r.results[0].detail == "local Supabase stack (run `supabase start`) not found"
    strict = check.Runner(strict=True)
    check.check_database(strict)
    assert strict.failed


def test_database_with_local_stack_runs_rehearsal_sql_tests_and_dev_profile(monkeypatch):
    monkeypatch.setattr(check.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(check, "python_tool", lambda module: ["pytest"])
    monkeypatch.setattr(check, "local_stack_running", lambda: True)
    commands = []
    monkeypatch.setattr(check.Runner, "run", lambda self, name, cmd, cwd, summarise=None: commands.append(cmd))
    check.check_database(check.Runner(strict=True))
    assert commands[0][-1].endswith("db_rehearsal.py")
    assert commands[1] == ["pytest", "-q", "tests/sql"]
    assert commands[2][1:] == ["-m", "koi.dev", "--profile", "supabase", "--check"]


# Credential-shaped strings are assembled from pieces so this file does not
# trip the scan it tests.
LEAKS = {
    "wifi.ino": ('const char* WIFI_' + 'PASS = "hunter2";', "Wi-Fi password assignment"),
    "cam.ino": ("const char *pass" + 'word = "hunter2";', "Wi-Fi password assignment"),
    "token.ino": ("const char* DEVICE_" + 'TOKEN = "abc";', "device token literal"),
    "key.dart": ("const k = 'eyJ" + "hbGciOiJIUzI1.eyJ" + "pc3MiOiJzdXBh.c2lnbmF0dXJl';",
                 "JWT-shaped string"),
    "notes.md": ("const k = 'SUPABASE_SERVICE" + "_ROLE_KEY = \"abc\"';",
                 "service-role credential assignment"),
}


def write_files(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    return list(files)


def test_secret_scan_flags_each_pattern(tmp_path):
    paths = write_files(tmp_path, {rel: text for rel, (text, _) in LEAKS.items()})
    findings = check.scan_for_secrets(tmp_path, paths)
    assert findings == [f"{rel}:1: {label}" for rel, (_, label) in LEAKS.items()]


def test_secret_scan_skips_examples_archive_and_binaries(tmp_path):
    leak = LEAKS["wifi.ino"][0]
    paths = write_files(tmp_path, {
        "Embedded/camera_node/secrets.h.example": leak,
        "archive/pre-refactor/old.ino": leak,
    })
    (tmp_path / "image.jpg").write_bytes(b"\xff\xd8\0" + leak.encode())
    assert check.scan_for_secrets(tmp_path, paths + ["image.jpg"]) == []


def test_secret_scan_passes_config_that_reads_from_secrets_h(tmp_path):
    paths = write_files(tmp_path, {
        "a.ino": '#include "secrets.h"\nWiFi.begin(WIFI_SSID, WIFI_PASS);',
        "b.py": 'DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")',
        "c.dart": "final String bucketName = AppConfig.environment.pondImageBucket;",
        "schema.sql": 'GRANT ALL ON TABLE pond_data TO "service_role";',
    })
    assert check.scan_for_secrets(tmp_path, paths) == []


def test_secret_step_fails_on_a_finding(monkeypatch, tmp_path):
    paths = write_files(tmp_path, {"leak.ino": LEAKS["token.ino"][0]})
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check, "tracked_files", lambda root: paths)
    r = check.Runner(strict=False)
    check.check_secrets(r)
    assert r.results[0].status == "FAIL"
    assert r.results[0].detail == "1 finding(s)"


def test_secret_step_skips_without_git(monkeypatch):
    monkeypatch.setattr(check.shutil, "which", lambda name: None)
    r = check.Runner(strict=False)
    check.check_secrets(r)
    assert r.results[0].status == "SKIP"
    assert r.results[0].detail == "git not found"


@pytest.mark.parametrize("sketch", [s for s, _ in check.SKETCHES])
def test_networked_sketches_take_credentials_from_secrets_h(sketch):
    ino = (check.EMBEDDED / sketch / f"{sketch}.ino").read_text(encoding="utf-8")
    example = check.EMBEDDED / sketch / "secrets.h.example"
    if "WIFI_PASS" not in ino and "SUPABASE_URL" not in ino:
        assert not example.exists()
        return
    assert example.exists()
    assert '#if __has_include("secrets.h")' in ino
    assert f"#error \"{sketch}: secrets.h not found. Copy Embedded/{sketch}/secrets.h.example" in ino
    assert not re.search(r"(WIFI_PASS|DEVICE_TOKEN|SUPABASE_URL)\s*=", ino)


def test_stage_sketch_compiles_against_the_example_not_real_secrets(monkeypatch, tmp_path):
    embedded = tmp_path / "Embedded"
    write_files(embedded, {
        "node/node.ino": '#include "secrets.h"',
        "node/secrets.h": "REAL",
        "node/secrets.h.example": "PLACEHOLDER",
        "bench/bench.ino": "void setup() {}",
    })
    monkeypatch.setattr(check, "EMBEDDED", embedded)
    monkeypatch.setattr(check, "BUILD", tmp_path / "build")

    staged = check.stage_sketch("node")
    assert (staged / "node.ino").exists()
    assert (staged / "secrets.h").read_text(encoding="utf-8").strip() == "PLACEHOLDER"

    bench = check.stage_sketch("bench")
    assert sorted(p.name for p in bench.iterdir()) == ["bench.ino"]


def test_host_test_exe_is_a_fresh_path_each_run(monkeypatch, tmp_path):
    monkeypatch.setattr(check, "BUILD", tmp_path / "build")
    first = check.host_test_exe()
    first.write_bytes(b"built")
    second = check.host_test_exe()
    assert second != first and second.parent.parent == tmp_path / "build"
    assert not second.exists()
    # The earlier run's directory is cleaned up when nothing holds it.
    assert not first.parent.exists()


def test_pytest_count_pattern():
    out = "....\n334 assertions passed\n69 passed, 2 skipped in 0.60s\n"
    assert check.last_match(check.PYTEST_COUNTS, out) == "69 passed, 2 skipped"


def test_firmware_skips_compile_when_board_core_missing(monkeypatch):
    monkeypatch.setattr(check.shutil, "which",
                        lambda name: "arduino-cli" if name == "arduino-cli" else None)
    monkeypatch.setattr(check, "installed_cores", lambda cli: set())
    r = check.Runner(strict=False)
    check.check_firmware(r)
    compiles = r.results[1:]
    assert [x.status for x in compiles] == ["SKIP"] * len(check.SKETCHES)
    assert compiles[0].detail == "arduino-cli core esp32:esp32 not found"
    assert not r.failed


def test_firmware_missing_board_core_fails_under_strict(monkeypatch):
    monkeypatch.setattr(check.shutil, "which",
                        lambda name: "arduino-cli" if name == "arduino-cli" else None)
    monkeypatch.setattr(check, "installed_cores", lambda cli: set())
    r = check.Runner(strict=True)
    check.check_firmware(r)
    assert r.failed


@pytest.mark.parametrize("payload, expected", [
    ('{"platforms": []}', set()),
    ('{"platforms": [{"id": "esp32:esp32"}]}', {"esp32:esp32"}),
    ('{"platforms": [{"metadata": {"id": "esp32:esp32"}}]}', {"esp32:esp32"}),
    ('[{"id": "esp32:esp32"}]', {"esp32:esp32"}),
    ("not json", set()),
])
def test_installed_cores_parses_core_list(monkeypatch, payload, expected):
    class Proc:
        stdout = payload
    monkeypatch.setattr(check.subprocess, "run", lambda *a, **k: Proc())
    assert check.installed_cores("arduino-cli") == expected


def test_backend_runs_the_backtest_against_its_baseline(monkeypatch):
    """Issue #33: the backend suite runs python -m koi.tools.backtest
    --check from Backend/, so a worse model metric fails it."""
    monkeypatch.setattr(check, "check_secrets", lambda r: None)
    monkeypatch.setattr(check, "python_tool", lambda module: None)
    calls = []

    def fake_run(self, name, cmd, cwd, summarise=None):
        calls.append((name, cmd, cwd, summarise))
        return self.record(check.Result(name, "PASS"))

    monkeypatch.setattr(check.Runner, "run", fake_run)
    r = check.Runner(strict=False)
    check.check_backend(r)
    (name, cmd, cwd, summarise), = [c for c in calls if c[0] == "backend: backtest"]
    assert cmd == [sys.executable, "-m", "koi.tools.backtest", "--check"]
    assert cwd == check.BACKEND
    out = "WORSE  demo_pond.x: 2\n10 of 11 metrics within baseline version 3, 1 worse\n"
    assert summarise(out) == "10 of 11 metrics within baseline version 3"
