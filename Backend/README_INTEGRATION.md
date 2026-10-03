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
| `koi/storage/base.py` | | The `Storage` interface, `StorageError`, `fail_soft` |
| `koi/storage/supabase_storage.py` | `DigitalTwin/state_store.py`, the camera's own client | `SupabaseStorage`: all Supabase I/O, client built on first use |
| `koi/storage/memory.py` | the tests' fake store | `MemoryStorage`: in-process tables, seedable from JSON |
| `koi/registry.py` | `DigitalTwin/registry.py` | One locked `PondTwin` per user, reloaded when another process saved a newer snapshot |
| `koi/api/` | `DigitalTwin/app.py` | `create_app(settings)` and the routes |
| `koi/worker/poller.py` | `DigitalTwin/poller.py` | Environmental poll, lease-gated `Worker`, pond thread pool |
| `koi/camera/` | `Camera/camera.py`, `hsvEngine.py`, `imageSchedule.py` | Camera service, `create_app(settings)` |

`koi/models/` does no I/O. The API, the worker and the camera service each
hold one `Storage` (chosen by `KOI_STORAGE`) and do all database and file
I/O through it. Every storage failure raises `StorageError`, which names
the operation; nothing in `koi/storage` logs or swallows an error. The
caller decides: the snapshot write, the chemistry log and the pond
config reads propagate, while the evaporation and algae logs, the rating
history and the camera's previous-frame read are wrapped in `fail_soft`
(log a warning, carry on with a default), which is what the old `state_store`
did for those calls.

## Settings

`koi/settings.py` reads environment variables, then `Backend/.env`
(copy `Backend/.env.example`). An environment variable wins over the same
name in `.env`. Both services read the same file.

| Variable | Default | Used by |
|---|---|---|
| `KOI_ENV` | `production` | `development` runs the poller inside `python -m koi.api` |
| `KOI_STORAGE` | `supabase` | `memory` keeps data in process (empty at start, lost on exit) |
| `SUPABASE_URL` | none | `SupabaseStorage` |
| `SUPABASE_SERVICEROLE_KEY` | none | `SupabaseStorage` (server-side only) |
| `KOI_TIMEZONE` | `Asia/Singapore` | camera daylight slots |
| `KOI_POLL_INTERVAL_MINUTES` | `15` | poller |
| `KOI_WORKER_THREADS` | `4` | poller; ponds polled at the same time |
| `KOI_HYPOXIA_WATCH_TEMP_C` | `30` | poller and API; water temperature (C) at which a dark pond's hypoxia flag is `watch` |
| `KOI_HYPOXIA_HIGH_TEMP_C` | `32` | poller and API; the same for `high`; must be above the watch level |
| `KOI_CORS_ORIGINS` | `*` | both Flask apps; comma separated |
| `POND_IMAGE_BUCKET` | `imageAnalysisBucket` | camera; must match the app's `env/*.json` |
| `DEVICE_TOKEN` | empty (uploads unauthenticated) | camera; must match `Embedded/camera_node/secrets.h` |
| `TEST_MODE` | `0` | camera; `1` = short bench-test sleep times |
| `KOI_LOG_LEVEL` | `INFO` | all three; lowest level written to the JSON log |
| `KOI_AUTH` | `required` | API; `disabled` skips sign-in and is refused unless `KOI_ENV=development` |
| `KOI_JWT_ALGORITHM` | `HS256` | API; `ES256`/`RS256` verify with `SUPABASE_JWKS_URL` |
| `SUPABASE_JWT_SECRET` | none | API, with `HS256` (server-side only) |
| `SUPABASE_JWKS_URL` | none | API, with `ES256`/`RS256` |
| `KOI_JWT_ISSUER` | `SUPABASE_URL` + `/auth/v1` | API; the token's `iss` |
| `KOI_JWT_AUDIENCE` | `authenticated` | API; the token's `aud` |

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
API never starts it (`create_app` never does); run `python -m koi.worker` as
its own process.

On a host with gunicorn (Linux), serve the API with two worker processes:

