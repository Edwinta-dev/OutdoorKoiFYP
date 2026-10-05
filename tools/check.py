"""Single entry point for every check in the repository.

Usage, from anywhere:
    python tools/check.py backend|firmware|mobile|all [--strict]
    python tools/check.py database [--strict]

`all` runs backend, firmware and mobile. `database` is separate because it
rebuilds the local Supabase database (Docker and `supabase start` needed):
the dump-upgrade rehearsal (tools/db_rehearsal.py, schema.sql plus the
migrations, then a fresh reset), the SQL tests in Backend/tests/sql, and
the dev stack's local database profile (python -m koi.dev --profile
supabase --check). Without a running local stack each step is SKIP, and
the database gate stays open until it runs.

Each step prints PASS, FAIL or SKIP with its run time. A step whose tool is
not installed (including an arduino-cli board core) prints
"SKIP <step> (<tool> not found)" and does not fail the
run unless --strict is given (CI always passes --strict). The exit code is
non-zero if any step failed.

No step contacts a live service: the backend tests stub Supabase, and the
firmware and mobile checks build and test locally.

The backend suite also scans every tracked file for committed credentials
(see SECRET_PATTERNS). The firmware compiles use each sketch's
secrets.h.example, never a developer's real secrets.h.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "Backend"
EMBEDDED = ROOT / "Embedded"
MOBILE = ROOT / "MobileUI" / "mobile_app"
BUILD = ROOT / "build"

# The local Supabase stack's database container (project_id in
# supabase/config.toml), as tools/db_rehearsal.py names it.
LOCAL_DB_CONTAINER = "supabase_db_OutdoorKoiFYP"

# Sketch folder -> arduino-cli board (FQBN).
SKETCHES = [
    ("sensor_bench", "esp32:esp32:esp32"),
    ("sensor_node", "esp32:esp32:esp32"),
    ("camera_node", "esp32:esp32:esp32cam"),
]


# Committed-credential scan: (label, pattern). Written so that this file
# does not match its own patterns.
SECRET_PATTERNS = [
    ("Wi-Fi password assignment",
     re.compile(r'(?i)\b\w*(?:wifi_?pass|password)\w*\s*=\s*"[^"]+"')),
    ("device token literal", re.compile(r'DEVICE_TOKEN\s*=\s*"')),
    ("JWT-shaped string",
     re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("service-role credential assignment",
     re.compile(r'''(?i)\b(?:supabase[_\s-]*)?service[_\s-]+role[_\s-]*(?:key|token)\s*[:=]\s*["'][^"']+["']''')),
]
# Template files may hold placeholders. archive/ is frozen superseded code:
# its real credential values were redacted (and are rotated), but it keeps
# placeholder device-token lines that the patterns would still flag.
SECRET_SCAN_SKIP_SUFFIXES = (".example",)
SECRET_SCAN_SKIP_PREFIXES = ("archive/",)


@dataclass
class Result:
    name: str
    status: str  # PASS, FAIL or SKIP
    seconds: float = 0.0
    detail: str = ""

    def line(self) -> str:
        extra = f" ({self.detail})" if self.detail else ""
        timing = "" if self.status == "SKIP" else f" [{self.seconds:.1f}s]"
        return f"{self.status} {self.name}{extra}{timing}"


class Runner:
    def __init__(self, strict: bool):
        self.strict = strict
        self.results: list[Result] = []

    def record(self, result: Result) -> Result:
        self.results.append(result)
        print(result.line(), flush=True)
        return result

    def skip(self, name: str, tool: str) -> Result:
        """A step whose tool is missing: SKIP, or FAIL under --strict."""
        status = "FAIL" if self.strict else "SKIP"
        return self.record(Result(name, status, detail=f"{tool} not found"))

    def run(self, name: str, cmd: list[str], cwd: Path,
            summarise: Optional[Callable[[str], str]] = None) -> Result:
        """Runs cmd in cwd, echoes its output, and records PASS or FAIL
        from the exit code. summarise(output) supplies the detail text."""
        print(f"\n==> {name}: {' '.join(str(c) for c in cmd)}", flush=True)
        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  encoding="utf-8", errors="replace")
        except OSError as exc:
            return self.record(Result(name, "FAIL", time.monotonic() - start, str(exc)))
        elapsed = time.monotonic() - start
        output = proc.stdout or ""
        if output:
            print(output.rstrip(), flush=True)
        detail = summarise(output) if summarise else ""
        if proc.returncode != 0:
            detail = f"exit {proc.returncode}" + (f", {detail}" if detail else "")
        status = "PASS" if proc.returncode == 0 else "FAIL"
        return self.record(Result(name, status, elapsed, detail))

    @property
    def failed(self) -> bool:
        return any(r.status == "FAIL" for r in self.results)


# ---------------------------------------------------------------------
# Tool lookup
# ---------------------------------------------------------------------
def python_tool(module: str) -> Optional[list[str]]:
    """Command prefix for a Python tool: the current interpreter's module
    if installed there, otherwise an executable on PATH."""
    if importlib.util.find_spec(module) is not None:
        return [sys.executable, "-m", module]
    exe = shutil.which(module)
    return [exe] if exe else None


PYTEST_COUNTS = r"\d+ passed(?:, \d+ \w+)*"


def tracked_files(root: Path) -> Optional[list[str]]:
    """Repo-relative paths git tracks, plus new files it would track (not
    gitignored), or None if git is unavailable."""
    git = shutil.which("git")
    if not git:
        return None
    proc = subprocess.run([git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                          cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


def scan_for_secrets(root: Path, paths: list[str]) -> list[str]:
    """Returns one "path:line: label" finding per matching line, skipping
    templates, archive/ and binary files."""
    findings = []
    for rel in paths:
        if rel.endswith(SECRET_SCAN_SKIP_SUFFIXES) or rel.startswith(SECRET_SCAN_SKIP_PREFIXES):
            continue
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        for lineno, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{rel}:{lineno}: {label}")
    return findings


def last_match(pattern: str, output: str) -> str:
    matches = re.findall(pattern, output)
    return matches[-1] if matches else ""


# ---------------------------------------------------------------------
# Suites
# ---------------------------------------------------------------------
def check_secrets(r: Runner) -> None:
    name = "backend: secret scan"
    start = time.monotonic()
    paths = tracked_files(ROOT)
    if paths is None:
        r.skip(name, "git")
        return
    findings = scan_for_secrets(ROOT, paths)
    for finding in findings:
        print(f"  {finding}", flush=True)
    status = "FAIL" if findings else "PASS"
    detail = f"{len(findings)} finding(s)" if findings else f"{len(paths)} files"
    r.record(Result(name, status, time.monotonic() - start, detail))


def check_backend(r: Runner) -> None:
    check_secrets(r)

    ruff = python_tool("ruff")
    if ruff:
        r.run("backend: ruff", ruff + ["check", "--config", "Backend/pyproject.toml",
                                       "Backend", "tools"], ROOT)
    else:
        r.skip("backend: ruff", "ruff")

    mypy = python_tool("mypy")
    if mypy:
        r.run("backend: mypy", mypy + ["koi", "tests"], BACKEND,
              summarise=lambda out: last_match(r"(Found \d+ errors?|Success)", out))
    else:
        r.skip("backend: mypy", "mypy")

    pytest = python_tool("pytest")
    if pytest:
        r.run("backend: pytest", pytest + ["-q"], BACKEND,
              summarise=lambda out: ", ".join(filter(None, [
                  last_match(PYTEST_COUNTS, out),
                  last_match(r"\d+ assertions passed", out)])))
        r.run("tools: pytest", pytest + ["-q", "-p", "no:cacheprovider", "tools"], ROOT,
              summarise=lambda out: last_match(PYTEST_COUNTS, out))
    else:
        r.skip("backend: pytest", "pytest")
        r.skip("tools: pytest", "pytest")


def host_test_exe() -> Path:
    """A fresh output path for the firmware host-test binary. On Windows a
    binary that has just run can stay open for a moment, so linking over
    the previous one failed now and then ("cannot open output file:
    Permission denied"). Each run builds into its own directory; earlier
    ones are removed when nothing holds them any more."""
    BUILD.mkdir(exist_ok=True)
    for old in BUILD.glob("fw_tests-*"):
        shutil.rmtree(old, ignore_errors=True)
    out = Path(tempfile.mkdtemp(prefix="fw_tests-", dir=BUILD))
    return out / ("fw_tests.exe" if sys.platform == "win32" else "fw_tests")


def check_firmware(r: Runner) -> None:
    gxx = shutil.which("g++")
    if gxx:
        exe = host_test_exe()
        src = EMBEDDED / "tests" / "test_all.cpp"
        inc = EMBEDDED / "libraries" / "koi_sensing" / "src"
        build = r.run("firmware: host tests build",
                      [gxx, "-std=c++17", "-Wall", "-I", str(inc), str(src), "-o", str(exe)], ROOT)
        if build.status == "PASS":
            r.run("firmware: host tests", [str(exe)], ROOT,
                  summarise=lambda out: last_match(r"\d+ passed, \d+ failed", out))
        else:
            r.record(Result("firmware: host tests", "FAIL", detail="build failed"))
    else:
        r.skip("firmware: host tests", "g++")

    cli = shutil.which("arduino-cli")
    cores = installed_cores(cli) if cli else set()
    for sketch, fqbn in SKETCHES:
        name = f"firmware: compile {sketch} ({fqbn})"
        if not cli:
            r.skip(name, "arduino-cli")
            continue
        core = ":".join(fqbn.split(":")[:2])
        if core not in cores:
            r.skip(name, f"arduino-cli core {core}")
            continue
        r.run(name, [cli, "compile", "--fqbn", fqbn,
                     "--libraries", str(EMBEDDED / "libraries"),
                     "--build-path", str(BUILD / "arduino" / sketch),
                     str(stage_sketch(sketch))], ROOT)


def installed_cores(cli: str) -> set[str]:
    """Platform ids (e.g. "esp32:esp32") that arduino-cli has installed.
    A board whose core is missing is treated like a missing tool."""
    try:
        proc = subprocess.run([cli, "core", "list", "--format", "json"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, encoding="utf-8", errors="replace")
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError):
        return set()
    platforms = data.get("platforms", []) if isinstance(data, dict) else data
    ids = set()
    for p in platforms or []:
        if isinstance(p, dict):
            pid = p.get("id") or (p.get("metadata") or {}).get("id")
            if pid:
                ids.add(pid)
    return ids


def stage_sketch(sketch: str) -> Path:
    """Copies a sketch into build/sketches/ for the check compile. A sketch
    that ships secrets.h.example gets that file as its secrets.h, so the
    check never reads a developer's real credentials and CI (which has no
    secrets.h) still compiles."""
    src = EMBEDDED / sketch
    dest = BUILD / "sketches" / sketch
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("secrets.h"))
    example = src / "secrets.h.example"
    if example.exists():
        shutil.copyfile(example, dest / "secrets.h")
    return dest


def check_mobile(r: Runner) -> None:
    flutter = shutil.which("flutter")
    # Existing informational lints are advisory; warnings and errors remain fatal.
    steps = ["pub get", "analyze --no-fatal-infos", "test"]
    if not flutter:
        for step in steps:
            r.skip(f"mobile: flutter {step}", "flutter")
        return
    for step in steps:
        result = r.run(f"mobile: flutter {step}", [flutter, *step.split()], MOBILE)
        if step == "pub get" and result.status == "FAIL":
            return


def local_stack_running() -> bool:
    """True when the local Supabase database container is up."""
    docker = shutil.which("docker")
    if not docker:
        return False
    proc = subprocess.run([docker, "ps", "--format", "{{.Names}}"], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    return proc.returncode == 0 and LOCAL_DB_CONTAINER in proc.stdout.split()


def check_database(r: Runner) -> None:
    steps = ["database: migration rehearsal", "database: SQL tests", "database: dev stack, local database profile"]
    pytest = python_tool("pytest")
    for tool, found in (("docker", shutil.which("docker")), ("supabase", shutil.which("supabase")),
                        ("pytest", pytest)):
        if not found:
            for step in steps:
                r.skip(step, tool)
            return
    if not local_stack_running():
        for step in steps:
            r.skip(step, "local Supabase stack (run `supabase start`)")
        return
    assert pytest is not None
    r.run(steps[0], [sys.executable, str(ROOT / "tools" / "db_rehearsal.py")], ROOT)
    r.run(steps[1], pytest + ["-q", "tests/sql"], BACKEND,
          summarise=lambda out: last_match(PYTEST_COUNTS, out))
    r.run(steps[2], [sys.executable, "-m", "koi.dev", "--profile", "supabase", "--check"], BACKEND,
          summarise=lambda out: last_match(r"\d+ passed, \d+ failed", out))


SUITES: dict[str, Callable[[Runner], None]] = {
    "backend": check_backend,
    "firmware": check_firmware,
    "mobile": check_mobile,
}
# Run only when named: not part of `all`.
SEPARATE_SUITES: dict[str, Callable[[Runner], None]] = {
    "database": check_database,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the repository checks.")
    parser.add_argument("suite", choices=[*SUITES, *SEPARATE_SUITES, "all"])
    parser.add_argument("--strict", action="store_true",
                        help="treat a missing tool as a failure (used by CI)")
    args = parser.parse_args(argv)

    # Tool output (flutter's box drawing, for one) can hold characters a
    # Windows console code page cannot encode; print them as "?" instead
    # of crashing the run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    runner = Runner(strict=args.strict)
    suites = {**SUITES, **SEPARATE_SUITES}
    for name in (SUITES if args.suite == "all" else [args.suite]):
        suites[name](runner)

    print("\nSummary")
    for result in runner.results:
        print(f"  {result.line()}")
    return 1 if runner.failed else 0


if __name__ == "__main__":
    sys.exit(main())
