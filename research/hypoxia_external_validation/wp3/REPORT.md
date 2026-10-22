# WP3: PAN evaporation benchmark (frozen engine)

Engine `Backend/koi/models/evaporation_engine.py` imported read-only, unchanged (SHA-256 baafc94e…ca74). Data: Figshare 10329347 v1 `pan_v1.xlsx` (hashes in MANIFEST.md). Seed 20261022, 5000 run-level bootstrap resamples. All numbers come from `run_wp3.py`; see `results.json` and the CSVs.

Dataset caveats: 40 hand-picked "typical days" from one site and one pan (SWP only), in 12 runs of at most 9 days. There is no rain (0 on every row), no radiation and no pan geometry.

## `evaporation_mm_per_day`
- **Repository commit:** research worktree HEAD at run time (see MANIFEST.md)
- **Dataset:** PAN v1, SWP pan
- **Inputs:**
  - SWP water temperature (°C)
  - air temperature (°C)
  - RH (%)
  - wind (m/s), with `wind_measured_at_10m=False`
- **Missing:** radiation (the engine has no radiation term), rain (all zeros), shelter.
- **Scenarios:**
  - shelter_factor 1.0 and 0.6 (the default) are named variants.
  - Wind measured at 10 m is a sensitivity run only.
- **Target measured independently?** Y. The target is the daily SWP level drop: the day's first record minus its last.
- **Splits:**
  - The baseline mean is computed leave-one-run-out.
  - Persistence uses the previous day's drop within the same run.
- **Scored:** 35 days in 10 runs. Five days were excluded for missing air temperature/RH; see `exclusions.csv`.
- **Metrics** (95% CIs from run-level bootstrap; bias is predicted minus observed; observed mean 2.88 mm/day):

| Predictor | MAE | Bias | RMSE | r |
|---|---|---|---|---|
| Engine, shelter 1.0 | 1.27 [0.89, 1.79] | -1.20 [-1.78, -0.74] | 1.49 | 0.73 [0.34, 0.92] |
| Engine, shelter 0.6 | 1.47 [1.06, 2.06] | -1.43 [-2.05, -0.96] | 1.70 | 0.73 [0.29, 0.91] |
| Leave-one-run-out mean | 1.02 [0.66, 1.53] | 0.03 | 1.33 | n/a |

- **Against baselines** (ΔMAE; positive means the engine is worse):
  - **Vs the mean:** at shelter 1.0, +0.25 [-0.11, +0.58], not distinguishable. At shelter 0.6, +0.44 [+0.07, +0.78], worse.
  - **Vs persistence** (25 days, 9 runs; persistence MAE 0.78): at shelter 1.0, +0.43 [+0.06, +0.74]. At shelter 0.6, +0.60 [+0.25, +0.92]. Persistence is better.
- **Cumulative drift:** the engine falls short in all 10 runs. At shelter 1.0 the shortfall is 12.5–60.6%; at shelter 0.6 it is 22.4–64.0%. In run 2, 24.0 mm were observed and 13.1 mm predicted.
- **By weather regime:** the underprediction appears in every wind and VPD tercile. It is descriptive only, at 11–12 days per cell.
- **Post-hoc only, not a headline:** a single multiplicative factor k, fitted leave-one-run-out (1.58–1.79 at shelter 1.0), gives MAE 0.75. That beats the mean by -0.27 [-0.63, -0.02].
- **Tier:** A, limited.
- **Supported:**
  - It ranks days reasonably (r about 0.73).
  - It underpredicts systematically, by about 1.2–1.4 mm/day (about 40%).
  - It does not beat a climatic mean or persistence.
  - The default shelter of 0.6 makes the error worse on an open pan.
- **Not supported:**
  - accuracy on sheltered ornamental ponds
  - rain handling
  - the cause of the bias (missing radiation is a hypothesis)
  - anything beyond about a week

## `fit_water_air_temperature` / `predict_water_temp`
- **Method:** the engine's own 70/30 chronological split, run within each run on hourly means.
- **Runs:** 4 of the 12 runs could be fitted (runs 2, 4, 8 and 9). The other 8 have fewer than 72 usable hours and are Blocked.
- **Results** (RMSE in °C, 154 evaluation hours pooled):

| Run | Eval h | Model | 24 h persistence | Default offset |
|---|---|---|---|---|
| 2 | 62 | 1.24 | 3.49 | 2.11 |
| 4 | 26 | 0.82 | 0.43 | 1.33 |
| 8 | 40 | 2.21 | 1.19 | 2.86 |
| 9 | 26 | 0.86 | 1.97 | 2.26 |
| Pooled | 154 | 1.46 [0.84, 2.04] | 2.44 [0.97, 3.28] | 2.26 [1.72, 2.66] |

- **Tier:** B.
- **Supported:** the fit beats the default offset in all 4 runs.
- **Not supported:** being better than persistence. It wins in only 2 of 4 runs, and the pooled difference cannot be told apart.

## Blocked
- **`cumulative_loss_litres`, `loss_pct`, `EvaporationConfig` litres, `advance_state`, `project_forward`:** PAN has no area, volume or water-balance events.
- **`fit_shelter_factor` and top-up calibration:** there is no top-up log.
- **`cross_check_against_tds`:** PAN has no TDS.
- **Rain dilution:** rain is all zeros.
- **SBP/LBP pans:** their sign conventions are inconsistent.

## Deviations
- **How the script was run:** with `python -X utf8` and without `-I`, because matplotlib needs the python-dateutil from the user site. Only data files were read. This is recorded in MANIFEST.md.
- **Who wrote this report:** the main session, from the subagent's report. Its headline numbers were spot-checked against `results.json`.