```
pip install -e ".[wsgi]"
gunicorn -c gunicorn.conf.py   # koi.api:create_app(), -w 2, port 8080
python -m koi.worker           # separately, one per deployment
```

### Several processes, one pond state

Each API worker process and the poller worker keep their own in-memory
twins. They stay consistent through the stored snapshot:

- `pond_chemistry_state.snapshot_version` goes up by one on every save.
  Before each access the registry compares it with the version it holds
  and reloads the pond if the stored one is newer, so the API serves what
  the worker last saved and the worker polls from the API's latest event.
- A save names the version it started from. If another process saved in
  between, storage rejects it (`StaleSnapshotError`); the registry reloads
  and runs the change once more on the fresh state. A second rejection is
  returned to the caller as an error.
- Only one poller worker polls at a time. Each cycle it takes or renews the
  `poller` row in `worker_lease` for two poll intervals. Another
  `python -m koi.worker` finds the lease held, logs
  `standby, another worker holds the poller lease` and polls nothing; it
  takes over within two intervals if the active worker dies, or at the
  next cycle if it is stopped with Ctrl+C.
- Within the active worker, ponds are polled `KOI_WORKER_THREADS` at a
  time, each under its own pond lock.

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
   `Backend/` as the working directory. PythonAnywhere web apps run one
   process each, so no gunicorn step is needed there.
5. Reload the web apps.

Database: apply `supabase/migrations/0002_worker_lease.sql` before
deploying this version: the snapshot reads and writes and the worker
lease need its column, table and functions. Apply the rest of
`supabase/migrations/` before or after the restart. The
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

