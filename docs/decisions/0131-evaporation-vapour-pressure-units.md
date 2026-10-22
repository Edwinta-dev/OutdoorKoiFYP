# 0131: Evaporation vapour-pressure units
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #131 · Date: 2026-10-23

## In short
The Penman aerodynamic term now receives vapour-pressure differences in hPa, which matches its retained coefficients 0.26 and 0.54. The default shelter factor is 0.5, and the Singapore sanity check is stated as about 3.9 mm/day at 2 m wind with that shelter.

## How it works
1. The model calculates saturation vapour pressure over the water and in the air from temperature using the existing Tetens equation, which returns kPa.
2. It converts each pressure to hPa by multiplying by 10, then applies relative humidity to the air pressure.
3. Forecast wind is converted from 10 m to 2 m when needed, then reduced by the pond shelter factor. The wind function multiplies the hPa difference; negative results remain floored at zero.
4. The resulting daily depth feeds the existing pond loss and lookahead calculations. A per-pond shelter fit still overrides the default in the engine state.

## Main design choice
Convert kPa directly to hPa and retain the published `WIND_FN_A` and `WIND_FN_B` values. Keeping mmHg would require replacing both coefficients with the equivalent mmHg pair and would preserve pressure in a unit the code never needs elsewhere. The former makes the coefficient's intended units explicit and removes the unused mmHg conversion helper.

The shelter default targets a four-day mean in the stated 4–5 mm/day band for the 18 Aug payload conditions. For outlook midpoint air temperatures, humidity and wind, the corrected daily values at 0.5 are 3.9857, 3.4321, 5.1792 and 4.8788 mm/day. Their mean is 4.3689 mm/day, rounded to 4.4. The wind calculation uses the existing 10 m-to-2 m multiplier 0.748 (`Backend/koi/models/evaporation_engine.py::_WIND_10M_TO_2M`). At shelter 0.6 the same mean is 4.9. The shelter root is approximately 0.45, rounded to one decimal place as 0.5 for the configured default. The typical-condition calculation uses 3 m/s at 2 m, shelter 0.5, and gives about 3.9 mm/day. These are model calculations from the supplied payload and documented conditions, not fresh PAN measurements.

## Traps: why the obvious fix is wrong
- Do not retain `saturation_vapour_pressure_mmhg` in this formula with `WIND_FN_A = 0.26`: the coefficient expects hPa, so that combination preserves the 25% low result.
- Do not adjust a fitted multiplier to compensate. The offline owner-supplied comparison supports correcting units; it does not supply a new fitted constant.
- The shelter fit's results are expected to change because the loss integral now has corrected magnitudes.

## Components
- `Backend/koi/models/evaporation_engine.py::evaporation_mm_per_day` — hPa Penman term
- `Backend/koi/models/evaporation_engine.py::POND_SHELTER_FACTOR` — default 0.5
- `Backend/tests/models/test_new_engines.py::test_penman_evaporation_physical_plausibility` — independent fixed-input calculation
- `Backend/koi/models/uncertainty.json` — scenarios recentered to 0.5
- `docs/models.md` — old/new model parameter table

## Breaks or needs follow-up
Previously fitted per-pond shelter values will now produce different loss estimates; recalibration from pond history may produce different fitted values. Existing saved fitted values continue to load and remain in force. The owner-supplied PAN result reports residual underprediction of about 22%, associated with the absent radiation term; radiation is outside this issue.

## Problem and constraints
The model passed mmHg from its helper into Penman coefficients expressed for hPa. Correcting only the unit conversion and default shelter is in scope. No radiation term, calibration multiplier, API change or schema change was introduced.

## Alternatives considered
- Keep mmHg and switch to 0.35 / 0.526 — mathematically equivalent, but less direct than aligning pressure with the documented coefficient units.
- Keep shelter at 0.6 — gives a corrected four-day mean of 4.9 mm/day; the selected 0.5 gives 4.4 for the supplied midpoint case.

## Trade-offs
The corrected model raises evaporation relative to the old implementation before shelter adjustment. Default shelter compensates for the stated open-terrain/pond wind difference; actual pond exposure varies.

## Key parameters
- `WIND_FN_A = 0.26`, `WIND_FN_B = 0.54`: unchanged, pressure input now hPa.
- `POND_SHELTER_FACTOR = 0.5`: selected from the corrected 18 Aug four-day mean above; higher values raise wind-driven loss.
- 10 m to 2 m wind factor `Backend/koi/models/evaporation_engine.py::_WIND_10M_TO_2M = 0.748`: unchanged.

## Assumptions
The supplied outlook midpoints and existing wind conversion represent the payload conditions described by the issue. If observed pond wind differs, the per-pond fit remains the adjustment path.

## Edge cases
- Handled: condensation remains floored at zero; humidity remains clamped to 0–100%.
- Not handled: radiation contribution, which the issue excludes.

