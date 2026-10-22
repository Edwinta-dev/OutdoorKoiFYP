# OutdoorKoiFYP — Complete Model-Layer External Evaluation Plan

**Scope:** Every Python module and accompanying configuration in [`Backend/koi/models`](https://github.com/Edwinta-dev/OutdoorKoiFYP/tree/main/Backend/koi/models), not just `PondTwin`.  
**Status:** Design for experiments; no dataset-specific performance metrics are claimed.  
**Repository:** `Edwinta-dev/OutdoorKoiFYP`, `main`, inspected 22 October 2026. Re-pin a commit SHA before running experiments.  
**Goal:** Establish which behaviours are quantitatively validated, environmentally challenged, or verified as software. A successful replay does **not** prove a physical prediction accurate.

## 1. Evidence labels used throughout

- **A — Target validation:** A prediction is compared with an independent measurement of the **same target**, appropriately aligned in time and unit. Report error, calibration, intervals, baselines and site holdouts.
- **B — Proxy / consistency test:** Related, but non-identical, physical observables or controlled environmental treatments are compared. Report agreement, direction or treatment ranking; **not** error against an invented ground truth.
- **C — External replay / robustness:** Real historical records exercise input handling, missingness, gates, state updates and persistence. Report coverage, runtime, failure counts and invariants, **not** predictive accuracy.
- **D — Deterministic/software verification:** Unit/property/metamorphic tests with controlled fixtures (e.g. time order, event replay, mass conservation). Independent pond datasets are not required.
- **Blocked:** The necessary input or measured outcome is absent. Report this explicitly and run only a labelled scenario if scientifically useful.

## 2. Source catalogue (dataset IDs used below)

| ID | Dataset / source | Relevant available measurements | Key caveat |
|---|---|---|---|
| **IDN-T** | [Indonesia Aquaponic Fish Pond, Mendeley](https://data.mendeley.com/datasets/yd36bx6f8f/2); [methods paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC10293995/) | Time-indexed **pH, TDS, water temperature**, Jan–Mar 2023, raw and filtered releases; approximately 700 L water and 30 tilapia described | No DO, direct TAN/NO₂/NO₃, evaporation loss, weather, feeding history, lux or camera series; filtering to recommended ranges can distort anomaly rates. Verify exact raw file availability. |
| **IDN-V** | [Indonesia Fishpond Visual Condition v2](https://data.mendeley.com/datasets/rtsrk8792k/2) | Pond photographs, 100×100 crops, temperature, pH and TDS, different drone views/heights | No independent algae annotation; individual crops may share an original source photo; not automatically a same-view time series. |
| **FWI** | [Fish Welfare Initiative pond campaign](https://github.com/fish-welfare-initiative/Data-Campaign-Data) | 17 pond time-series; 15-min **DO, pH, temperature**; periodic TAN, chlorophyll-a, handheld DO/pH/temp; QC flags, some operational/cleaning events | Missing lux, green coverage, complete feeding histories, volume, and meteorological forcing. TAN is not directly equivalent to free NH₃. |
| **OMAN** | [Oman Nile Tilapia DO Forecasting](https://github.com/AhmedTheNetCoder/DO-Forecasting-Tilapia-Dataset) | Water temperature, pH, measured DO; open-pond and controlled-tank runs, raw/5-min aggregates, timestamped event logs | DO must be withheld from the predictor, including engineered DO lags. Limited sites/period; benchmark separately by open pond versus controlled tank. |
| **PAN** | [Pan Evaporation Data — Figshare](https://figshare.com/articles/dataset/Pan_evaporation_data/10329347) | 2016–2019 10-min meteorology, measured water level and water temperature | Water-level change needs rainfall/refill/overflow interpretation; evaporation pan is not a pond. Confirm units, forcing channels and level conventions before A-tier scoring. |
| **FAB** | [FABLIGHT filamentous algae experiments](https://zenodo.org/records/15669103); [dataset description](https://research.utwente.nl/en/datasets/datasets-for-light-and-temperature-as-triggers-for-surface-filame/) | Algae surface area, biomass, chlorophyll-a, nutrients, oxygen and experimental light/temperature treatments | PAR / DLI is not lux without spectral assumptions; filamentous algae != ESP32-CAM green-pixel ratio; temperate experimental treatments. |
| **MESO** | [Mesocosm chlorophyll/phycocyanin](https://zenodo.org/records/10520248) | Pigment-based algae-related time series under environmental treatments | Not visible surface green coverage; verify time-indexed files and experimental metadata. |
| **BRU** | [Urban clear/turbid ponds](https://zenodo.org/records/11103557) | Chemistry, chlorophyll-a, oxygen-related data and turbidity across ponds | Sampling frequency, temporal alignment and analyte type need inspection; not an intervention log. |
| **LOCAL** | Your own ESP32-CAM sequences, optical ammonia-card labels, NEA historical weather, Supabase sensor records and Experiment 4 interventions | Actual deployed camera features, weather, feed/dosing/action timestamps, sensor/quality flags | Not independent external validation; necessary for testing camera-specific event logic and matching production data contracts. |
| **SYN** | Frozen synthetic/hand-specified event and sensor fixtures | Fully controlled inputs, expected invariants | D-tier software verification, never field accuracy evidence. |

**Important:** The IDN-T and IDN-V releases are **different Indonesian datasets**; do not accidentally treat them as one synchronized stream. The denoised [2026 Indonesian derivative](https://zenodo.org/records/18883559) comes from IDN-T and is **not** an independent holdout.

## 3. Master matrix: every model file and what can be tested

| Repository file | Subcomponent / exported behaviour | Candidate data | Tier | Test and measurable output | What it cannot establish |
|---|---|---|---|---|---|
| `hypoxia.py` | `assess_hypoxia`, temperature/night rule, aeration/algae modifiers | OMAN, FWI; LOCAL for lux | **B/A*** | Score warning vs withheld observed low DO; event recall, false alarms/pond-day, lead time, sensitivity by temp band; compare night-only and simple temp baselines | Neither OMAN nor FWI supplies lux; any clock-imputed darkness is a **scenario** and **not unchanged model validation**. A-tier attainable for a redesigned DO-targeted predictor with valid deployable inputs. |
| `engine.py` | `SensorGate`, `RawSample`, channel verdicts, handling missing/erratic pH/TDS/temp/lux | IDN-T raw vs filtered, FWI QC, OMAN raw, LOCAL | **B/C/D** | Run gate unchanged; verify invalid/zero readings, jumps, gap effects, false rejection of plausible excursions; compare to independent FWI QC flags where applicable | Published QC/filtered labels are not exhaustive independently adjudicated truth; cross-domain sensor thresholds may differ. |
| `engine.py` | `WaterChemistryEngine`, feeding → TAN → NO₂ → NO₃ and pH NH₃ fraction | FWI periodic TAN, BRU nutrient observations, OMAN event logs, LOCAL ammonia-card | **B/C/D**, A only with full forcing | Perform **known-input-only** replay; run bounded feeding scenarios separately; compare trend directions and sparse TAN observations where inputs suffice; verify mass balances/non-negativity | Missing exact feed mass/protein, pond volume, biofilter and all inputs prevents identified quantitative concentration accuracy; free NH₃ card bands are not TAN reference values. |
| `engine.py` | chemistry `project_forward`, `rain_dilution_term`, risk scoring | LOCAL NEA + rain and events; FWI if rain/event data can be joined | **B/C/D** | Replay only forecasts issued before event; test monotonic dilution and nitrogen state sensitivity; compare time-to-threshold with available chemistry references | No independent NO₃ trajectory in IDN-T; rain dilution is untestable without rain + known depth/volume and chemistry. |
| `pond_twin.py` | `PondTwin` state aggregation; `ingest_sensor_inputs`, `ingest_environment` | IDN-T, FWI, OMAN, LOCAL | **C/D** | Replay at event time with missing channels explicit; compare single-pass vs chunked, snapshot/restart replay and output identity; count exceptions and lost data | A successful state update is not evidence that TAN, algae or evaporation values are physically correct. |
| `pond_twin.py` | atomic `apply_event`, `record_event`, `reconcile_events`, delayed-event replay | OMAN event logs, FWI key events, LOCAL, SYN | **C/D** | Inject documented events only where type/quantity known; late insertion, duplication, correction and reorder tests; snapshot hashes/invariants | A cleaning log without dose/volume cannot be converted to a precise model event. |
| `pond_twin.py` | `no3_projection`, severity-rating handling, depth/profile sync | LOCAL ratings; SYN; BRU nitrogen as loose ecological check | **B/D** | Projection determinism, profile-history semantics and undo/redos; quantify scenario spread | External datasets don't carry your user severity labels or full nitrate forcing. |
| `evaporation_engine.py` | `evaporation_mm_per_day`, physical water-loss formula | PAN | **A (conditional)** | Aggregate measured evaporation after rain/refill correction; MAE, bias, RMSE, daily/cumulative errors vs constant-rate baseline | Pan error is not directly ornamental-pond error; requires verifying dataset variables and units. |
| `evaporation_engine.py` | `advance_state`, `cumulative_loss_litres`, `loss_pct`, `project_forward` | PAN plus documented geometry | **A/C/D (conditional)** | Convert depth to volume using known area; plot cumulative loss and threshold-crossing lead-time error; test no negative volume | Not physical pond-volume validation without geometry, water-balance events and independent depth data. |
| `evaporation_engine.py` | water-air offset/lag calibration, `predict_water_temp`, `fit_water_air_temperature` | PAN (water and air temp), LOCAL NEA paired observations | **A/B** | Train early/forecast later, RMSE and bias vs naive persistence/offset, site/time holdout | Ambient air is not equivalent to pond water, calibration must not see target period. |
| `evaporation_engine.py` | shelter-factor fit and top-up calibration | PAN under known shelter and top-up; LOCAL recorded top-ups | **C/D**, A if actual top-up amounts | Leave-one-top-up-out prediction, parameter stability and uncertainty | Unlogged rainfall, drainage and refills invalidate calibration. |
| `evaporation_engine.py` | `feed_cap_grams_per_day`, temperature advisory | OMAN/IDN-T for water temperature; published species recommendations | **B/D** | Verify thresholds, units, output consistency and responses to high temperatures | No feeding outcome/clinical welfare labels: cannot claim reduced mortality or benefit. |
| `evaporation_engine.py` | `cross_check_against_tds` | IDN-T, LOCAL, PAN with independent TDS if any | **B/C** | Calculate agreement/conflict rates between TDS trend and modeled evaporation; do not infer water-loss truth from TDS alone | TDS changes also reflect dosing, waste, feed, sensor drift and rain. |
| `algae_engine.py` | `light_factor`, `temperature_factor`, `nutrient_factor`, `favourability` | FAB, MESO, BRU | **B** | Treatment ranking and monotonicity; compare predicted favourable regimes to observed bloom/biomass response | PAR→lux conversion is not straightforward; high pigment concentration is not the same as surface coverage. |
| `algae_engine.py` | `parse_image_rows`, `GreenSample`, camera `green_ratio` or GCC | IDN-V original/crops; LOCAL | **B/C** | Run production image feature extraction; annotate held-out image groups independently; precision/recall for **visible green appearance**, crop/height/daylight stability, uncertain-label rate | Cannot claim species-level algal identification or measured algal biomass without verified labels. |
| `algae_engine.py` | `gcc_daily_series`, `gcc_growth`, `gcc_zones`, `detect_camera_drift` | LOCAL fixed-camera longitudinal data; IDN-V grouped originals | **B/C/D** | Robustness to missing frames, reflection, viewpoint changes, time-of-day normalization, deliberate camera shift; cross-frame stability | IDN-V arbitrary drone crops must **not** be ordered as if repeated fixed-view days. |
| `algae_engine.py` | `fit_specific_growth_rate`, `refit_growth_rate`, `advance_state`, `project_forward` | FAB area trajectories where sufficiently sampled; LOCAL fixed camera | **A/B (conditional)** | Calibrate on early measured area; forecast later area and score error vs persistence; treat physical surface area as a **different observable** from green-pixel ratio | Do not equate FAB surface area directly to HSV coverage; assess environmental response separately where endpoints differ. |
| `algae_engine.py` | `calibrate_thresholds`, ratings, scrub/water-change response | LOCAL labelled frames/actions; SYN; FAB as hypothetical treatment | **B/C/D** | Calibrate only training groups; test held-out images and known scrub events; monotonic decrease after scrub event | Cannot validate intervention impact without before/after matched observations and recorded action magnitudes. |
| `sensor_inputs.py` | timestamp parsing, grouping, merging, freshness, time basis | IDN-T, OMAN, FWI, SYN | **C/D** | Replay across time zones and cadences; inject shuffled, duplicate and delayed rows; assert original event order and declared time basis | No water-quality prediction accuracy implied. |
| `tds_steps.py` | `detect_steps`, noise threshold, predicted water-change/salt steps | IDN-T, LOCAL; SYN logged water change or salt | **B/C/D** | Count detected steps and false alerts away from logged actions; test expected magnitude for known concentration/volume | IDN-T has no logged dosing, so unlabelled steps cannot be declared interventions. |
| `tds_prompts.py` | prompt candidate generation, rejection, event-ledger interaction | IDN-T, LOCAL, SYN | **C/D** | Temporal alert stability, prompt duplication, persistence, confirmation/rejection after logged volume changes | No independent intervention outcome for IDN-T. |
| `event_ledger.py` | `LedgerEntry`, IDs, checkpoint, prune and replay | OMAN partial events, FWI key events, LOCAL, SYN | **C/D** | Replay deduplication, correction, cancellation, out-of-order insertion; ensure identical final state and bounded checkpoint history | Operational events may lack numerical details; don't fabricate quantities. |
| `profile.py` | `PondProfile`, `ProfileHistory`, pond configuration at time t | IDN-T documented 700 L context, OMAN 180 L controlled tank, LOCAL, SYN | **C/D** | Check historical vs current profiles; deliberately change pond volume midway and prove earlier states unaffected | Physical parameter transferability to Singapore koi ponds not established. |
| `device_health.py` | contacts, missed cycles, battery, stale channels, confidence | FWI time gaps/QC, OMAN raw, IDN-T timestamps, LOCAL ESP32 battery logs | **B/C/D** | Gap detection recall against FWI `time_gap` QC, reliability by cadence, documented failure case flags; compare detected anomalies to QC/event logs | FWI probes are not ESP32 nodes, and battery voltage is not provided by all external sources. |
| `forecast_utils.py` | weather text/rain extraction, 24-h observed rainfall, outlook parsing, slope, TDS post-event series | LOCAL NEA historic/forecast archive + IDN-T for slopes; SYN payloads | **B/C/D** | Schema/units, exact issue-time backtests, text mapping, daily forecast timing and last-24h rain totals; verify TDS slope algebra | IDN-T alone has no weather; cannot validate meteorological forecasts or rain impact from it. |
| `ladder.py` | `react`, `preempt`, `window`, `nowcast`, `actions` | LOCAL archived NEA forecasts/observations, SYN controlled weather | **A/B/D (conditional)** | Compare historical advisory decisions against independently observed weather events; contingency tables, false alert rates, warning lead times and simpler baseline | Forecast issued AFTER event is leakage. Weather event forecasting skill ≠ fish survival benefit. |
| `uncertainty.py` | projection envelopes, crossing ranges, scenarios | PAN/FAB measured outcomes where comparable; LOCAL; SYN | **A/D (conditional)** | Test monotonic bounds and deterministic replay; if independent targets exist, score coverage of claimed predictive intervals | Scenario min/max is not a statistical confidence interval without probabilistic calibration. |
| `local_time.py` | local date/day keys and time-zone conversion | FWI IST, IDN WIB, OMAN local time; SYN DST/time-zone fixtures | **D/C** | Cross-midnight boundaries, time zone correctness, zero future leakage | Date correctness alone says nothing about model accuracy. |
| `ladder_params.json` | forecast advisory thresholds | LOCAL archived NEA data, SYN | **A/B/D** | Freeze thresholds before holdout; compare against simpler weather decision rules | Thresholds chosen on test records invalidate holdout performance. |
| `uncertainty.json` | uncertainty assumptions | PAN/FAB prospective hindcasts if output interpretable; SYN | **D/A conditional** | Verify configuration handling and coverage only if mathematically meaningful intervals exist | Cannot call scenario envelope a calibrated 95% interval by naming it so. |
| `__init__.py` | package export/import integrity | SYN/CI | **D** | Import and minimal invocation smoke tests | Not a predictive component. |

**A-tier note:** In the hypoxia row, only DO outcome scoring is direct; where darkness is manufactured using clock time, the unchanged deployed algorithm is not fully tested. In the algae and evaporation rows A-tier requires validation that independent observation and engine target really match.

## 4. Dataset → exact test battery

### IDN-T: three-month sensor replay (highest-value PondTwin/state test)
1. Load **raw** pH/TDS/temp by event time; record units, timestamps and gaps. Keep filtered/denoised data for a **secondary sensitivity comparison**, not ground truth.
2. Create a documented `PondProfile` from the published geometry (about **700 litres**), with all other unspecified profile fields recorded as unknown, not silently assumed known.
3. Run `sensor_inputs`, `SensorGate`, `tds_steps`, `tds_prompts`, `PondTwin` state snapshots and health/freshness checks on the **same rows**, recording per-component intermediate data.
4. Repeat with chunks, forced restart, out-of-order event delivery and duplicated rows: compare state snapshots, event ID sets and effective timestamps.
5. Only run chemistry/evaporation scenario branches when assumptions are explicitly provided, saving a scenario manifest. Exclude those outputs from accuracy scores.
6. Compare raw against filtered data to estimate preprocessing sensitivity. Do **not** use the externally filtered range constraints as independent sensor anomaly labels.

**Deliverable:** processing success, usable-channel proportion, invalid/gated fraction, gate-versus-QC limitations, suspicious TDS step count, snapshot-replay equivalence and scenario sensitivity curves.

### FWI: biological outcome and independent QC reference
1. Load each of 17 ponds separately. Use QC flags **per channel**; remove documented reset zeros; preserve the number of excluded samples and reasons. Use IST timestamps.
2. Hide continuous and handheld **DO** from any DO prediction input. Score hypoxia or proposed DO-free model against independently recorded DO events (not against predicted DO in another model).
3. Evaluate the strict original rule as `unknown` without lux. Run a **separately labelled** clock-derived light scenario, with aeration/algae modifiers unknown. Measure low-DO-event misses particularly at water temperatures below 30°C.
4. Compare periodic TAN **only** to a chemistry process with enough forcing inputs, or as loose trend/sensitivity evidence; report that TAN NH₃-N and colour-card **free NH₃** differ and require temperature/pH speciation.
5. Use handheld ProDSS readings, per-channel QC and cleaning logs for sensor/health consistency; inspect label reliability instead of treating every flag as ground truth.
6. Use chlorophyll-a to test broad biological relationship only, not green coverage or surface area.

**Deliverable:** event-level recall and warning lead time, false alerts per pond-day, performance by pond, TAN/chlorophyll feasibility analysis, channel-QC agreement.

### OMAN: alternate DO site and event-ledger stress
1. Partition the **open pond** and **controlled 180 L** runs; do not randomly shuffle time-neighbouring records.
2. Withhold DO, all lagged DO values, and any published feature that was calculated from DO. Baselines: temperature-only, pH+temperature, clock/time features, and past-input-trend model. Evaluate 0 h (nowcast), 30 min, 2 h, 6 h **where valid labelled targets exist**.
3. Use chronological holdout plus separate-site transfer. Compare against the paper's forecasts only as *published external benchmarks*, not your model's ground truth.
4. Feed only actual recorded events to `event_ledger` and `PondTwin`, and retain event quantity missingness. Verify no events from the future are ingested.

**Deliverable:** MAE for DO-regression models; low-DO event precision/recall, false alerts, lead time and transfer drop; event-ledger C-tier reproducibility.

### PAN: independent physical evaporation check
1. Inspect level convention and extract rain, air temperature, humidity, wind, radiation and water-temperature fields. If required meteorological drivers are absent, mark coverage and **do not silently impute measured weather**.
2. Construct an observation water balance (`evaporation = initial level - final level + rainfall - non-evaporation losses/adjustments`, sign-checked against documentation) for qualified intervals only.
3. Evaluate `evaporation_mm_per_day` unchanged with fixed parameters before any fitting. Compare daily MAE/bias/RMSE and accumulated water loss against persistence or climatic mean.
4. Test holdout water-air lag/offset calibration and, where justified, shelter-factor calibration, avoiding fitting and scoring on the same period.
5. Convert to litres only when the evaporating surface area is defined.

**Deliverable:** measured-versus-predicted evaporation graph, error by weather regime, cumulative drift and reproducible exclusion table.

### IDN-V: camera observable and nuisance robustness
1. Identify original-image grouping, crop locations, timestamps, pond and drone height; group train/test splits by original photograph or pond, **never independently by crops**.
2. Run unchanged production HSV and GCC feature extractors. Annotate a modest blinded subset for **visible greenness** and confound categories (duckweed/plants, reflection, brown turbidity, unclear), without turning hue into confirmed algae biomass.
3. Score categorical greenness detection against those human visual annotations; analyse disagreement and crop/height sensitivity.
4. Only run temporal `gcc_growth` if matching fixed-water regions have repeated, comparable dated imagery. Otherwise mark growth forecast **blocked** and test only snapshot optical robustness.

**Deliverable:** independent-image confusion matrix, threshold sensitivity shown only after baseline freeze, crop variability and illustrative true/false positives.

### FAB + MESO + BRU: algae physiology and cross-domain consistency
1. Examine actual FAB files for time-indexed surface area, treatments and available controls. Evaluate temperature/light **treatment ranking** for `favourability` without assuming a lux↔PAR equivalence.
2. If sufficient early/late surface-area observations exist, fit early `fit_specific_growth_rate` and predict later physical area; this is **an adapted physical-area experiment**, not validation of green-pixel-ratio predictions.
3. Compare `nutrient_factor`/favourability with nutrient and chlorophyll outcomes only as ecological association tests.
4. Test robustness to alternative growth measures (surface area, chlorophyll-a, fluorescence); do not merge them as the same target.

**Deliverable:** treatment ranking agreement, error on matched physical-area holdout when possible, and a table of incompatible observables.

### LOCAL weather, images and interventions: production-fidelity tests
1. Freeze an image sample and event/telemetry manifest from actual deployment logs (including known turbidity and ammonia-card cases).
2. Run existing camera pipeline to verify colour/quality-state processing; assess ammonia-card band behaviour **separately** because it is not an algae model target.
3. Replay NEA forecasts from their issue times and compare advisory actions with later observed events, preserving issue-time availability.
4. Replay logged water changes, feeding, dosing and top-ups using actual recorded amounts; test historical edits, deduplication and snapshot restore.

**Deliverable:** deployed-pipeline verification and forecast-advisory retrospective skill, kept distinct from external validation.

## 5. Cross-cutting rules that prevent misleading scores

1. **Withhold the target:** DO can only be used for label generation or scoring, never as predictor or within features calculated using DO; similarly future camera ratios and future water levels must be unavailable at decision time.
2. **Chronological grouping:** Split by pond/site/source photograph and time block. Avoid random row splits of autocorrelated IoT data.
3. **Freeze thresholds:** Run original engine and naive baselines first. Make any new parameter tuning a separate training exercise and evaluate on untouched sites/time periods.
4. **No fabricated sensor readings:** `lux=None` must remain unknown in a strict run. Day/night proxy, pH proxy, synthetic dosing or notional pond geometry belong only to named *scenario* runs.
5. **No fabricated ground truth:** Chlorophyll-a is not surface green coverage; TAN is not the ammonia card's free NH₃; pH/TDS are not nitrate or evaporation measurements; drone crop sequence is not temporal growth.
6. **Time-zone fidelity:** Normalize timestamps to aware UTC internally; retain original local time zone and daylight-derived features as-of each timestamp.
7. **QC accountability:** Record all dropped rows by reason, with outcomes with and without QC exclusion where meaningful.
8. **Baselines:** Simple nighttime warning, temperature-only, persistence, mean evaporation, environmental-only algae favourability, and eventless state replay are essential comparisons.
9. **Leakage audit:** Log feature availability time, label availability time and model version on every generated prediction.
10. **Reproducibility:** Commit SHA, source URL/version/hash, preprocessing rules, assumptions file, training/holdout partition IDs and random seeds accompany every run.

## 6. Prioritised work packages and deliverables

| Package | Modules covered | Data | Expected evidence | Relative priority |
|---|---|---|---|---|
| **WP1 — Instrumented state replay** | `pond_twin`, `engine` gates, `sensor_inputs`, `event_ledger`, `profile`, `local_time`, TDS modules, `device_health` | IDN-T + FWI + OMAN logs | C/D + some B | **1** |
| **WP2 — Independent hypoxia outcome** | `hypoxia` and candidate replacement | FWI + OMAN | A for DO-target predictor; B for original proxy scenario | **1** |
| **WP3 — Evaporation benchmark** | `evaporation_engine`, relevant uncertainty | PAN | A if corrected water balance sufficient | **2** |
| **WP4 — Camera robustness** | algae optics, GCC parsing, quality/drift handling | IDN-V + LOCAL | B/C | **2** |
| **WP5 — Algae environmental-growth response** | algae favourability, fitting, forecasting | FAB + MESO + BRU | B; A conditional for matched surface-area model | **2** |
| **WP6 — Chemistry/feeding scenario** | `WaterChemistryEngine`, event-aware projections | FWI TAN + OMAN events + LOCAL | B/C/D; quantitative validation probably blocked | **3** |
| **WP7 — Weather advice / uncertainty** | `forecast_utils`, `ladder`, JSON configs, `uncertainty` | NEA archived issue-time weather + LOCAL | A/B/D depending target | **3** |

## 7. Reporting template for every subcomponent

For each evaluated function, fill in:

```text
Module / function:
Repository commit:
Dataset and published version:
Input fields used (with units):
Required fields missing:
Assumptions / scenario modifications:
Target measured independently? Y/N; precise definition:
Temporal and pond/site splits:
Baseline:
Number of ponds / rows / events scored:
Metrics with confidence intervals or site spread:
Failure cases:
Evidence tier (A/B/C/D/Blocked):
Exact conclusion supported:
Exact conclusion NOT supported:
Saved artefacts (CSV, JSON, charts, run log):
```

## 8. Suggested Results chapter map

- **External data methods:** dataset sources, licences, measured quantities, replay assumptions and leakage prevention.
- **System replay integrity (IDN-T/OMAN/FWI):** event ordering, freshness, missingness, sensor gate and deterministic state restoration.
- **Hypoxia outcomes (FWI/OMAN):** withheld measured DO; unchanged existing heuristic and redesigned predictor distinguished.
- **Evaporation physics (PAN):** actual water-loss benchmark, corrected water balance and off-domain caveat.
- **Optical observables (IDN-V):** HSV/GCC robustness to unseen ponds/viewpoints and explicit missing algae ground truth.
- **Algae growth consistency (FAB/MESO):** treatment effects, area growth where available, non-transferable optical definitions.
- **Chemistry limitations and scenarios:** TAN reference availability but missing feeding/volume; no claim of unearned NO₃ accuracy.
- **Advisory and uncertainty verification:** NEA issue-time skill, scenario envelope semantics, input confidence and limits.

## 9. Bottom-line expected contribution

A defensible thesis outcome does **not** require every engine to earn an A-tier accuracy score. It requires each component to be evaluated using the strongest available evidence without swapping in proxies as if they were the actual target. The most compelling configuration is **independent measured-DO testing on FWI/OMAN; measured evaporation on PAN; image robustness on IDN-V; growth-treatment consistency on FAB; and cross-domain PondTwin replay on IDN-T.** Chemistry, intervention effects and algae surface forecasts remain explicitly conditional where the necessary forcing or comparable target is absent.

## References and starting links

- [Your model layer](https://github.com/Edwinta-dev/OutdoorKoiFYP/tree/main/Backend/koi/models)
- [FWI public pond campaign](https://github.com/fish-welfare-initiative/Data-Campaign-Data)
- [Oman tilapia dataset](https://github.com/AhmedTheNetCoder/DO-Forecasting-Tilapia-Dataset)
- [Indonesia time series](https://data.mendeley.com/datasets/yd36bx6f8f/2)
- [Indonesia drone pond images](https://data.mendeley.com/datasets/rtsrk8792k/2)
- [Pan evaporation](https://figshare.com/articles/dataset/Pan_evaporation_data/10329347)
- [FABLIGHT surface algae](https://zenodo.org/records/15669103)
- [Mesocosm fluorescence](https://zenodo.org/records/10520248)
- [Brussels ponds](https://zenodo.org/records/11103557)