Sign-in (`koi/api/auth.py`, issue #11): every route below except
`/health`, `/ready` and `/metrics` needs `Authorization: Bearer <access
token>`, the Supabase session token the app holds. The token must verify
(configured algorithm only, signature, `iss`, `aud`, `exp`, with `sub`
and `session_id`) and its session must still exist in `auth.sessions`
(`auth_session_active`, migration 0007), so signing out or revoking a
session stops its token early. Otherwise 401 with a `WWW-Authenticate`
header (codes `auth_required`, `invalid_token`, `token_expired`,
`session_revoked`). The `<uid>` in the path or the `user_id` in the body
must be the pond whose `UserData.auth_uid` is the token's `sub`; any other
pond, or an account with no pond linked, is 403 (`pond_forbidden`,
`pond_not_linked`). Knowing a pond's id gives no access.

Ponds are linked to accounts only by the owner, with
`link_pond_to_account(pond_id, account_uuid)` in the Supabase SQL editor
after confirming outside the app that the account holder keeps that pond
(the process #49 reuses). A trigger refuses any change of `auth_uid` made
with the app's keys, and the backend never writes it. One pond per
account for now; #67 lifts that.

`KOI_AUTH=disabled` restores the old behaviour (trust `user_id`) for local
development only; the API refuses to start with it in any other
`KOI_ENV`.

Cheap cached reads (dashboard cards + alert badges):
- `GET /assessment/<uid>` — chemistry
- `GET /assessment/evaporation/<uid>`
- `GET /assessment/algae/<uid>`
- `GET /assessment/all/<uid>` — all three in one call, plus `hypoxia`: the night-time
  low-oxygen flag (`level` none/watch/high/unknown, `explanation`, `advice`) from the
  latest reading, the profile's aeration and the cached algae assessment
  (`koi/models/hypoxia.py`)

Live projections (detail graph screens):
- `GET /forecast/<uid>?horizon_days=21` — chemistry
- `GET /forecast/evaporation/<uid>?horizon_days=14` — uses the depth in
  the pond profile, or 1.2 m when none is stored; `&depth_m=0.9` replaces
  it for that one projection

Pond profile (volume, depth, fish, tap water, aeration; effective-dated):
- `GET /v1/ponds/<uid>/profile` — the profile in force now and every stored row
- `PUT /v1/ponds/<uid>/profile` — `{volume_l, biomass_g, depth_m?, fish_type?,
  fish_count?, tap_tds_ppm?, tap_nitrate_ppm?, aeration?, effective_from?}`;
  adds a row in force from `effective_from` (default now, UTC when no offset)
- `GET /forecast/algae/<uid>?horizon_days=21`

Algae severity ratings:
- `POST /events/algae-rating` — `{user_id, severity, image_id, green_ratio}`
  where severity is `none|minor|moderate|severe|obstruction`
- `POST /events/algae-rating/undo` — `{user_id, rating_id}`
- `GET /ratings/algae/<uid>` — history + calibration state + latest frame

Every `/events/*` response now returns all three domain assessments, so
the app can refresh every card from one response.

Operations (`koi/api/health.py`, `koi/observability.py`):
- `GET /health` — liveness only; 200 while the process answers.
- `GET /ready` — 200 when storage answers and the poller's last successful
  cycle finished within two poll intervals; otherwise 503 with the error
  envelope (code `not_ready`). Both report the last success time and age,
  the ponds that failed or were skipped in the last cycle and the lease
  holder. The worker writes this report to `worker_status` (migration
  0003) after every cycle, so it works with the poller in its own process.
- `GET /metrics` — Prometheus text: request counts and latency by route
  pattern, last poll cycle duration, per-pond failures, last-success age,
  newest sensor reading age and snapshot age per pond. The camera service
  serves its own `/metrics`: uploads by result, analysis time and state
  transitions.

Logs are one JSON object per line on stderr with `time`, `level`,
`service`, `pond_id`, `request_id` and `event`. A request's id comes from
its `X-Request-ID` header (or is generated) and is returned in the
response's `X-Request-ID`. `print` is not allowed in `koi/` (ruff T20).

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
| `worker/test_poller_integration.py` | 97 | real poller + all endpoints, storage failures |
| `worker/test_worker_lease.py` | | worker lease, pond thread pool, snapshot versions, API and worker registries staying consistent |
| `models/test_severity_ratings.py` | 63 | rating assimilation |
| `models/test_new_engines.py` | 49 | engine physics |
| `api/test_contract.py` | 25 | Python <-> Dart JSON contract |
| `models/test_projection.py` | 29 | chemistry lookahead scenarios |
| `models/test_algae_history_cache.py` | 7 | algae history cache |
| `models/test_daily_retention.py` | 9 | daily snapshot retention |
| `storage/test_schema.py` | | code against `supabase/migrations/` |
| `storage/test_memory_storage.py` | | `MemoryStorage` behaves like the tables |
| `storage/test_supabase_storage.py` | | `SupabaseStorage` queries and error wrapping, fake client |
| `api/test_app_factory.py` | | app factory, CORS, entry points, poller gating |
| `camera/test_camera_app.py` | | camera upload pipeline against `MemoryStorage` |
| `test_settings.py` | | settings defaults, variable names, validation |

The run ends with an "N assertions passed" line from `conftest.py`.
All run offline: `conftest.py` stubs supabase and apscheduler before any
test module is imported, builds `Settings` with `_env_file=None` so a real
`Backend/.env` is never read, and runs the services on `MemoryStorage`
seeded from `tests/fixtures/pond_455.json` (passed to `create_app`, not
patched in). The poller integration tests
walk one pond through a sequence of events and must run in file order,
which is pytest's default. `test_contract.py` reads
`MobileUI/mobile_app/lib/utils/digital_twin_api.dart` relative to its own
location, so it works from any working directory.

## Still open

- **Direct database access.** The API checks the caller's pond (above),
  but until #12 the app's anon key can still call
  `get_bundled_dashboard_payload`, `get_historical_graph_payload`,
  `get_pond_telemetry_history` and `aggregate_daily_sensor_data` and read
  the tables for any pond id. See migration 0007's header.
- **ESP32 `user_id` mismatch.** The camera firmware hardcoded `15`; pond data
  is `455`. Algae stays `no-camera` for 455 until reconciled.
- Both new models are uncalibrated against ground truth.
