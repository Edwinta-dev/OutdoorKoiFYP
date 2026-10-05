"""Tests for tools/update_goldens.py. Run with: python -m pytest tools"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_goldens  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_reads_the_flutter_version_ci_pins():
    workflow = """
      - uses: subosito/flutter-action@v2
        with:
          channel: stable
          # a comment
          flutter-version: 3.47.6
          cache: true
"""
    assert update_goldens.ci_flutter_version(workflow) == "3.47.6"


def test_the_real_workflow_pins_a_version():
    # CI and the golden recorder must render with the same Flutter.
    version = update_goldens.ci_flutter_version((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert version.count(".") == 2


def test_unpinned_workflow_is_refused():
    with pytest.raises(ValueError):
        update_goldens.ci_flutter_version("- uses: subosito/flutter-action@v2\n  with:\n    channel: stable\n")


def test_check_mode_records_nothing():
    script = update_goldens.container_script(update=False)
    assert "--update-goldens" not in script and "/out" not in script
    assert "flutter test test/goldens_test.dart" in script


def test_update_mode_works_on_a_copy_without_build_outputs():
    script = update_goldens.container_script(update=True)
    assert "--update-goldens" in script and "cp test/goldens/*.png /out/" in script
    # The app is mounted read-only at /src; the run happens in /work.
    assert "--exclude=./build" in script and "--exclude=./.dart_tool" in script and "cd /work" in script
