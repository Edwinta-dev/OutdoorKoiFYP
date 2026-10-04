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
| `koi/api/` | `DigitalTwin/app.py` | `create_app(settings)` and the routes, all under `/v1` (route table in `spec.py`, response models in `responses.py`, the dashboard in `dashboard.py`); `python -m koi.api.openapi` regenerates `docs/api/openapi.yaml` |
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
| `KOI_SENSOR_CADENCE_MINUTES` | `15` | poller; expected minutes between sensor node uploads; a channel's newest reading is used while it is at most two of these old |
| `KOI_SENSOR_INGEST_OVERLAP_MINUTES` | `60` | poller; how far before the newest ingested insert time each sensor scan starts, to find rows committed out of id order |
| `KOI_SENSOR_INGEST_BATCH_ROWS` | `2000` | poller; most `SensorData` rows one pond ingests per cycle (whole uploads only); the rest wait for the next cycle |
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
| `CAMERA_DYNAMIC_ENTER_FRAMES` | `2` | camera; raised frames in a row to enter the dynamic schedule |
| `CAMERA_DYNAMIC_EXIT_FRAMES` | `3` | camera; stable frames in a row to leave it |
| `CAMERA_DYNAMIC_RATE_LEVELS` | `0.10,0.20` | camera; green-ratio rise per frame past which the dynamic interval steps from 2 h to 1 h to 30 min |
| `CAMERA_OBSTRUCTION_CONFIRM_FRAMES` | `3` | camera; consistent obstructed frames that restart the baseline (issue #80); `0` keeps the old latch |
| `CAMERA_OBSTRUCTION_TOLERANCE` | `0.05` | camera; how close (green ratio) those frames must stay to their mean |
| `NEA_API_BASE_URL` | `https://api-open.data.gov.sg/v2/real-time/api` | weather job |
| `NEA_API_KEY` | empty | weather job; optional data.gov.sg key (server-side only) |
| `NEA_MIN_REQUEST_INTERVAL_SECONDS` | `1.0` | weather job; spacing between requests |
| `NEA_MAX_ATTEMPTS` | `5` | weather job; tries per request with backoff |
| `WEATHER_HISTORY_STATIONS` | empty | weather job; extra stations to keep history for (comma separated), beside those assigned to ponds |
| `WEATHER_HISTORY_AREAS` | empty | weather job; extra 2-hour forecast areas, `*` for all |
| `SENTRY_DSN` | empty (off) | all three; error tracking turns on when set (server-side only) |
| `SENTRY_ENVIRONMENT` | `KOI_ENV` | all three |
| `SENTRY_RELEASE` | `koi@<version>+twin.<snapshot version>` | all three |

The old camera service loaded its `.env` with `override=True`, so the file
beat the environment. Settings use the usual order instead: the
environment wins. On PythonAnywhere and on the laptop nothing else sets
these names, so the result is the same.

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

### Sensor ingestion (issue #18, migration 0014)

The worker applies every `SensorData` row of a pond once, in
`(effective_sample_time, id)` order, rather than the newest value of each
sensor type per poll:

- Discovery: `sensor_ingest_pending` returns the pond's rows not in
  `sensor_ingest_ledger`, from `KOI_SENSOR_INGEST_OVERLAP_MINUTES` before
  the pond's `sensor_ingest_cursor.watermark` (the newest `created_at`
  ingested). The overlap finds a row committed after a row with a higher
  id; the ledger keeps it from being applied twice. The ordering key is
  not the scan position.
