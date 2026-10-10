# 0027: Chemistry rain term by pond depth and observed rain
Status: implemented · Issue: #27 · Date: 2026-10-10

## In short

The chemistry risk score no longer adds points for forecast wording such as "thundery" or "heavy". It now adds one point for each dilution level that the rain observed over the last 24 hours at the pond's assigned station reaches, based on the depth in the pond profile. Each chemistry assessment includes a `rain_dilution` block that shows the rain, depth, dilution and points. When the rain or the depth is unknown, the block says so and reports reduced confidence.

## Problem and constraints

The 24-hour forecast uses thundery wording on about 72 % of days. With the Watch threshold at 3, the old +2 put most ponds one point from Amber. The physical effect is small: rain dilution is 1 − exp(−R/D), about 2.5 % for 30 mm on a 1.2 m pond. The issue amendment required three things. Observed rain has to come from the #24 observation history, not the latest telemetry cache. Units have to be converted consistently. Missing rain or an unknown depth must not be treated as zero dilution. The scope was `forecast_utils.rain_context_from_text` and `engine.WaterChemistryEngine`.

## Approach

- `Backend/koi/models/engine.py::WaterChemistryEngine.rain_dilution_fraction` (static method) computes 1 − exp(−(mm/1000)/depth_m) and rejects a depth that is zero or not finite.
- `Backend/koi/models/engine.py::WaterChemistryEngine.rain_dilution_window` returns [now − 24 h, now] from the class constant `RAIN_DILUTION_WINDOW`.
- `Backend/koi/models/engine.py::WaterChemistryEngine` gains `RAIN_DILUTION_LEVELS = (0.08, 0.16)`. Reaching the first takes about 101 mm on a 1.2 m pond and 34 mm on a 0.4 m pond. Reaching the second takes about 209 mm and 70 mm.
- `Backend/koi/models/engine.py::WaterChemistryEngine.rain_dilution_term` turns a `rainfall_total` result and a depth into a dict with the window, station, rain_mm, rain_status, coverage, depth_m, dilution_fraction, levels, points, status, confidence and reasons.
- `Backend/koi/models/engine.py::WaterChemistryEngine._score_risk` now takes `rain_points` in place of `rain_incoming`/`rain_intensity`.
- `Backend/koi/models/engine.py::WaterChemistryEngine.assess` accepts `observed_rain` and `depth_m` as keyword arguments. Forecast wording (`rain_incoming`) still sets `add_hardener_now` at Watch, but it no longer affects the score.
- `Backend/koi/models/engine.py::WaterChemistryAssessment` gains an optional `rain_dilution` field carrying that dict.
- `Backend/koi/models/engine.py::WaterChemistryEngine.project_forward` still accepts `daily_rain`, but no longer scores it.
- `Backend/koi/models/forecast_utils.py::rain_context_from_text` is unchanged in behaviour; its docstring now says its output feeds advice only.
- `Backend/koi/models/forecast_utils.py::observed_rain_24h` reads the pond's `stations.rainfall` slot and calls `Storage.fetch_rainfall_total` as of now. That function is the #24 history (`weather_rainfall_total` in SQL, `weather.history.rainfall_total` in memory).
- `Backend/koi/models/pond_twin.py::PondTwin.depth_m_at` returns the measured profile depth. It does not fall back to the evaporation default of 1.2 m.

## Key parameters

