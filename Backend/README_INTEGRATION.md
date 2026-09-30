# Backend — the `koi` package

The digital twin models all three pond outcome domains (chemistry,
evaporation, algae). The backend is one Python package, `koi`, under
`Backend/koi/`; the Flutter client it serves is `MobileUI/mobile_app/`. The
flat-script version it replaced is kept in `archive/pre-refactor/`.

## Layout

| Module | Formerly | Role |
|---|---|---|
| `koi/settings.py` | `os.environ` reads in each script | Every environment value in one typed `Settings` object |
| `koi/models/engine.py` | `DigitalTwin/engine.py` | Chemistry engine |
| `koi/models/evaporation_engine.py` | `DigitalTwin/evaporation_engine.py` | Evaporation engine |
| `koi/models/algae_engine.py` | `DigitalTwin/algae_engine.py` | Algae engine, camera assimilation |
| `koi/models/pond_twin.py` | `DigitalTwin/pond_twin.py` | Aggregate holding all three engines per user |
| `koi/models/forecast_utils.py` | `DigitalTwin/forecast_utils.py` | NEA payload parsing |
| `koi/storage/client.py` | two separate `create_client` calls | The one Supabase client, built on first use |
| `koi/storage/state_store.py` | `DigitalTwin/state_store.py` | All digital twin Supabase I/O |
| `koi/registry.py` | `DigitalTwin/registry.py` | One locked `PondTwin` per user |
| `koi/api/` | `DigitalTwin/app.py` | `create_app(settings)` and the routes |
| `koi/worker/poller.py` | `DigitalTwin/poller.py` | Environmental poll |
| `koi/camera/` | `Camera/camera.py`, `hsvEngine.py`, `imageSchedule.py` | Camera service, `create_app(settings)` |

`koi/models/` does no I/O. Everything that talks to Supabase goes through
`koi/storage/client.py`.

## Settings

`koi/settings.py` reads environment variables, then `Backend/.env`
(copy `Backend/.env.example`). An environment variable wins over the same
name in `.env`. Both services read the same file.

| Variable | Default | Used by |
|---|---|---|
| `KOI_ENV` | `production` | `development` runs the poller inside `python -m koi.api` |
| `SUPABASE_URL` | none | storage client |
| `SUPABASE_SERVICEROLE_KEY` | none | storage client (server-side only) |
| `KOI_TIMEZONE` | `Asia/Singapore` | camera daylight slots |
| `KOI_POLL_INTERVAL_MINUTES` | `15` | poller |
| `KOI_CORS_ORIGINS` | `*` | both Flask apps; comma separated |
| `POND_IMAGE_BUCKET` | `imageAnalysisBucket` | camera; must match the app's `env/*.json` |
| `DEVICE_TOKEN` | empty (uploads unauthenticated) | camera; must match `Embedded/camera_node/secrets.h` |
| `TEST_MODE` | `0` | camera; `1` = short bench-test sleep times |

The old camera service loaded its `.env` with `override=True`, so the file
beat the environment. Settings use the usual order instead: the
environment wins. On PythonAnywhere nothing else sets these names, so the
result is the same.

## Running

From `Backend/`, after `pip install -e ".[dev]"`:

```
python -m koi.api      # digital twin API, port 8080
python -m koi.worker   # poller, in the foreground
python -m koi.camera   # camera service, port 5000
```

With `KOI_ENV=development`, `python -m koi.api` also starts the poller on a
background thread, as `python app.py` used to. With any other `KOI_ENV` the
API never starts it; run `python -m koi.worker` as its own process.

## Dependencies

`pyproject.toml` lists direct dependencies with lower bounds.
`requirements.lock` pins the full set for Python 3.11 on Linux; regenerate
it with the command at its top after changing `pyproject.toml`.
`requirements.txt` installs the lock plus the package and is what
PythonAnywhere uses.

## Deploying on PythonAnywhere

1. Pull the repository, then from `Backend/`:
   `pip install --user -r requirements.txt` (or, in the web app's
   virtualenv, without `--user`).
2. Create `Backend/.env` from `.env.example` and set `KOI_ENV=production`.
3. Point each web app's WSGI file at its factory. Camera service:

   ```python
   import sys
   path = "/home/<username>/OutdoorKoiFYP/Backend"
   if path not in sys.path:
       sys.path.insert(0, path)

   from koi.camera import create_app
   application = create_app()
   ```

   Digital twin API: the same, with `from koi.api import create_app`.
4. Run the poller as an always-on task: `python -m koi.worker`, with
   `Backend/` as the working directory.
5. Reload the web apps.

