# Local development stack

`python -m koi.dev` runs the digital twin API, the worker and the camera
service on a seeded demo pond, with no live Supabase project, no NEA API,
no PythonAnywhere, no phone and no hardware. It is for trying the app and
the API end to end on one machine. Nothing it serves is a measurement.

## Run it

Python 3.11 or newer, from the repository root:

```bash
pip install -e "Backend[dev]"
cd Backend
python -m koi.dev            # API on :8080, camera service on :5000, worker every 15 min
python -m koi.dev --check    # start, check, stop; exit 0 when every check passed
```

Options: `--host` (default `0.0.0.0`, so a phone on the same network can
reach it), `--api-port`, `--camera-port`, `--seed PATH`.

What happens on start:

1. The seed (`Backend/fixtures/demo_pond/seed.json`) is loaded and every
   time in it is moved so its newest sensor reading is the current time.
   Daily averages are computed from the moved readings.
2. The worker is run over the seed's 14 days of history, one poll per
   hourly reading, with each logged feed, water change and top-up applied
   at its own time (`Backend/koi/dev/replay.py`). The engines only see the
   readings and camera frames stored up to each poll. Takes a few seconds.
3. The API, the camera service and the worker start in one process on one
   in-memory store. The data is lost when the process stops.

The stack runs with `KOI_ENV=development`, `KOI_AUTH=disabled` (the pond
id in the request is trusted; no sign-in token is checked) and error
reporting off. Keep it on a development network.

`--check` starts the stack on free local ports, waits for the worker's
first cycle, then requests the dashboard, assessments, profile and the
three forecasts of both ponds and validates each response against the
OpenAPI document (`docs/api/openapi.yaml`, generated from the same
models). It also checks that pond 1's dashboard has all three assessments
and fresh readings, and that the camera service answers.

## The demo data

All values are synthetic. They are built by
`Backend/fixtures/demo_pond/build_seed.py` from the sample rows in
`Backend/tests/models/test_new_engines.py` and the recorded NEA responses
in `Backend/tests/fixtures/nea_responses.json`; edit the builder and
rerun it rather than editing `seed.json`.

| Pond | What it has |
|---|---|
| 1 | 5000 L, 8000 g of koi, a pond profile. 14 days of hourly pH, TDS, water temperature and light, ending on the sample reading (pH 7.64, TDS 220 ppm, 27.65 C, 22755 lux). Six camera frames a day with the green ratio rising about 20% a day. A 40 g feed every morning, a 30% water change on day 5 and a 2% top-up on day 10. |
| 2 | Older data shapes: no pond profile (volume and biomass from `UserData`), pH, temperature and light but no TDS, two pH rows with the same time (the higher id is the newest), a camera row with the old plain-text state `base`, and an engine snapshot from before `snapshot_version` existed. The worker skips it ("no TDS"), so its dashboard has readings and weather but no assessments. |

Also one sensor row with no pond (firmware from before the node sent a
pond id), and the weather caches and station list that one NEA fetch
(2 October 2026, 10:06 Singapore time) writes. The weather is moved to the
current hour: the same conditions, UV report and forecasts whatever the
time of day.

No new readings arrive while the stack runs, so 30 minutes after start
the dashboard marks the readings stale, as it would for a node that
stopped reporting. Restart to move the seed to the new time. Events and
profile changes sent to the API are applied and kept until the stack
stops.

## Point the app at it

The app reads its configuration from `MobileUI/mobile_app/env/dev.json`
(gitignored; copy `env/dev.json.example`). Use the address of the machine
running the stack as the phone sees it:

| App runs on | Address |
|---|---|
| A phone on the same Wi-Fi | the machine's LAN address, e.g. `192.168.1.20` (allow ports 8080 and 54321 through the machine's firewall) |
| The Android emulator | `10.0.2.2` |
| The iOS simulator, desktop or web | `127.0.0.1` |

