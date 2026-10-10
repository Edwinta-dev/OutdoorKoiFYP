# 0032: Calibration jobs for pond-specific constants
Status: implemented · Issue: #32 · Date: 2026-10-10

## In short
A daily worker job fits three constants per pond from its own history: the evaporation shelter factor, the water/air temperature offset and lag, and a nitrification rate scale. Each result is stored as a version with fit date, sample count and error. Polls use the version in force at their time, defaults otherwise, and `GET /v1/ponds/{pond}/calibration` shows them.

## Problem and constraints
Nothing stored fitted parameters. Fits may use only aligned, usable data; too little data must give a pending state and the defaults. The temperature error is measured on a later, separate interval against persistence. A rebuild must use the version in force at the replayed time, and old snapshots must still load.

## Approach
- `0022_pond_calibration.sql`: append-only `pond_calibration` (parameter, `fitted`/`pending`, value, lag, `fitted_at`, `effective_from`, sample count, `error`/`details`, training and evaluation intervals). Backend only.
- `evaporation_engine.py::fit_shelter_factor`: least-squares grid over each top-up's litres against `modelled_topup_litres` (hourly Penman net loss since the previous top-up or water change). `fit_water_air_temperature`: lags 0 to 12 h. The first 70% of hours train, the rest evaluate beside 24 h persistence and the default.
- `kit_readings.py::fit_nitrification_scale`: log-grid scale. `simulate_nitrogen` replays feedings and water changes through `WaterChemistryEngine._step_pools` and is compared with kit TAN and nitrite.
- `poller.py::calibrate_pond`/`CalibrationJob` (04:00 SGT, own lease) read 60 days of station observations, forecast humidity, `temp` rows, interventions and kit readings, and store only changed results; a pending row never replaces a fit.
- `poller.py::apply_calibration` runs at the start of each poll cycle (and rebuild) with the newest fitted row whose `effective_from` is at or before the cycle time. The values are kept in the snapshot, so API projections use them.

## Alternatives considered
| Option | Why not chosen |
|---|---|
| Fit kept only in the snapshot | No history; a rebuild could not tell which version applied |
| Full rebuild per nitrification grid point | Far too slow for a daily job |
| Network-mean weather series | Not the pond's station (0009 regime rule) |

## Trade-offs
The shelter and nitrification errors are in-sample, since there are too few samples to hold any back. The nitrification simulation starts from empty pools and ignores readings in its first week. The lag is used only in the live loss integral; daily projections use the offset alone.

## Assumptions
Each top-up refilled to the same level; if not, the shelter fit is biased. Hours are UTC bins. Wind is in knots. Rainfall counts in an hour when its rows cover at least half of it.

## Edge cases
Handled: no station, no water record, missing hours (an interval counts at 75% coverage, scaled up), top-ups without a volume (skipped, listed), unreadable calibration rows (the twin keeps its values), one pond failing (others continue). Not handled: a fit worse than persistence is still applied; `beats_persistence` only reports it.

## Changes outside the scope
- `models/engine.py`: `nitrification_scale` and two snapshot keys, needed for the engine to use the fit.
- `storage/{base,memory,supabase_storage}.py`: `insert_calibration` and `fetch_calibrations`.
- `api/responses.py` models, and a regenerated `openapi.yaml`.
- `tests/api/test_app_factory.py` now expects the third scheduled job.
- `tests/worker/test_poller_integration.py`: two order-dependent algae-rating tests renamed `test_rating_thresholds_*`, because `-k calibration` ran them out of order (they failed that way at HEAD too).
- Docs: `docs/models.md`, `docs/api/README.md`, `supabase/README.md`, `docs/weather-ingestion.md`.

## How it was verified
New tests:
- `test_calibration_fits.py`: synthetic data with known values recovered (lag, offset, shelter, scale), the pending cases, the engines using a fit, and the old snapshot fixtures.
- `test_calibration_job.py`: a 30-day synthetic pond. It checks the values recovered, storage, dedup, poll application and rebuild by time, the lease, and a failing pond.
- API, storage and SQL (`test_sql_calibration.py`: constraints, grants, RLS) tests.

`pytest -k calibration` passes (50 tests, SQL tests included, against the local Supabase stack). `python tools/check.py all` and `check.py database` pass (migration rehearsal with 0022, 246 SQL tests, dev stack profile). Not tested: live data volumes, and the job's run time against the REST API.

