# 0033: Backtest harness against historical data
Status: implemented · Issue: #33 · Date: 2026-10-10

## In short
`python -m koi.tools.backtest --scenario demo_pond|nea_ladder` replays a
recorded fixture through the twin's models and writes
`build/backtest/<scenario>.json` with error metrics. A committed baseline,
`Backend/backtest_baseline.json`, is checked by `python tools/check.py
backend`, which now fails when a metric gets worse than its stated
tolerance allows and the baseline was not updated with a reason in
`docs/models.md`.

## Problem and constraints
There was no number that showed whether a model change made the twin's
predictions better or worse. The issue asks for three: water temperature
prediction error against persistence at +1, +3 and +6 hours, the
evaporation estimate against logged top-ups, and action ladder precision.
Tests and CI may not contact live services, so the inputs are the demo
seed and the NEA CSVs already in the repository. `NEA_Data_Analysis/` is
read only. The action ladder is not implemented in the backend yet.

## Approach
- `Backend/koi/tools/backtest.py::demo_pond` loads the demo seed, puts the
  pond's UserData, profile and interventions into `MemoryStorage`, and
  loads the new `seed["backtest"]` station weather as `weather_observation`
  and `weather_forecast_issuance` rows. Hourly inputs are then built with
  the worker calibration job's own helpers (`koi/worker/poller.py`
  `_observations`, `_hourly_mean`, `_hourly_rain`, `_hourly_humidity`,
  `_topup_samples`), so the replay reads weather the way the twin does.
- `backtest.py::water_temperature_metrics` fits offset and lag with
  `evaporation_engine.py::fit_water_air_temperature` (first 70% of hours),
  then for each hour t of the fit's evaluation interval scores the
  prediction for t+1, t+3 and t+6 hours (air at target minus lag, plus
  offset) against the measured water temperature, beside persistence
  (water at t). It reports RMSE, mean absolute and mean error, and skill.
- `backtest.py::evaporation_metrics` compares each logged top-up with
  `evaporation_engine.py::modelled_topup_litres` at the default shelter
  factor (0.6) over the hours since the previous top-up or water change.
- `backtest.py::nea_ladder` reads `NEA_Data_Analysis/hot_day_validation.csv`
  and scores the recorded `clear_day` firings against `DRY` and
  `heat_holds` against `HARD`: precision, base rate, lift, per-year
  precision. It reproduces the README's 0.861 and 0.842.
- `backtest.py::check` runs every baseline scenario, compares each listed
  metric with its value, direction and tolerance, and requires a line
  `Backtest baseline version N` in `docs/models.md`.
  `--update-baseline` rewrites values and raises N.
- `tools/check.py::check_backend` runs `--check` as the step
  "backend: backtest".

## Alternatives considered
| Option | Why not chosen |
|---|---|
| Replay the full poller step (`koi/tools/rebuild.py`) and read the snapshot's loss before each top-up | The poller takes air temperature from the latest observation and humidity from forecasts per poll; the demo seed has no weather history for it to read, and the calibration helpers already give the hour-by-hour model the issue asks to score. |
| Put the backtest weather into `seed["tables"]` | The development stack would load it (memory and local database profiles) and change what the dev dashboard shows. A separate top-level key is ignored by `koi.dev`. |
| Store weather as full row objects in the seed | About 1,000 rows of 12 keys; compact hourly lists are expanded at run time instead. |

## Trade-offs
- The demo pond numbers come from synthetic data. They guard against
  regressions in code; they do not measure real accuracy. On this fixture
  the air-plus-offset model loses to persistence at every horizon (1.00 C
  against 0.17 C at +1 h), because the seeded air swings two to three times
  as far as the water and the model has no damping term.
- One top-up is scored on the demo pond, so the evaporation metric is a
  single sample.
- Ladder precision scores the notebook's recorded firings, not backend
  code, so it only moves if the table changes. When the ladder is ported,
  `nea_ladder` should compute the firings with it.

## Assumptions
- Air temperature at the target hour minus the lag stands in for the
  forecast. Where the horizon exceeds the lag, that is a perfect forecast,
  so the model error is a lower bound.
- Each top-up refilled the pond to the same level (as the shelter fit
  assumes).
- `hot_day_validation.csv` keeps its column names; if they change, the
  ladder metrics become None and the check fails.

## Edge cases
- Too few hours for the temperature fit: the metrics are None, which the
  check counts as worse.
- A ladder rule that never fires: precision None.
- A baseline scenario name that no longer exists: the check fails with
  that message.
- An improvement passes and is reported as "better" with the update
  command.
- Hourly windows start on the hour; a window starting at :06 had dropped
  one rain hour a day (found and fixed during this work).

## Changes outside the scope
- `Backend/fixtures/demo_pond/build_seed.py`: `seed.json` is generated
  and a test fails if it is edited by hand, so the backtest weather is
  added in the builder (new `backtest_weather`). Existing seed content is
  unchanged (the diff only adds lines).
