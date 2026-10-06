"""Record or check the mobile app's golden images on Linux (issue #48).

Usage, from the repo root, with Docker running:
    python tools/update_goldens.py            # re-record test/goldens/*.png
    python tools/update_goldens.py --check    # compare only, as CI does

The goldens in MobileUI/mobile_app/test/goldens/ are recorded and checked on
Linux only, because CI runs on Linux and Windows draws text differently by
3-6 % of pixels (test/goldens_test.dart is skipped on Windows). This runs
test/goldens_test.dart in a Linux container with the Flutter version that
.github/workflows/ci.yml pins, so the images match what CI compares against.

The container gets a read-only view of the app and works on a copy of it
(without build/ and .dart_tool/), so the only files this changes are the
PNGs under test/goldens/, and only with re-recording. Review the PNG diff
(`git diff --stat MobileUI/mobile_app/test/goldens`) before committing.

The image koi-flutter:<version> is built on first use from
tools/flutter-linux.Dockerfile (a few minutes; the image is about 4 GB) and reused
afterwards. Nothing here contacts the live project or any service.
Exit code: 0 on success, 1 when the golden tests fail, 2 on a setup problem.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "MobileUI" / "mobile_app"
GOLDENS = APP / "test" / "goldens"
CI = ROOT / ".github" / "workflows" / "ci.yml"
DOCKERFILE = Path(__file__).resolve().parent / "flutter-linux.Dockerfile"
GOLDEN_TEST = "test/goldens_test.dart"


def ci_flutter_version(workflow: str) -> str:
    """The flutter-version pinned for subosito/flutter-action in the CI
    workflow. Goldens must match the version CI renders with."""
    m = re.search(r"subosito/flutter-action@\S+\s*\n\s*with:\s*\n"  # the action and its inputs,
                  r"(?:\s+\S.*\n)*?"  # other inputs and comments,
                  r"\s+flutter-version:\s*['\"]?([0-9][0-9.]*)", workflow)  # then the pin
    if not m:
        raise ValueError("no flutter-version pinned for subosito/flutter-action in .github/workflows/ci.yml")
    return m.group(1)


def container_script(update: bool) -> str:
    """Shell run inside the container: copy the app, test, export PNGs."""
    test = f"flutter test {'--update-goldens ' if update else ''}{GOLDEN_TEST}"
    export = "&& mkdir -p /out && cp test/goldens/*.png /out/" if update else ""
    return ("set -e; mkdir -p /work && cd /src "
            "&& tar --exclude=./build --exclude=./.dart_tool -cf - . | tar -xf - -C /work "
            "&& cd /work && flutter pub get >/dev/null "
            f"&& {test} {export}")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("RUN", " ".join(cmd), flush=True)
    return subprocess.run(cmd, **kw)


def ensure_image(docker: str, version: str) -> str:
    image = f"koi-flutter:{version}"
    have = subprocess.run([docker, "image", "inspect", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if have.returncode != 0:
        print(f"Building {image} (first use; downloads the Flutter SDK)", flush=True)
        built = run([docker, "build", "-t", image, "--build-arg", f"FLUTTER_VERSION={version}",
                     "-f", str(DOCKERFILE), str(DOCKERFILE.parent)])
        if built.returncode != 0:
            raise SystemExit(2)
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="compare only; change nothing")
    args = parser.parse_args(argv)

    docker = shutil.which("docker")
    if not docker:
        print("docker not found", file=sys.stderr)
        return 2
    try:
        version = ci_flutter_version(CI.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    image = ensure_image(docker, version)

    with tempfile.TemporaryDirectory(prefix="goldens-") as out:
        cmd = [docker, "run", "--rm", "-v", f"{APP}:/src:ro"]
        if not args.check:
            cmd += ["-v", f"{out}:/out"]
        cmd += [image, "bash", "-c", container_script(update=not args.check)]
        result = run(cmd)
        if result.returncode != 0:
            print(f"golden tests failed (Flutter {version}, Linux)", file=sys.stderr)
            return 1
        if args.check:
            print(f"goldens match (Flutter {version}, Linux)")
            return 0
        recorded = sorted(Path(out).glob("*.png"))
        GOLDENS.mkdir(exist_ok=True)
        for png in recorded:
            shutil.copyfile(png, GOLDENS / png.name)
        print(f"recorded {len(recorded)} goldens with Flutter {version} on Linux into {GOLDENS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