## To change this
Thresholds and bounds are module constants (`MIN_TOPUP_SAMPLES`, `MIN_HOUR_COVERAGE`, `MIN_NITROGEN_READINGS`, `SCALE_BOUNDS`, `CALIBRATION_WINDOW` and others). Bump `CALIBRATION_METHOD_VERSION` when the method changes. Likely bug sources: hour binning, the rainfall coverage rule, and the relative scenario scaling in `EvaporationFeedEngine.project_forward`.

## Rollback
Removing the job and the `apply_calibration` call returns the engines to their defaults; snapshots keep the unused keys harmlessly. The API route reads `pond_calibration`. Keep the table, since migrations are not reverted.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-10 01:48 UTC for issue #32 attempt 7; compared HEAD..working tree (base 1257072).

**Changed components**

| Component | Change | Lines +/- | Tags | Description |
|---|---|---|---|---|
| `Backend/koi/api/responses.py::CalibrationVersion` | added | +24/-0 |  | One pond_calibration row: a fit or a pending result. |
| `Backend/koi/api/responses.py::CalibrationParameter` | added | +11/-0 |  |  |
| `Backend/koi/api/responses.py::CalibrationParameters` | added | +4/-0 |  |  |
| `Backend/koi/api/responses.py::Calibration` | added | +5/-0 |  |  |
| `Backend/koi/api/routes.py::get_calibration` | added | +26/-0 |  | The evaporation shelter factor, the water/air temperature offset and lag, and the nitrification rate scale |
| `Backend/koi/api/routes.py::_CALIBRATION_UNITS` | added | +1/-0 |  |  |
| `Backend/koi/api/routes.py::<module>` | added | +7/-0 |  |  |
| `Backend/koi/kit_readings.py::NitrogenEvent` | added | +10/-0 |  | A logged event as the chemistry engine applies it to TAN and |
| `Backend/koi/kit_readings.py::nitrogen_events` | added | +7/-0 |  | pondInterventions rows (Storage.fetch_interventions) as the events |
| `Backend/koi/kit_readings.py::nitrogen_events.num` | added | +2/-0 |  |  |
| `Backend/koi/kit_readings.py::simulate_nitrogen` | added | +25/-0 |  | (TAN, nitrite) in mg/L at each of times (ascending), from empty |
| `Backend/koi/kit_readings.py::_apply` | added | +10/-0 |  |  |
| `Backend/koi/kit_readings.py::fit_nitrification_scale` | added | +40/-0 |  | Fits the multiplier on both nitrification rates that makes the |
| `Backend/koi/kit_readings.py::fit_nitrification_scale.errors` | added | +9/-0 |  |  |
| `Backend/koi/kit_readings.py::fit_nitrification_scale.rmse` | added | +2/-0 |  |  |
| `Backend/koi/kit_readings.py::MIN_NITROGEN_READINGS` | added | +1/-0 |  |  |
| `Backend/koi/kit_readings.py::SPIN_UP` | added | +1/-0 |  |  |
| `Backend/koi/kit_readings.py::SCALE_BOUNDS` | added | +1/-0 |  |  |
| `Backend/koi/kit_readings.py::_SCALE_GRID_POINTS` | added | +1/-0 |  |  |
| `Backend/koi/kit_readings.py::_BASE_TAN_TO_NO2` | added | +1/-0 |  |  |
| `Backend/koi/kit_readings.py::_BASE_NO2_TO_NO3` | added | +1/-0 |  |  |
| `Backend/koi/kit_readings.py::<module>` | added | +17/-0 |  |  |
| `Backend/koi/models/engine.py::WaterChemistryEngine.__init__` | modified | +4/-0 |  |  |
| `Backend/koi/models/engine.py::WaterChemistryEngine._advance_pools` | modified | +2/-0 |  |  |
| `Backend/koi/models/engine.py::WaterChemistryEngine.project_forward` | modified | +5/-1 |  | Simulates the pond forward assuming feeding continues at |
| `Backend/koi/models/engine.py::WaterChemistryEngine.to_snapshot` | modified | +3/-0 |  |  |
| `Backend/koi/models/engine.py::WaterChemistryEngine.from_snapshot` | modified | +3/-1 |  |  |
| `Backend/koi/models/evaporation_engine.py::predict_water_temp` | modified | +1/-1 |  | Applies the fitted offset. Falls back to -1.0C (ponds usually sit |
| `Backend/koi/models/evaporation_engine.py::_rmse` | added | +2/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::pending_calibration` | added | +6/-0 |  | A calibration result with too little data: no value, the reason |
| `Backend/koi/models/evaporation_engine.py::fit_water_air_temperature` | added | +67/-0 |  | Fits water(h) = air(h - lag) + offset from the pond's own hourly |
| `Backend/koi/models/evaporation_engine.py::fit_water_air_temperature.residuals` | added | +2/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::HourConditions` | added | +10/-0 |  | One complete hour of conditions for the shelter fit. Wind is at |
| `Backend/koi/models/evaporation_engine.py::TopUpSample` | added | +10/-0 |  | A logged top-up and the hours since the previous top-up or water |
| `Backend/koi/models/evaporation_engine.py::TopUpSample.coverage` | added | +3/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::modelled_topup_litres` | added | +14/-0 |  | The net loss the model gives over the sample's complete hours (the |
| `Backend/koi/models/evaporation_engine.py::fit_shelter_factor` | added | +36/-0 |  | Fits the wind shelter factor that makes the modelled loss since |
| `Backend/koi/models/evaporation_engine.py::fit_shelter_factor.sse` | added | +2/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.__init__` | modified | +5/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.shelter_factor` | added | +4/-0 |  | The pond's fitted shelter factor, else POND_SHELTER_FACTOR. |
| `Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.calibration_versions` | added | +4/-0 |  | pond_calibration ids applied, by parameter. |
| `Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.apply_calibration` | added | +11/-0 |  | Sets the pond's calibrated constants (issue #32). None for a |
| `Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine._air_for_water_estimate` | added | +16/-0 |  | The air temperature the water estimate follows: with a fitted |
| `Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.advance_state` | modified | +3/-1 |  | Integrates evaporative loss over the interval since the last |
| `Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.project_forward` | modified | +10/-1 |  | Projects cumulative loss forward from the CURRENT accumulated |
| `Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.to_snapshot` | modified | +4/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.from_snapshot` | modified | +3/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::DEFAULT_WATER_AIR_OFFSET_C` | added | +1/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::MAX_WATER_AIR_LAG_HOURS` | added | +1/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::TEMPERATURE_TRAINING_FRACTION` | added | +1/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::MIN_TEMPERATURE_HOURS` | added | +1/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::MIN_TEMPERATURE_EVALUATION_HOURS` | added | +1/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::PERSISTENCE_HOURS` | added | +1/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::MIN_TOPUP_SAMPLES` | added | +1/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::MIN_HOUR_COVERAGE` | added | +1/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::SHELTER_BOUNDS` | added | +1/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::_SHELTER_STEP` | added | +1/-0 |  |  |
| `Backend/koi/models/evaporation_engine.py::<module>` | added | +31/-0 |  |  |
| `Backend/koi/storage/base.py::Storage.insert_calibration` | added | +5/-0 |  | Appends one calibration result (CALIBRATION_COLUMNS without id, |
| `Backend/koi/storage/base.py::Storage.fetch_calibrations` | added | +4/-0 |  | Every calibration row of the pond, ordered by effective_from, |
| `Backend/koi/storage/base.py::CALIBRATION_COLUMNS` | added | +3/-0 |  |  |
| `Backend/koi/storage/base.py::<module>` | added | +2/-0 |  |  |
| `Backend/koi/storage/memory.py::MemoryStorage.insert_calibration` | added | +5/-0 |  |  |
| `Backend/koi/storage/memory.py::MemoryStorage.fetch_calibrations` | added | +5/-0 |  |  |
| `Backend/koi/storage/memory.py::TABLE_COLUMNS` | modified | +2/-0 |  |  |
| `Backend/koi/storage/memory.py::USER_COLUMN` | modified | +1/-0 |  |  |
| `Backend/koi/storage/memory.py::_STAMPED` | modified | +1/-0 |  |  |
| `Backend/koi/storage/supabase_storage.py::SupabaseStorage.insert_calibration` | added | +5/-0 |  |  |
| `Backend/koi/storage/supabase_storage.py::SupabaseStorage.fetch_calibrations` | added | +11/-0 |  |  |
| `Backend/koi/worker/poller.py::_poll_user` | modified | +3/-0 |  | Advances one pond to now (default: the current time; koi.dev |
| `Backend/koi/worker/poller.py::_poll_user.cycle` | modified | +2/-0 |  |  |
| `Backend/koi/worker/poller.py::calibration_in_force` | added | +12/-0 |  | The newest fitted row of each parameter with effective_from at or |
| `Backend/koi/worker/poller.py::apply_calibration` | added | +14/-0 |  | Puts the calibration in force at `at` into the twin's engines and |
| `Backend/koi/worker/poller.py::_hour` | added | +2/-0 |  |  |
| `Backend/koi/worker/poller.py::_observations` | added | +13/-0 |  | Station observations over [start, end], a day per call so no call |
| `Backend/koi/worker/poller.py::_hourly_mean` | added | +5/-0 |  |  |
| `Backend/koi/worker/poller.py::_hourly_rain` | added | +10/-0 |  |  |
| `Backend/koi/worker/poller.py::_hourly_humidity` | added | +16/-0 |  | The 24-hour general forecast's humidity midpoint usable at each |
| `Backend/koi/worker/poller.py::_interval_hours` | added | +3/-0 |  |  |
| `Backend/koi/worker/poller.py::_topup_samples` | added | +38/-0 |  | Each logged top-up with the hours since the previous top-up or |
| `Backend/koi/worker/poller.py::_temp_lookup` | added | +10/-0 |  | Hourly temperature for the nitrogen simulation: the pond's water, |
| `Backend/koi/worker/poller.py::_temp_lookup.temp_at` | added | +3/-0 |  |  |
| `Backend/koi/worker/poller.py::fit_pond_calibration` | added | +47/-0 |  | The three fits for one pond from its history over the |
| `Backend/koi/worker/poller.py::_calibration_row` | added | +8/-0 |  |  |
| `Backend/koi/worker/poller.py::_calibration_row.when` | added | +2/-0 |  |  |
| `Backend/koi/worker/poller.py::_changed` | added | +22/-0 |  | Whether row is worth a new version: a fit whose value, lag, sample |
| `Backend/koi/worker/poller.py::calibrate_pond` | added | +21/-0 |  | Fits the pond's three constants and stores each result that is new |
| `Backend/koi/worker/poller.py::CalibrationJob` | added | +5/-0 |  | The scheduled run: takes the calibration lease, calibrates every |
| `Backend/koi/worker/poller.py::CalibrationJob.__init__` | added | +3/-0 |  |  |
| `Backend/koi/worker/poller.py::CalibrationJob.run` | added | +29/-0 |  |  |
| `Backend/koi/worker/poller.py::start` | modified | +4/-0 |  | Schedules a Worker's run_cycle every poll_interval_minutes, first run |
| `Backend/koi/worker/poller.py::CALIBRATION_LEASE` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::CALIBRATION_LEASE_SECONDS` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::CALIBRATION_WINDOW` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::MAX_TOPUP_INTERVAL_DAYS` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::CALIBRATION_METHOD_VERSION` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::SHELTER` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::TEMPERATURE` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::NITRIFICATION` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::CALIBRATION_PARAMETERS` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::CALIBRATION_DEFAULTS` | added | +5/-0 |  |  |
| `Backend/koi/worker/poller.py::_KNOTS_TO_MS` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::_HOUR` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::_HUMIDITY_BLOCK_HOURS` | added | +1/-0 |  |  |
| `Backend/koi/worker/poller.py::<module>` | added | +77/-0 |  |  |
| `supabase/migrations/0022_pond_calibration.sql` | new file | +66/-0 |  |  |

Plus 7 test file(s), 5 doc file(s).

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/models/evaporation_engine.py::fit_water_air_offset`, `Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine`, `Backend/koi/kit_readings.py`, `Backend/koi/worker/poller.py`, `Backend/koi/api/routes.py`, `supabase/migrations/**`. Verdict: violation.
- out of scope: `Backend/koi/api/responses.py::CalibrationVersion` (added); explained in the record: no
- out of scope: `Backend/koi/api/responses.py::CalibrationParameter` (added); explained in the record: no
- out of scope: `Backend/koi/api/responses.py::CalibrationParameters` (added); explained in the record: no
- out of scope: `Backend/koi/api/responses.py::Calibration` (added); explained in the record: no
- out of scope: `Backend/koi/models/engine.py::WaterChemistryEngine.__init__` (modified); explained in the record: no
- out of scope: `Backend/koi/models/engine.py::WaterChemistryEngine._advance_pools` (modified); explained in the record: no
- out of scope: `Backend/koi/models/engine.py::WaterChemistryEngine.project_forward` (modified); explained in the record: no
- out of scope: `Backend/koi/models/engine.py::WaterChemistryEngine.to_snapshot` (modified); explained in the record: no
- out of scope: `Backend/koi/models/engine.py::WaterChemistryEngine.from_snapshot` (modified); explained in the record: no
- out of scope: `Backend/koi/models/evaporation_engine.py::predict_water_temp` (modified); explained in the record: no
- out of scope: `Backend/koi/models/evaporation_engine.py::<module>` (added); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::Storage.insert_calibration` (added); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::Storage.fetch_calibrations` (added); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::CALIBRATION_COLUMNS` (added); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::<module>` (added); explained in the record: no
- out of scope: `Backend/koi/storage/memory.py::MemoryStorage.insert_calibration` (added); explained in the record: no
- out of scope: `Backend/koi/storage/memory.py::MemoryStorage.fetch_calibrations` (added); explained in the record: no
- out of scope: `Backend/koi/storage/memory.py::TABLE_COLUMNS` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/memory.py::USER_COLUMN` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/memory.py::_STAMPED` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/supabase_storage.py::SupabaseStorage.insert_calibration` (added); explained in the record: no
- out of scope: `Backend/koi/storage/supabase_storage.py::SupabaseStorage.fetch_calibrations` (added); explained in the record: no

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), database suite (migration rehearsal, SQL tests, local profile), scope check, dangling reference check

**Consistency**

0 error(s), 140 warning(s) between the rationale above and the change.
- WARN: 'How it was verified' names a test file that does not exist: test_calibration_fits.py
- WARN: 'How it was verified' names a test file that does not exist: test_calibration_job.py
- WARN: 'How it was verified' names a test file that does not exist: test_sql_calibration.py
- WARN: references something that doesn't exist: evaporation_engine.py::fit_shelter_factor (no such file)
- WARN: references something that doesn't exist: kit_readings.py::fit_nitrification_scale (no such file)
- WARN: references something that doesn't exist: poller.py::calibrate_pond (no such file)
- WARN: references something that doesn't exist: 0022_pond_calibration.sql
- WARN: references something that doesn't exist: models/engine.py
- WARN: references something that doesn't exist: api/responses.py
- WARN: references something that doesn't exist: openapi.yaml
- WARN: references something that doesn't exist: tests/api/test_app_factory.py
- WARN: references something that doesn't exist: tests/worker/test_poller_integration.py
- WARN: Backend/koi/api/responses.py::CalibrationVersion (added) changed but not mentioned in the record
- WARN: Backend/koi/api/responses.py::CalibrationParameter (added) changed but not mentioned in the record
- WARN: Backend/koi/api/responses.py::CalibrationParameters (added) changed but not mentioned in the record
- WARN: Backend/koi/api/responses.py::Calibration (added) changed but not mentioned in the record
- WARN: Backend/koi/api/routes.py::get_calibration (added) changed but not mentioned in the record
- WARN: Backend/koi/api/routes.py::_CALIBRATION_UNITS (added) changed but not mentioned in the record
- WARN: Backend/koi/api/routes.py::<module> (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::NitrogenEvent (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::nitrogen_events (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::nitrogen_events.num (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::simulate_nitrogen (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::_apply (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::fit_nitrification_scale (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::fit_nitrification_scale.errors (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::fit_nitrification_scale.rmse (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::MIN_NITROGEN_READINGS (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::SPIN_UP (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::SCALE_BOUNDS (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::_SCALE_GRID_POINTS (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::_BASE_TAN_TO_NO2 (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::_BASE_NO2_TO_NO3 (added) changed but not mentioned in the record
- WARN: Backend/koi/kit_readings.py::<module> (added) changed but not mentioned in the record
- WARN: Backend/koi/models/engine.py::WaterChemistryEngine.__init__ (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/engine.py::WaterChemistryEngine._advance_pools (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/engine.py::WaterChemistryEngine.project_forward (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/engine.py::WaterChemistryEngine.to_snapshot (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/engine.py::WaterChemistryEngine.from_snapshot (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::predict_water_temp (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::_rmse (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::pending_calibration (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::fit_water_air_temperature (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::fit_water_air_temperature.residuals (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::HourConditions (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::TopUpSample (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::TopUpSample.coverage (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::modelled_topup_litres (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::fit_shelter_factor (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::fit_shelter_factor.sse (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.__init__ (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.shelter_factor (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.calibration_versions (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.apply_calibration (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine._air_for_water_estimate (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.advance_state (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.project_forward (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.to_snapshot (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::EvaporationFeedEngine.from_snapshot (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::DEFAULT_WATER_AIR_OFFSET_C (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::MAX_WATER_AIR_LAG_HOURS (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::TEMPERATURE_TRAINING_FRACTION (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::MIN_TEMPERATURE_HOURS (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::MIN_TEMPERATURE_EVALUATION_HOURS (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::PERSISTENCE_HOURS (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::MIN_TOPUP_SAMPLES (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::MIN_HOUR_COVERAGE (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::SHELTER_BOUNDS (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::_SHELTER_STEP (added) changed but not mentioned in the record
- WARN: Backend/koi/models/evaporation_engine.py::<module> (added) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::Storage.insert_calibration (added) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::Storage.fetch_calibrations (added) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::CALIBRATION_COLUMNS (added) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::<module> (added) changed but not mentioned in the record
- WARN: Backend/koi/storage/memory.py::MemoryStorage.insert_calibration (added) changed but not mentioned in the record
- WARN: Backend/koi/storage/memory.py::MemoryStorage.fetch_calibrations (added) changed but not mentioned in the record
- WARN: Backend/koi/storage/memory.py::TABLE_COLUMNS (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/memory.py::USER_COLUMN (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/memory.py::_STAMPED (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/supabase_storage.py::SupabaseStorage.insert_calibration (added) changed but not mentioned in the record
- WARN: Backend/koi/storage/supabase_storage.py::SupabaseStorage.fetch_calibrations (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_poll_user (modified) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_poll_user.cycle (modified) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::calibration_in_force (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::apply_calibration (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_hour (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_observations (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_hourly_mean (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_hourly_rain (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_hourly_humidity (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_interval_hours (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_topup_samples (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_temp_lookup (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_temp_lookup.temp_at (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::fit_pond_calibration (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_calibration_row (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_calibration_row.when (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_changed (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::calibrate_pond (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::CalibrationJob (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::CalibrationJob.__init__ (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::CalibrationJob.run (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::start (modified) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::CALIBRATION_LEASE (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::CALIBRATION_LEASE_SECONDS (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::CALIBRATION_WINDOW (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::MAX_TOPUP_INTERVAL_DAYS (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::CALIBRATION_METHOD_VERSION (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::SHELTER (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::TEMPERATURE (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::NITRIFICATION (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::CALIBRATION_PARAMETERS (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::CALIBRATION_DEFAULTS (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_KNOTS_TO_MS (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_HOUR (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::_HUMIDITY_BLOCK_HOURS (added) changed but not mentioned in the record
- WARN: Backend/koi/worker/poller.py::<module> (added) changed but not mentioned in the record
- WARN: supabase/migrations/0022_pond_calibration.sql (new file) changed but not mentioned in the record
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/api/responses.py::CalibrationVersion
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/api/responses.py::CalibrationParameter
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/api/responses.py::CalibrationParameters
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/api/responses.py::Calibration
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/models/engine.py::WaterChemistryEngine.__init__
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/models/engine.py::WaterChemistryEngine._advance_pools
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/models/engine.py::WaterChemistryEngine.project_forward
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/models/engine.py::WaterChemistryEngine.to_snapshot
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/models/engine.py::WaterChemistryEngine.from_snapshot
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/models/evaporation_engine.py::predict_water_temp
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/models/evaporation_engine.py::<module>
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::Storage.insert_calibration
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::Storage.fetch_calibrations
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::CALIBRATION_COLUMNS
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::<module>
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/memory.py::MemoryStorage.insert_calibration
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/memory.py::MemoryStorage.fetch_calibrations
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/memory.py::TABLE_COLUMNS
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/memory.py::USER_COLUMN
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/memory.py::_STAMPED
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/supabase_storage.py::SupabaseStorage.insert_calibration
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/supabase_storage.py::SupabaseStorage.fetch_calibrations

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0032-pond-calibration-jobs.md` gives the commit.
<!-- verified-facts:end -->
