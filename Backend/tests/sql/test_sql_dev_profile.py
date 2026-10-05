"""Issue #14: the dev stack's local database profile end to end.

`python -m koi.dev --profile supabase --check` writes the demo seed (two
ponds, a row with no pond, a pond with pH but no TDS, two pH rows at the
same instant, the camera's old plain-text state, the weather cache slots,
station metadata and a snapshot from before snapshot_version) into the
local Supabase stack, checks every migration is applied, fast-forwards
the worker through SupabaseStorage, compares each pond's dashboard with
the memory profile's on the same seed, serves the API, worker and camera
service over the local REST API, validates every response against the
OpenAPI document, and removes the seeded rows.

It runs in a subprocess because these tests replace the supabase client
with a stub. Local stack only (the command refuses anything else);
skipped when none is running, as in CI's backend job.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


def test_dev_stack_local_database_profile_passes_its_check():
    env = {k: v for k, v in os.environ.items() if not k.startswith("KOI_DEV_")}
    proc = subprocess.run([sys.executable, "-m", "koi.dev", "--profile", "supabase", "--check"], cwd=BACKEND,
                          env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)
    if proc.returncode == 2 and proc.stdout.startswith("SKIP"):
        pytest.skip(proc.stdout.strip())
    assert proc.returncode == 0, proc.stdout + proc.stderr[-2000:]
    out = proc.stdout
    assert "PASS local database at every migration" in out
    assert "PASS pond 1 dashboard: local database equals memory profile" in out
    assert "PASS pond 2 dashboard: local database equals memory profile" in out
    assert "PASS GET /v1/ponds/2/dashboard (200, matches the OpenAPI document)" in out
    assert "FAIL" not in out
