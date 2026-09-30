# DigitalTwin — three-domain integration

This service now models all three pond outcome domains, not just water
chemistry. The code lives in `Backend/DigitalTwin/`; the Flutter client it
serves is `MobileUI/mobile_app/`. The version replaced by this one is kept in
`archive/pre-refactor/Backend/DigitalTwin/`.

## New / changed files

| File | Status | Notes |
|---|---|---|
| `pond_twin.py` | **NEW** | Aggregate holding all three engines per user |
| `MobileUI/mobile_app/lib/widgets/detail_graph/algae_severity_rating_card.dart` | **NEW** | Camera frame + human rating control (Flutter) |
| `evaporation_engine.py` | **NEW** | Now stateful (was a stateless projector) |
| `algae_engine.py` | **NEW** | Now stateful, with camera assimilation |
| `registry.py` | changed | Holds `PondTwin` instead of a bare chemistry engine |
| `poller.py` | changed | Advances all three engines per tick |
| `app.py` | changed | Events fan out; new cached assessment endpoints |
| `state_store.py` | changed | Evaluation pushes/reads for the two new domains |
| `engine.py` | unchanged | |
| `forecast_utils.py` | unchanged | |

## Deploy order

1. `pip install -r requirements.txt` in `Backend/DigitalTwin/`.
2. Create the two evaluation tables in Supabase. **Optional to do
   first** — the evaluation pushes are fail-soft, so the service runs
   without them; you just lose the cached `/assessment/*` history until
   they exist.
3. Restart the service. Existing `pond_chemistry_state` rows load fine —
   `PondTwin.from_snapshot` detects the legacy bare-chemistry shape and
   wraps it, preserving accumulated nitrogen state.

## State update triggers

Three, all through the same locked+persisted path:

**Environmental poll** (`poller.py`, every 15 min) — pulls sensors, NEA
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
python -m pytest -q DigitalTwin/test_pond_twin.py      # one file
```

| File | Asserts | Covers |
|---|---|---|
| `test_pond_twin.py` | 61 | state resets, snapshots |
| `test_poller_integration.py` | 91 | real poller + all endpoints |
| `test_severity_ratings.py` | 63 | rating assimilation |
| `test_new_engines.py` | 49 | engine physics |
| `test_contract.py` | 25 | Python <-> Dart JSON contract |
| `test_projection.py` | 29 | chemistry lookahead scenarios |
| `test_algae_history_cache.py` | 7 | algae history cache |
| `test_daily_retention.py` | 9 | daily snapshot retention |

The run ends with an "N assertions passed" line from `conftest.py`.
All run offline: `conftest.py` stubs supabase, dotenv and apscheduler
before any server module is imported, and its `fake_store` fixture
replaces every `state_store` call with an in-memory store. The poller
integration tests walk one pond through a sequence of events and must run
in file order, which is pytest's default. `test_contract.py` reads
`MobileUI/mobile_app/lib/utils/digital_twin_api.dart` relative to its own
location, so it works from any working directory.

## Still open

- **No auth.** Every endpoint trusts a caller-supplied `user_id`.
- **Single process only.** The registry lock does not span gunicorn workers.
- **ESP32 `user_id` mismatch.** The camera firmware hardcoded `15`; pond data
  is `455`. Algae stays `no-camera` for 455 until reconciled.
- Both new models are uncalibrated against ground truth.