- Inputs: rows with the same time are one upload; a channel the node did
  not send, or sent as a non-number, is passed as missing and the others
  are used (`koi/models/sensor_inputs.py`). An input older than the last
  one applied is applied at the last input's time and reported as late
  (placing it by its sample time is #82).
- Atomic progress: the snapshot, the ledger rows and the watermark are
  saved in one transaction (`save_pond_snapshot_with_ingest`). A cycle that
  fails before the save records nothing and drops the in-memory twin, so
  the next cycle applies the same rows to the stored snapshot.
- Time basis: until the node sends a sample time (#17), a reading's time
  is the database insert time. The twin's channel readings, the ledger
  and the cycle report (`worker_status.ponds.<pond>.sensor`) label it
  `ingestion`.
- With no new rows the chemistry engine sees no input; evaporation and
  algae still advance on the weather. Water temperature and light are
  used while fresh (`KOI_SENSOR_CADENCE_MINUTES`); otherwise the air
  temperature stands in for the algae and nitrate terms and the hypoxia
  flag reports unknown.

### Event ledger (issue #19, migration 0015)

Each logged intervention has a UUID, `event_id`, that the app writes to
`pondInterventions.event_id` and posts to `/v1/ponds/{pond}/events/...`
(body field `event_id`). The twin keeps a ledger of the event_ids it has
applied in its snapshot (`koi/models/event_ledger.py`, snapshot version 4):

- Idempotent: a post whose event_id is already in the ledger changes
  nothing and returns the current assessments with `event.status`
  `duplicate` (or `deleted` for a deleted event). A post without one (an
  older app build) is recorded as `legacy:<n>`, and the first table row
  with the same kind and amounts within 14 hours takes it over instead of
  being applied again.
- Time-ordered: an event older than the newest input applied rewinds the
  chemistry engine to the last daily checkpoint before it and replays
  every ingested sensor reading (`sensor_ingest_history`) and event after
  it, in order; a reading at or before the newest event applied is
  replayed in the same way. Checkpoints cover 30 days, which is also how
  far back the event endpoints accept a timestamp. An event older than
  every checkpoint is applied at the current state (`event.placed_late`).
  Evaporation and algae are not rewound: they receive each new event
  once, at their current state.
- Reconciled: each poll cycle gives any row without an event_id the UUID
  derived from its id (`backfill_intervention_event_ids`, deterministic),
  reads the last 30 days of rows (`pond_interventions_since`) and applies
  rows not in the ledger, re-applies edited rows and removes deleted ones,
  with one replay. A deleted event leaves a tombstone, so a retried post
  cannot bring it back; the row reappearing applies it again. Rows created
  before a pond's ledger existed (the stored v3 snapshot's `saved_at`)
  count as applied. The cycle report has `worker_status.ponds.<pond>.events`.
- Per pond: rows are read and ledgers kept per pond; the same event_id
  posted to another pond is that pond's own event.

### Evaluation provenance (issue #21, migration 0016)

Every row the poller and the event endpoints write to the three evaluation
tables carries four values (`koi/provenance.py`):

- `model_version`: `koi.__version__` plus `+g<commit>` when the backend
  runs from a git checkout (`.dirty` when tracked files have uncommitted
  changes); the package version alone otherwise.
- `input_cutoff`: the newest sensor sample, event or camera frame time the
  twin had consumed. `evaluated_at` stays the time the row was computed.
- `forecast_issued_at`: NEA's issue time of the forecast used
  (`weather_forecasts.source_issued_at`), only when every forecast record
  the assessment used has that same time. Chemistry uses the 2-hour
  nowcast and the 4-day outlook, evaporation the 24-hour general forecast
  and the 4-day outlook, algae the 4-day outlook, so chemistry and
  evaporation rows usually have null here and the per-record times in
  `inputs.forecasts`.
- `inputs`: the run (`poll`, `event`, `rating`, `rating_undo`), the sensor
  groups, events, camera frames and ratings it applied, and per forecast
  product the cache records read with their issue times. A record whose
  cache row no longer holds the data the run read has an unknown issue
  time.

The assessment, dashboard and event responses include all four; the
forecast responses include `model_version` and `input_cutoff`. Rows
written before migration 0016 read null in each (unknown); they are not
backfilled.

## Dependencies

`pyproject.toml` lists direct dependencies with lower bounds.
`requirements.lock` pins the full set for Python 3.11 on Linux; regenerate
it with the command at its top after changing `pyproject.toml`.
`requirements.txt` installs the lock plus the package and is what the
PythonAnywhere camera uses.

## Where each service runs

As of 2026-10-03 (permanent hosting for the API and worker: issue #83):

| Service | Runs on | Code |
|---|---|---|
| Camera (`koi.camera`) | PythonAnywhere free tier, `edwinta.pythonanywhere.com` | `~/koi-camera/Backend`, a sparse clone of `main` |
| Digital twin API (`koi.api`) | The owner's laptop, for now | this repository |
| Poller (`koi.worker`) | The owner's laptop, for now | this repository |

The free tier allows one web app (the camera), no always-on tasks and
512 MB of disk, so the API and the worker cannot run there.

### Camera on PythonAnywhere

Web tab settings: source and working directory `/home/edwinta/koi-camera/Backend`,
virtualenv `/home/edwinta/.virtualenvs/koi` (Python 3.11), WSGI file
`/var/www/edwinta_pythonanywhere_com_wsgi.py`:

```python
import sys
path = "/home/edwinta/koi-camera/Backend"
if path not in sys.path:
    sys.path.insert(0, path)

from koi.camera import create_app
application = create_app()
```

First install, from a Bash console with the virtualenv active. The
`Backend/` folder alone keeps the clone at about 4 MB:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/Edwinta-dev/OutdoorKoiFYP.git ~/koi-camera
cd ~/koi-camera && git sparse-checkout set Backend
cd Backend && pip install -r requirements.txt   # the virtualenv's packages, no --user
cp ~/Camera/.env .env && chmod 600 .env         # the camera's keys; never committed
python -c "from koi.camera import create_app; create_app()"
```

Update: `cd ~/koi-camera && git pull`, then `pip install -r Backend/requirements.txt`
if `requirements.lock` changed, then Reload on the Web tab, ideally between
camera wake-ups. Check `GET /` and `GET /metrics` return 200 and the error log
is clean.

Rollback: `~/Camera` still holds the pre-refactor camera app. Copy
`~/camera_wsgi_backup.py` back over the WSGI file and Reload;
`~/koi-venv-freeze-before-20261003.txt` restores the virtualenv's earlier
packages with `pip install -r`.

The free site stops unless "Run until 1 month from today" is clicked on the
Web tab once a month.

### API and worker on the laptop

From `Backend/`, with `Backend/.env` set as for production
(`KOI_ENV=production`, the Supabase URL and service-role key,
`SUPABASE_JWT_SECRET`):

```bash
python -m koi.api      # port 8080
python -m koi.worker   # its own terminal; stops when the laptop sleeps
```

Only one worker polls at a time (the `worker_lease`), so starting a second
copy elsewhere is safe. While the laptop is off, nothing polls and the
stored pond state stops advancing until the worker runs again. The app
cannot use this API yet: it has no sign-in (#49), and `KOI_AUTH=required`
refuses unsigned requests.

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