- `docs/models.md`: the issue requires the baseline reason there.
- `Backend/tests/tools/test_backtest.py`, `tools/test_check.py`: tests.

## How it was verified
- `python -m koi.tools.backtest --scenario demo_pond` and
  `--scenario nea_ladder` run and write their JSON.
- 26 new tests in `Backend/tests/tools/test_backtest.py`: a perfect model
  scores zero error, persistence error equals the change over the horizon,
  pending fits, top-up scoring, ladder arithmetic on a small table, the
  README precisions from the real CSV, the demo replay, the dev stack not
  loading the weather, direction and tolerance handling, the
  models.md version rule, `--update-baseline`, and the committed baseline
  passing.
- One new test in `tools/test_check.py` for the check step.
- `python tools/check.py all`; see the result block for counts.
- Not checked: CI on Linux (the step uses `sys.executable`, which in CI is
  the interpreter `pip install -e Backend[dev]` installed into).

## To change this
- Tolerances and directions per metric live in `backtest_baseline.json`.
- Horizons: `backtest.py::HOURS_AHEAD`. Rules: `LADDER_RULES`.
- Fixture weather: `build_seed.py::backtest_weather`; rebuild the seed and
  update the baseline with a reason.
- Likely bug sources: the poller helpers are private and could change
  shape; the per-day observation reads depend on hour-aligned windows.

## Rollback
Nothing in the services depends on the backtest. Reverting removes the
check step, the baseline and the `seed["backtest"]` key; `koi.dev` never
reads that key, so the dev stack is unaffected. Remove the models.md
baseline entry with it.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-10 02:10 UTC for issue #33 attempt 8; compared HEAD..working tree (base d75e32f).

**Changed components**

| Component | Change | Lines +/- | Tags | Description |
|---|---|---|---|---|
| `Backend/backtest_baseline.json` | new file | +25/-0 |  |  |
| `Backend/fixtures/demo_pond/build_seed.py::backtest_weather` | added | +30/-0 |  | Hourly station weather for the demo pond's 14 days, as compact |
| `Backend/fixtures/demo_pond/build_seed.py::build` | modified | +1/-0 |  |  |
| `Backend/fixtures/demo_pond/build_seed.py::BACKTEST_STATION` | added | +1/-0 |  |  |
| `Backend/fixtures/demo_pond/build_seed.py::RAIN_CLOUD_BELOW` | added | +1/-0 |  |  |
| `Backend/fixtures/demo_pond/build_seed.py::RAIN_MM_BY_HOUR` | added | +1/-0 |  |  |
| `Backend/fixtures/demo_pond/build_seed.py::HUMIDITY_EVERY_HOURS` | added | +1/-0 |  |  |
| `Backend/fixtures/demo_pond/build_seed.py::<module>` | added | +14/-0 |  |  |
| `Backend/fixtures/demo_pond/seed.json` | modified | +1309/-0 |  |  |
| `tools/check.py::check_backend` | modified | +4/-0 |  |  |
| `tools/check.py::<module>` | modified | +4/-1 |  |  |

Plus 3 test file(s), 1 doc file(s).

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/tools/backtest.py`, `Backend/backtest_baseline.json`, `tools/check.py`, `Backend/fixtures/demo_pond/seed.json`. Verdict: violation.
- out of scope: `Backend/fixtures/demo_pond/build_seed.py::backtest_weather` (added); explained in the record: yes
- out of scope: `Backend/fixtures/demo_pond/build_seed.py::build` (modified); explained in the record: yes
- out of scope: `Backend/fixtures/demo_pond/build_seed.py::BACKTEST_STATION` (added); explained in the record: yes
- out of scope: `Backend/fixtures/demo_pond/build_seed.py::RAIN_CLOUD_BELOW` (added); explained in the record: yes
- out of scope: `Backend/fixtures/demo_pond/build_seed.py::RAIN_MM_BY_HOUR` (added); explained in the record: yes
- out of scope: `Backend/fixtures/demo_pond/build_seed.py::HUMIDITY_EVERY_HOURS` (added); explained in the record: yes
- out of scope: `Backend/fixtures/demo_pond/build_seed.py::<module>` (added); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), scope check, dangling reference check

**Consistency**

0 error(s), 9 warning(s) between the rationale above and the change.
- WARN: references something that doesn't exist: backtest.py::water_temperature_metrics (no such file)
- WARN: references something that doesn't exist: evaporation_engine.py::fit_water_air_temperature (no such file)
- WARN: references something that doesn't exist: build_seed.py::backtest_weather (no such file)
- WARN: references something that doesn't exist: koi/worker/poller.py
- WARN: references something that doesn't exist: koi/tools/rebuild.py
- WARN: references something that doesn't exist: hot_day_validation.csv
- WARN: references something that doesn't exist: seed.json
- WARN: references something that doesn't exist: backtest_baseline.json
- WARN: Backend/fixtures/demo_pond/seed.json (modified) changed but not mentioned in the record

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0033-backtest-harness.md` gives the commit.
<!-- verified-facts:end -->
