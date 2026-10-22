# PAN field check (Figshare 10329347) - 2026-10-22, field check, not the benchmark

Verdict: **GO-with-caveats for WP3. A-tier limited to `evaporation_mm_per_day` against ONE pan (SWP), about 40 dry days.**

Everything below was computed by `read_xlsx.py`, `pan_inspect.py`, `pan_plaus.py` in this folder (stdlib only, `python -X utf8 -I`, data path passed as an argument).

## What was downloaded
- Figshare article 10329347 has two versions. **v2 (current, 2020-07-01) is a metadata-only record with zero files.** The only data is **v1** (2019-11-19): `20191119_pan evaporation data.xlsx`, 534,566 B, md5 87feecefa93fb930b1f63751103812f3 (matches Figshare), file id 18810497. Saved as `src/pan/pan_v1.xlsx`.
- Licence CC BY 4.0. DOI 10.6084/m9.figshare.10329347.v2. Authors Qiu, Gao, Yan, Wang, Luo, Chen. No README, paper or methods text in the record; the whole description is one sentence ("2016 to 2019 ... recorded every 10 mins").

## Structure
One sheet, 5,760 data rows x 14 columns, two-row header. Columns: Date (Excel serial), Time (day fraction), Air tem. degC, RH %, Wind velocity m/s, Pa mmHg, Pa kPa, Rain mm, LBP / SBP / SWP level (mm), LBP / SBP / SWP water temp (degC). LBP, SBP, SWP are **never defined** (three different pans; my reading, unverified: L/S = large/small, B/W = buried/weather-station type).

**Biggest finding: this is not a continuous 2016-2019 series.** It is **40 "typical days" x 144 records (10-min, no cadence gaps)** in 12 separate runs: 2016-12-08/09, 12-11..19; 2017-06-02/03, 06-07..10; 2018-12-31, 2019-01-01; 01-07..09; 01-12..14; 01-22..27; 02-07..10; 03-17/18; 03-21/22; 03-28. (The workbook's saved path contains the Chinese words for "typical-day data", consistent with hand-picked days.) Within a run, midnight-to-midnight continuity is exact (SWP step 0.0 to -0.1 mm); across runs the levels jump (refill/reset, undocumented).

## Fields
| Wanted | Present? | Detail |
|---|---|---|
| Solar radiation | **NO** | no column |
| Humidity | yes | RH %, 38-99; `---` on 101 rows (2018-12-31..2019-01-09, about 20/day) |
| Wind | yes | m/s, 0-8, mean 0.75, mostly calm; measurement height not stated |
| Air temp | yes | degC 8.4-34.4; `#VALUE!` on 103 rows (same 5 days) |
| Pressure | yes (extra) | mmHg and kPa |
| Rain | channel yes, **all 0.0** | every selected day is dry, so rain handling is untestable here |
| Water level | yes x3 | mm, 0.1 mm resolution; see convention |
| Water temp | yes x3 | degC; SWP 7.4-36.8 |
| Pan geometry | **NO** | no diameter/area/depth |
| Refill/overflow markers | **NO** | only visible as level jumps between runs |

## Level sign convention (empirical, undocumented)
- **SWP** falls on all 40 days: 1.0-6.4 mm/day (mean 2.75), larger in June 2017 (3.6-6.4) than winter (1.0-3.7). Plain water-height convention. Usable.
- **SBP and LBP are inconsistent.** SBP falls 3-8 mm/day in 2016-17 but rises 0.3-7.5 mm/day on 2019 days; LBP rises 2-6 mm/day in 2016-17, falls in 2019, and has >2 mm 10-min steps (4-26 per day) in 2016-17. Could be distance-vs-height flips or pan/sensor changes. Cannot be resolved without documentation. **Do not use SBP/LBP.**

## Engine inputs vs PAN (`Backend/koi/models/evaporation_engine.py`)
| Function | Input | PAN field |
|---|---|---|
| `evaporation_mm_per_day` | `water_temp_c` | SWP water temp (col N) |
| | `air_temp_c` | Air tem. |
| | `relative_humidity_pct` | RH |
| | `wind_speed_ms` | Wind; use `wind_measured_at_10m=False` (height unknown, record as assumption) |
| | `shelter_factor` | none; run 1.0 (open pan) and default 0.6 as named variants |
| `predict_water_temp`, `fit_water_air_offset` | air and water temp series | Air tem. + SWP water temp (5 days have about 20 air gaps/day) |
| `fit_water_air_temperature` | hourly dicts; needs >=72 usable hours with all 13 lags, >=24 eval hours | build hourly means from 10-min; usable only inside runs (longest 9 and 6 days) |
| `EvaporationConfig`, `cumulative_loss_litres`, `loss_pct`, `advance_state`, `project_forward` | volume, depth, area | **missing** (no area; depth only as mm level) |
| `DayEnvironment.rain_*`, rain category table | rain | channel exists but is 0 throughout: **untestable** |
| `fit_shelter_factor`, `TopUpSample` | top-up litres | **missing** (no refill log, no area) |
| `cross_check_against_tds` | TDS | **missing** |
| (no radiation term in engine) | - | PAN has no radiation either |

## Quick plausibility (informal, not scored)
- Observed SWP daily drop (first minus last record of the day, mm): 1.0-6.4, mean 2.75. Plausible for pan evaporation.
- `evaporation_mm_per_day` (10-min Penman mass-transfer, averaged over the day) vs SWP drop, 40 days: shelter 1.0: bias -1.15, MAE 1.22, RMSE 1.43, r=0.74. Shelter 0.6: bias -1.37, MAE 1.40, RMSE 1.64, r=0.75. A constant-mean baseline has MAE 0.89 (in-sample, flatters it). So the formula tracks day-to-day variation but **underpredicts by roughly 40%** (expected without a radiation term), and the 0.6 default shelter worsens it. Whether it beats persistence or a climatic mean on a proper holdout is open.
- Diurnal water temperature (SWP, mean over days): minimum 07:00 at 17.5 C, maximum 15:00 at 25.4 C; air peaks 14:00 (24.1 C). Water lags air by about 1 h. Plausible.
- Plumbing run of `fit_water_air_temperature` on pooled hourly data (all runs concatenated, time-ordered 70/30 split): SWP water: lag 2 h, offset +0.21 C, eval RMSE 1.56, persistence 1.36, default -1 C offset 2.51. It does not beat persistence. This is a mechanics check, not pond evidence.

## Verdict and reasons: GO-with-caveats
1. Only SWP is a usable level; **about 40 days across 3 seasons, all dry**. No rain regime for error-by-weather-regime; cumulative drift only within 12 short runs (<=9 days).
2. No area/geometry: mm-level evaporation only. Litres, `advance_state`, `loss_pct`, threshold lead-time stay Blocked (or scenario-only).
3. No radiation: bias cannot be attributed. Rain, refill, shelter, top-up and TDS branches are Blocked.
4. Wind height and the identity of SWP/LBP/SBP are undocumented; a pan is not a pond (matrix caveat).
5. Exclusion table to log: 103 air and 101 RH rows (2018-12-31..2019-01-09), run boundaries, SBP and LBP.
6. Before WP3, try to find the source paper (Qiu et al.) for pan definitions; the record has none.
