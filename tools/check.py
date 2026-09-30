"""Single entry point for every check in the repository.

Usage, from anywhere:
    python tools/check.py backend|firmware|mobile|all [--strict]

Each step prints PASS, FAIL or SKIP with its run time. A step whose tool is
not installed prints "SKIP <step> (<tool> not found)" and does not fail the
run unless --strict is given (CI always passes --strict). The exit code is
non-zero if any step failed.

No step contacts a live service: the backend tests stub Supabase, and the
firmware and mobile checks build and test locally.
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


def last_match(pattern: str, output: str) -> str:
    matches = re.findall(pattern, output)
    return matches[-1] if matches else ""


# ---------------------------------------------------------------------
# Suites
# ---------------------------------------------------------------------
def check_backend(r: Runner) -> None:
    ruff = python_tool("ruff")
    if ruff:
        r.run("backend: ruff", ruff + ["check", "--config", "Backend/pyproject.toml",
                                       "Backend", "tools"], ROOT)
    else:
        r.skip("backend: ruff", "ruff")

    mypy = python_tool("mypy")
    if mypy:
        r.run("backend: mypy", mypy + ["DigitalTwin", "Camera"], BACKEND,
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
                     str(EMBEDDED / sketch)], ROOT)


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