## Not verified
The offline PAN benchmark cannot be rerun in this environment. The benchmark metrics and attribution are owner-supplied.

## Changes outside the scope
`Backend/koi/models/uncertainty.json` was recentered from 0.4/0.6/0.8 to 1/3, 0.5 and 2/3 so its central run matches the new default and its relative low/high assumptions remain 2/3 and 4/3 of default.
`Backend/tests/models/test_calibration_fits.py` updates the fitted-shelter scenario expectation from 0.4 to 0.48: a 0.8 scenario multiplier applied to fitted 0.3 scales relative to the new default 0.5. `Backend/tests/worker/test_poller_integration.py` now polls once if its module-scoped store is empty, so the explicitly required `-k` selector can run the evaporation state test without depending on another test having seeded it.

## How it was verified
- `python -m pytest -q -k "evaporation or new_engines"` — 56 passed, 1 warning; checks the selected evaporation, engine, API, storage, calibration and worker cases.
- `python -m pytest -q tests/api/test_dashboard.py` — 49 passed; dashboard contract and Dart fixture freshness.
- `python tools/check.py all` — backend 1,955 passed and 1 SQL test skipped (local Supabase/Docker unavailable); tools 39 passed; backtest 11/11, firmware host 296 passed, all three ESP32 builds passed, Flutter analyze passed, Flutter 274 tests passed. Backtest evaporation absolute error changed from baseline 45.47 L to 17.9 L, still better than baseline tolerance.

## To change this
To use the equivalent mmHg equation, replace the hPa conversion and set `WIND_FN_A` / `WIND_FN_B` to 0.35 / 0.526; keep the pressure units paired with their coefficients. To raise projected loss, increase `POND_SHELTER_FACTOR`; this reduces effective wind shelter.

## Rollback
Reverting restores the old mmHg mismatch and 0.6 default. Check `Backend/koi/models/uncertainty.json` and the fixed-input test with any reversion so the central sensitivity scenario and expected unit calculation remain aligned.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-22 16:58 UTC for issue #131 attempt 5; compared HEAD..working tree (base bbbb574). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 2 file(s), 5 component(s), +9/-17 lines
- plus 5 test file(s), 1 doc file(s)

**Removed symbols**

- no remaining references: `Backend/koi/models/evaporation_engine.py::_KPA_TO_MMHG`
- referenced in docs only: `Backend/koi/models/evaporation_engine.py::saturation_vapour_pressure_mmhg`

**Scope**

Declared: `Backend/koi/models/evaporation_engine.py`, `Backend/tests/models/test_new_engines.py`, `Backend/tests/api/test_dashboard.py`, `Backend/tests/storage/test_supabase_storage.py`, `Backend/tests/fixtures/**`, `Backend/koi/tools/backtest.py`, `Backend/tests/tools/**`, `MobileUI/mobile_app/test/fixtures/v1_dashboard.json`, `docs/models.md`, `docs/decisions/**`. Verdict: violation.
- out of scope: `Backend/koi/models/uncertainty.json` (modified); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Evidence**

3 checked by the tool, 0 tested in this run, 16 claimed only (of 19 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 3 checked, 0 tested, 2 claimed
- Key parameters: 0 checked, 0 tested, 3 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 3 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/models/evaporation_engine.py::evaporation_mm_per_day` — hPa Penman term (named code exists and changed)
  - [checked] Components: `Backend/koi/models/evaporation_engine.py::POND_SHELTER_FACTOR` — default 0.5 (named code exists and changed)
  - [checked] Components: `Backend/koi/models/uncertainty.json` — scenarios recentered to 0.5 (named code exists and changed)
  - [claimed] Components: `Backend/tests/models/test_new_engines.py::test_penman_evaporation_physical_plausibilit... (names no code)
  - [claimed] Key parameters: `WIND_FN_A = 0.26`, `WIND_FN_B = 0.54`: unchanged, pressure input now hPa. (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: condensation remains floored at zero; humidity remains clamped to 0–100%. (names no code)
  - [claimed] How it was verified: `python -m pytest -q -k "evaporation or new_engines"` — 56 passed, 1 warning; checks th... (no test named)
  - [claimed] Components: `docs/models.md` — old/new model parameter table (names no code)
  - [claimed] Key parameters: `POND_SHELTER_FACTOR = 0.5`: selected from the corrected 18 Aug four-day mean above; hi... (no cited constant with a matching value)
  - [claimed] How it was verified: `python -m pytest -q tests/api/test_dashboard.py` — 49 passed; dashboard contract and D... (no test named)
  - full per-claim list (19) in the JSON record

**Consistency**

0 error(s), 1 warning(s) between the rationale above and the change.
- WARN: 'Main design choice' names no alternative; say which 1-2 real options were possible and why this one won

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0131-evaporation-vapour-pressure-units.md` gives the commit.
<!-- verified-facts:end -->