| Parameter | Value | Where |
|---|---|---|
| Dilution levels | 0.08 and 0.16 (one point each) | `WaterChemistryEngine.RAIN_DILUTION_LEVELS` |
| Rain needed for the first point | about 101 mm at 1.2 m, 34 mm at 0.4 m | derived |
| Observation window | rolling 24 h ending at the assessment | `WaterChemistryEngine.RAIN_DILUTION_WINDOW` |
| Units | rain mm divided by 1000, depth in m | `WaterChemistryEngine.rain_dilution_fraction` |
| Old forecast-wording points (removed) | +2 thundery/heavy, +1 shower/rain | was in `_score_risk` |
| Watch / High thresholds | 3 / 6, unchanged | `RISK_WATCH_THRESHOLD`, `RISK_HIGH_THRESHOLD` |

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Use yesterday's local day, as REACT does | The chemistry state is assessed every 15 minutes. A rolling window reflects rain that has just fallen. REACT keeps its morning-after day window. |
| Use the evaporation default depth when the profile has none | That would give an apparently confident number from an assumed depth. The issue requires an unknown depth to reduce confidence. |
| Lower `data_confidence` (the device-health block) | Its reason codes are a closed set in the OpenAPI model. Changing them would alter the API schema outside this issue, so the rain term carries its own `confidence`. |
| Remove the `daily_rain` and `rain_intensity` parameters | That would mean editing more callers and tests. Accepting them without using them keeps the change smaller. |

## Trade-offs