Database: apply `supabase/migrations/` before or after the restart. The
evaluation pushes are fail-soft, so the service runs without the
evaluation tables; you lose the cached `/assessment/*` history until they
exist. Existing `pond_chemistry_state` rows load unchanged —
`PondTwin.from_snapshot` detects the legacy bare-chemistry shape and wraps
it, preserving accumulated nitrogen state.

## State update triggers

Three, all through the same locked+persisted path:

**Environmental poll** (`koi/worker/poller.py`, every
`KOI_POLL_INTERVAL_MINUTES`, default 15) — pulls sensors, NEA
telemetry/forecast, and any new camera frames; advances all three engines
to now; pushes one evaluation row per domain.

**Logged intervention** (`/events/*`) — fans out to every affected engine,
then immediately re-assesses and re-projects:

| Event | chemistry | evaporation | algae |
|---|---|---|---|
| `FEEDING` | +TAN | — | — |
| `WATER_TOPUP` | TDS dilution | **loss reset** | — |
| `WATER_CHANGE` | dilute TAN/NO2, blend NO3 | **loss reset** | dilute suspended |
| `ALGAE_SCRUB` | suppress NO3 uptake 5d | — | **level reset** |

**Human severity rating** (`/events/algae-rating`) — the user rates the latest
camera frame. Corrects the algae engine's level at `HUMAN_TRUST = 0.85`
(above the camera's `0.70`), and the accumulated (label, green_ratio) pairs
calibrate the alert thresholds. `obstruction` retroactively removes the
frame from the growth fit. Exactly reversible via undo.

## Endpoints

Cheap cached reads (dashboard cards + alert badges):
- `GET /assessment/<uid>` — chemistry
- `GET /assessment/evaporation/<uid>`
- `GET /assessment/algae/<uid>`
- `GET /assessment/all/<uid>` — all three in one call

Live projections (detail graph screens):
- `GET /forecast/<uid>?horizon_days=21` — chemistry
- `GET /forecast/evaporation/<uid>?horizon_days=14&depth_m=1.2`
- `GET /forecast/algae/<uid>?horizon_days=21`

Algae severity ratings:
- `POST /events/algae-rating` — `{user_id, severity, image_id, green_ratio}`
  where severity is `none|minor|moderate|severe|obstruction`
- `POST /events/algae-rating/undo` — `{user_id, rating_id}`
- `GET /ratings/algae/<uid>` — history + calibration state + latest frame

Every `/events/*` response now returns all three domain assessments, so
the app can refresh every card from one response.

## Tests

Run from `Backend/` (settings in `Backend/pytest.ini`):

```
python -m pytest -q                                    # whole suite
python -m pytest -q tests/models/test_pond_twin.py     # one file
```

Tests live in `Backend/tests/`, mirroring the package.

| File | Asserts | Covers |
|---|---|---|
| `models/test_pond_twin.py` | 61 | state resets, snapshots |
| `worker/test_poller_integration.py` | 91 | real poller + all endpoints |
| `models/test_severity_ratings.py` | 63 | rating assimilation |
| `models/test_new_engines.py` | 49 | engine physics |
| `api/test_contract.py` | 25 | Python <-> Dart JSON contract |
| `models/test_projection.py` | 29 | chemistry lookahead scenarios |
| `models/test_algae_history_cache.py` | 7 | algae history cache |
| `models/test_daily_retention.py` | 9 | daily snapshot retention |
| `storage/test_schema.py` | | code against `supabase/migrations/` |
| `api/test_app_factory.py` | | app factory, CORS, entry points, poller gating |
| `camera/test_camera_app.py` | | camera upload pipeline against a fake client |
| `test_settings.py` | | settings defaults, variable names, validation |

The run ends with an "N assertions passed" line from `conftest.py`.
All run offline: `conftest.py` stubs supabase and apscheduler before any
test module is imported, builds `Settings` with `_env_file=None` so a real
`Backend/.env` is never read, and its `fake_store` fixture replaces every
`state_store` call with an in-memory store. The poller integration tests
walk one pond through a sequence of events and must run in file order,
which is pytest's default. `test_contract.py` reads
`MobileUI/mobile_app/lib/utils/digital_twin_api.dart` relative to its own
location, so it works from any working directory.

## Still open

- **No auth.** Every endpoint trusts a caller-supplied `user_id`.
- **Single process only.** The registry lock does not span gunicorn workers.
- **ESP32 `user_id` mismatch.** The camera firmware hardcoded `15`; pond data
  is `455`. Algae stays `no-camera` for 455 until reconciled.
- Both new models are uncalibrated against ground truth.