```json
{
  "SUPABASE_URL": "http://192.168.1.20:54321",
  "SUPABASE_PUBLISHABLE_KEY": "PUBLISHABLE_KEY from `supabase status -o env`",
  "DIGITAL_TWIN_BASE_URL": "http://192.168.1.20:8080",
  "POND_IMAGE_BUCKET": "imageAnalysisBucket",
  "SENTRY_DSN": "",
  "SENTRY_ENVIRONMENT": "development"
}
```

```bash
cd MobileUI/mobile_app
flutter run --dart-define-from-file=env/dev.json
```

At onboarding, enter pond (user) id `1` for the demo pond or `2` for the
older shapes.

The app reads some screens from the twin API (assessments, forecasts,
logging events) and some straight from Supabase (sensor history graphs,
the intervention timeline, onboarding, camera frames). With the memory
profile only the twin API has the demo data; for the Supabase screens use
the local database profile below, with `SUPABASE_URL` pointing at the
local stack as in the example. Never point a development build at the
live project with this stack.

Onboarding against the local stack rewrites pond 1's `UserData` row (its
volume, biomass and assigned stations come from the form and the phone's
location). Run the local database profile again to restore the seed.

## Local database profile

The same seed in the local Supabase stack, served through
`SupabaseStorage` and the stack's REST API, as the deployed services use
the live project. Needs Docker Desktop and the Supabase CLI; from the
repository root:

```bash
supabase start               # first run downloads the images
supabase db reset            # the local database at every migration
cd Backend
python -m koi.dev --profile supabase           # seed, fast-forward, serve
python -m koi.dev --profile supabase --check   # seed, check, remove the seed
```

The URL, service-role key and database URL are taken from `supabase status
-o env`, or from `KOI_DEV_SUPABASE_URL`, `KOI_DEV_SUPABASE_SERVICE_ROLE_KEY`
and `KOI_DEV_DB_URL` (`Backend/.env.example`). Anything not on this
machine is refused. Seeding first deletes the rows it would collide with:
everything of ponds 1 and 2, the seeded sensor row ids, and the weather
cache and station rows the seed carries. The local stack is disposable;
`supabase db reset` rebuilds it.

`--check` here also checks every migration in `supabase/migrations` is
applied, and that each pond's dashboard built from the local database is
identical to the memory profile's on the same seed (apart from the
evaluation rows' own id and write time). Without a running local stack it
prints `SKIP` and exits 2.

## Docker Compose

`docker-compose.yml` at the repository root builds one image
(`Backend/Dockerfile.dev`; only the backend package, the seed and the
migrations go in, no `.env`).

```bash
docker compose up --build
```

runs `python -m koi.dev` (memory profile) with ports 8080 and 5000. The
three services share one container because in-memory data cannot be
shared between containers.

```bash
supabase start && supabase db reset
export KOI_DEV_SUPABASE_SERVICE_ROLE_KEY=...   # SERVICE_ROLE_KEY from `supabase status -o env`
docker compose --profile supabase up --build api worker camera
```

runs the three services as separate containers on the local Supabase
stack (reached as `host.docker.internal`), after a one-shot `seed`
container has written the seed and fast-forwarded the worker. The API
runs under gunicorn, the worker and the camera service as in production.
Stop the memory stack first: both use ports 8080 and 5000.

## Database checks

```bash
python tools/check.py database
```

runs, against the local stack: the dump-upgrade rehearsal
(`tools/db_rehearsal.py`: the observed `schema.sql`, synthetic legacy
rows, then every later migration, compared with a fresh `supabase db
reset`), the SQL tests in `Backend/tests/sql`, and `python -m koi.dev
--profile supabase --check`. It is not part of `check.py all` because it
rebuilds the local database. Without Docker, the Supabase CLI or a running
local stack every step is SKIP, and the database checks remain to be run.
CI runs it in the `database` job (`.github/workflows/ci.yml`).