A projection has no observed rain, so forecast days now never score rain. A very wet forecast week can no longer move `first_watch_days_from_now` earlier. Unknown rain or depth adds 0 points with reduced confidence, so the score can understate risk until a depth is entered or a station is assigned. The `rain_dilution` block is not stored in `pond_chemistry_evaluations`, so it appears only on fresh assessments (event responses and the poller's in-memory result).

## Assumptions

- The assigned station's rain represents the rain on the pond.
- Values from `fetch_rainfall_total` are in mm. It only sums `interval_total` rows in mm.
- Profile `depth_m` is the mean water depth in metres.

If ponds have a large catchment, such as a sloped surround draining into the pond, the true dilution will be higher than this estimate.

## Edge cases

- No station: status `insufficient_data`, reason "No rainfall station is assigned".
- `no_data` rain: total None, so no points and reduced confidence.
- Partial coverage: the partial total is treated as a lower bound. Points it already reaches count, and confidence is reduced.
- Depth None, 0 or non-finite: no points and reduced confidence.
- Storage errors while reading rain: `fail_soft` catches a `StorageError`, logs `storage_call_failed`, and the term is treated as having no station. The confidence is correctly reduced, but the reason text then reads "No rainfall station is assigned", which is misleading in that case. Not checked: other exception types.

## Changes outside the scope

- `Backend/koi/models/engine.py::WaterChemistryAssessment`: new optional `rain_dilution` field (default None), so the assessment can report the rain term's confidence and reasons. Without it a missing depth or rain total would be indistinguishable from zero dilution, which the issue forbids. Existing constructors are unaffected because the field has a default.
- `Backend/koi/models/pond_twin.py::PondTwin.ingest_environment` (new `observed_rain` argument) and the new `PondTwin.depth_m_at`, so the poller path passes the profile depth.
- `Backend/koi/worker/poller.py::_poll_user` and `_poll_user.cycle`, and `Backend/koi/api/routes.py::_recompute_and_push`: fetch the rolling 24-hour rain and pass it in.

Without these call-site changes the new term would never receive data. In this attempt the window and fraction helpers, first written as module-level functions in engine.py, were moved into `WaterChemistryEngine` so no module-level code is added.

## How it was verified

- New `Backend/tests/models/test_rain_dilution.py` (14 tests) covers:
  - the mm/m conversion and the 30 mm and 100 mm numbers;
  - the pinned levels;
  - a 0.4 m pond reaching the point at 34 mm against 101 mm for 1.2 m;
  - a thundery forecast leaving `assess` and `project_forward` scores unchanged;
  - missing or partial inputs giving reduced confidence;
  - rolling 24 h (40 mm) against REACT's yesterday (60 mm) on the same MemoryStorage observations;
  - the twin using the profile depth.
- `python -m pytest -q -k "rain or risk"`: 34 passed (20 before).
- `python tools/check.py all` passed:
  - backend pytest: 1719 passed (1705 before);
  - ruff, mypy, backtest, tools, firmware host tests and Flutter all passed;
  - the three ESP32 compiles were SKIP because the esp32 core is not installed.

The Supabase path of `fetch_rainfall_total` was not exercised with the new caller.

## To change this

- To move the bar, change `RAIN_DILUTION_LEVELS` and record the change in docs/models.md.
- `rain_dilution_term(levels=...)` overrides the levels per call.
- To change the window, edit `RAIN_DILUTION_WINDOW`.
- A likely source of bugs is passing millimetres as metres, or the reverse. `rain_dilution_fraction` is the only conversion point.

## Rollback

Reverting restores the forecast-wording points. Nothing persists the new block, and the snapshot shape is unchanged, so no data migration is needed. After a revert, check that callers no longer pass `observed_rain`. The old `assess()` signature does not accept it.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-10 07:51 UTC for issue #27 attempt 2; compared HEAD..working tree (base 1c4585e).

**Changed components**

| Component | Change | Lines +/- | Tags | Description |
|---|---|---|---|---|
| `Backend/koi/api/routes.py::_recompute_and_push` | modified | +5/-0 |  | Re-assesses all three domains after a mutation and pushes a fresh |
| `Backend/koi/models/engine.py::WaterChemistryAssessment` | modified | +4/-0 |  |  |
| `Backend/koi/models/engine.py::WaterChemistryEngine` | modified | +15/-0 |  | Persistent, stateful per-pond engine. One instance per userID, |
| `Backend/koi/models/engine.py::WaterChemistryEngine.rain_dilution_window` | added | +4/-0 |  | [now - 24 h, now]: the chemistry rain term's observation window. |
| `Backend/koi/models/engine.py::WaterChemistryEngine.rain_dilution_fraction` | added | +8/-0 |  | Fraction of the pond's water replaced by rain_mm of rain on a pond |
| `Backend/koi/models/engine.py::WaterChemistryEngine._score_risk` | modified | +6/-5 |  | Returns (risk_score, nitrite_override). Pulled out of assess() |
| `Backend/koi/models/engine.py::WaterChemistryEngine.rain_dilution_term` | added | +51/-0 |  | The chemistry rain term from observed rain and the pond's depth. |
| `Backend/koi/models/engine.py::WaterChemistryEngine.assess` | modified | +11/-3 |  | rain_incoming (forecast wording) only shapes the advice |
| `Backend/koi/models/engine.py::WaterChemistryEngine.project_forward` | modified | +10/-13 |  | Simulates the pond forward assuming feeding continues at |
| `Backend/koi/models/forecast_utils.py::rain_context_from_text` | modified | +6/-1 |  | Same heuristic poller.py used inline before this was pulled out - |
| `Backend/koi/models/forecast_utils.py::observed_rain_24h` | added | +14/-0 |  | Rain at the pond's assigned rainfall station over the rolling 24 |
| `Backend/koi/models/pond_twin.py::PondTwin.ingest_environment` | modified | +7/-0 |  | Advances every engine to `now` using freshly polled conditions, |
| `Backend/koi/models/pond_twin.py::PondTwin.depth_m_at` | added | +8/-0 |  | The profile's measured depth at t, or None when there is no |
| `Backend/koi/worker/poller.py::_poll_user` | modified | +4/-0 |  | Advances one pond to now (default: the current time; koi.dev |
| `Backend/koi/worker/poller.py::_poll_user.cycle` | modified | +1/-0 |  |  |

Plus 1 test file(s), 1 doc file(s).

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/models/forecast_utils.py::rain_context_from_text`, `Backend/koi/models/engine.py::WaterChemistryEngine`. Verdict: violation.
- out of scope: `Backend/koi/api/routes.py::_recompute_and_push` (modified); explained in the record: yes
- out of scope: `Backend/koi/models/engine.py::WaterChemistryAssessment` (modified); explained in the record: yes
- out of scope: `Backend/koi/models/pond_twin.py::PondTwin.ingest_environment` (modified); explained in the record: yes
- out of scope: `Backend/koi/models/pond_twin.py::PondTwin.depth_m_at` (added); explained in the record: yes
- out of scope: `Backend/koi/worker/poller.py::_poll_user` (modified); explained in the record: yes
- out of scope: `Backend/koi/worker/poller.py::_poll_user.cycle` (modified); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), scope check, dangling reference check

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0027-rain-term-by-depth.md` gives the commit.
<!-- verified-facts:end -->
