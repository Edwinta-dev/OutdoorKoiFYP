"""Single entry point for every check in the repository.

Usage, from anywhere:
    python tools/check.py backend|firmware|mobile|all [--strict]

Each step prints PASS, FAIL or SKIP with its run time. A step whose tool is
not installed prints "SKIP <step> (<tool> not found)" and does not fail the
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
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "Backend"
EMBEDDED = ROOT / "Embedded"
MOBILE = ROOT / "MobileUI" / "mobile_app"
BUILD = ROOT / "build"

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


def check_firmware(r: Runner) -> None:
    gxx = shutil.which("g++")
    if gxx:
        BUILD.mkdir(exist_ok=True)
        exe = BUILD / ("fw_tests.exe" if sys.platform == "win32" else "fw_tests")
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
    for sketch, fqbn in SKETCHES:
        name = f"firmware: compile {sketch} ({fqbn})"
        if not cli:
            r.skip(name, "arduino-cli")
            continue
        r.run(name, [cli, "compile", "--fqbn", fqbn,
                     "--libraries", str(EMBEDDED / "libraries"),
                     "--build-path", str(BUILD / "arduino" / sketch),
                     str(stage_sketch(sketch))], ROOT)


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
    steps = ["pub get", "analyze", "test"]
    if not flutter:
        for step in steps:
            r.skip(f"mobile: flutter {step}", "flutter")
        return
    for step in steps:
        result = r.run(f"mobile: flutter {step}", [flutter, *step.split()], MOBILE)
        if step == "pub get" and result.status == "FAIL":
            return


SUITES: dict[str, Callable[[Runner], None]] = {
    "backend": check_backend,
    "firmware": check_firmware,
    "mobile": check_mobile,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the repository checks.")
    parser.add_argument("suite", choices=[*SUITES, "all"])
    parser.add_argument("--strict", action="store_true",
                        help="treat a missing tool as a failure (used by CI)")
    args = parser.parse_args(argv)

    runner = Runner(strict=args.strict)
    for name in (SUITES if args.suite == "all" else [args.suite]):
        SUITES[name](runner)

    print("\nSummary")
    for result in runner.results:
        print(f"  {result.line()}")
    return 1 if runner.failed else 0


if __name__ == "__main__":
    sys.exit(main())
