# hx_robust REPORT: robustness of the frozen 22:00-06:00 night-gated hypoxia model (FWI, unseen ponds)

Reference fold seed 20261022; extra fold seeds 20261023-25. Python 3.14 venv, scikit-learn 1.9.1, pandas 3.0.6 (as stage0). stage0/ untouched. All numbers come from scripts in this folder (outputs in `out/`). DO is used for labels only.
Evidence tier: **B** (measured DO on FWI, unseen-pond CV; one region and season, design iterated on the same data, so not A). Oman item: stress test only, no accuracy tier.

## 0. Method notes that change how earlier numbers read
- **nightgate.py gate bug:** it sets gated scores to 0 on all off-window slots, including ineligible (NaN) ones, so those slots count as monitored. Pond-days become 703-743 instead of 596, so FA/pond-day in the gated rows of `night_gate_summary.csv` is understated by about 20-25%. `robust.py` keeps NaN as NaN (596.4 pond-days in every row here).
- nightgate.py gates are `hour < 7` ("22-07"), not 22:00-06:00.

## 1. Reproduction of nightgate.py (seed 20261022)
Re-ran unchanged except the output path (`HX_OUT`). `diff` vs `results/night_gate_summary.csv`: **the 4 logistic rows are byte-identical; the 8 booster rows differ** (same kind of drift as stage0; round-2 library versions unrecorded).

| Row | Original: warned / % / FA per day | Re-run |
|---|---|---|
| Onset logistic, 24h | 49 / 13.1 / 0.189 | identical |
| Onset logistic, 22-07 | 59 / 15.8 / 0.234 | identical |
| Onset booster, 24h | 56 / 15.0 / 0.267 | 55 / 14.7 / 0.270 |
| Onset booster, 22-07 | 64 / 17.1 / 0.270 | 62 / 16.6 / 0.258 |
| Low@+60 booster, 24h | 50 / 13.4 / 0.225 | 50 / 13.4 / 0.218 |
| Low@+60 booster, 22-07 | 56 / 15.0 / 0.174 | 58 / 15.5 / 0.176 |

**The "11.8% / 0.156" row (44/374, 93 FA) is not in `night_gate_summary.csv` and nightgate.py cannot produce it.** It matches the 22-06 row of `results/gate_tradeoff.txt` (fixed-threshold, post-hoc gating of the no-gate model), made by a script that is not in stage0/code. Status: not reproduced. Closest run here (22-06 gate, threshold re-selected on validation ponds, seed 22): 52/374 = 13.9% (8.8-19.9) at 0.194/day.

## 2. Frozen gate and threshold, new fold seeds (only fold assignment changes)
Frozen in `frozen.json` (commit 84eb151, made before these runs): Low-DO@+60 min booster, all features, gate 22:00<=t<06:00, selection rule (<=0.177 FA/pond-day and <=25% occupancy on validation ponds; maximise warned30), hyperparameters, label 3 mg/L. Round-2 per-fold thresholds on seed 22: 0.522, 0.442, 0.860, 0.596, 0.690; median 0.596.
A threshold can be "frozen" two ways, so both are reported, with no retuning:
- **R (rule):** threshold re-selected on the new validation ponds by the frozen rule (the round-2 procedure).
- **N (number):** the constant 0.596 applied to all test ponds.
Comparator: same model, no gate, rule R.

Warned >= 30 min (of 374 onsets) and false alerts per pond-day (95% pond-bootstrap CI); 596.4 pond-days.

| Fold seed | R: gated, rule | N: gated, fixed 0.596 | No gate, rule |
|---|---|---|---|
| 20261022 (ref) | 52 = 13.9% (8.8-19.9); 0.194 (0.082-0.325) | 46 = 12.3% (8.0-16.7); 0.173 (0.077-0.292) | 50 = 13.4% (8.5-19.3); 0.218 (0.090-0.355) |
| 20261023 | 47 = 12.6% (6.9-18.7); 0.181 (0.086-0.306) | 67 = 17.9% (11.6-24.9); 0.198 (0.107-0.315) | 39 = 10.4% (4.2-17.4); 0.161 (0.059-0.304) |
| 20261024 | 38 = 10.2% (5.5-16.2); 0.210 (0.088-0.343) | 43 = 11.5% (6.5-17.3); 0.171 (0.074-0.276) | 34 = 9.1% (4.4-15.4); 0.178 (0.060-0.302) |
| 20261025 | 50 = 13.4% (8.3-18.3); 0.267 (0.141-0.401) | 43 = 11.5% (6.0-17.9); 0.216 (0.102-0.350) | 41 = 11.0% (5.6-17.4); 0.308 (0.145-0.482) |
| Mean of 4 seeds | 12.5%; 0.213 | 13.3%; 0.190 | 11.0%; 0.216 |
| Range, 3 new seeds | 10.2-13.4%; 0.181-0.267 | 11.5-17.9%; 0.171-0.216 | 9.1-11.0%; 0.161-0.308 |

Reading: a warning yield of roughly 10-14% survives re-folding, but the round-2 value (13.9%) is at the top of the R range, and seed-to-seed spread (about 3-4 pp under R, up to 6 pp under N) is as large as the CI half-widths. The selection budget of 0.177 FA/pond-day is exceeded on test in 4 of 4 seeds under R on the point estimate (0.181, 0.194, 0.210, 0.267), although every CI includes 0.177. Gate vs no gate: yield is higher with the gate in 4/4 seeds (+0.5 to +2.4 pp) but within noise, and FA is mixed. Per-fold validation thresholds under R range 0.27-0.89 (`out/step2_seeds.csv`), so threshold selection on 3-4 validation ponds is noisy.

## 3. Severity sensitivity (labels only: low DO < 2 mg/L; seed 22; same procedure; features identical)
Scored onsets: 374 -> 288. Low-bin prevalence 22.1% -> 13.1%. A false alert is now "no <2 mg/L in the next 2 h".

| Model | <3 mg/L: warned30 / FA per day | <2 mg/L: warned30 / FA per day |
|---|---|---|
| Onset logistic, no gate | 49/374 = 13.1% (8.7-17.2) / 0.189 | 33/288 = 11.5% (6.4-17.1) / 0.228 (0.103-0.386) |
| Low@+60 booster, 22-06 gate (R) | 52/374 = 13.9% (8.8-19.9) / 0.194 | 28/288 = 9.7% (5.4-14.2) / 0.154 (0.063-0.249) |
| Night clock rule 18-06 | 48/374 = 12.8% / 1.086 | 25/288 = 8.7% (6.2-11.9) / 1.180 |
| Legacy hypoxia.py (night and >=30 C) | 0 / 0 | 0 / 0 |

Reading: yield drops about 2-4 pp at the stricter label, CIs overlap fully, the pattern (models near the night rule yield at about one sixth of its false alerts) is unchanged, and the legacy engine stays at 0. I do not claim that severe events are more or less detectable.

## 4. Failure cases (seed 22 out-of-fold scores, frozen gated model; `out/failure_*.png`)
Panels: DO (label only), pH, temperature, gated alert score with threshold; red lines = scored onsets, purple dashed = alert starts, shading = 22-06 gate.
- **A. ara2_0677080b, 14-16 Dec** (`failure_A_0677080b.png`). The file starts 14 Dec 02:15, so the 14 Dec 04:00 episode (DO down to 0.9) has no 2 h history, no eligible score before 04:15 and is not a scored onset: a warm-up/data-start gap. 15 Dec: alert 04:15 for onset 04:45 (30 min lead, counts as warned). 16 Dec: alert 00:30; DO sits at 2.97-3.6 around 00:15-00:45 and is not counted as a scored onset (it does not pass the onset definition pre-window check). pH is 8.45-8.55 throughout (diel range about 0.35) while DO swings 0.3-18 mg/L; the score crosses the threshold only 0-1 h before DO nears 3. This fits pH moving with DO, not ahead of it (hypothesis). Pond total: 2 of 24 onsets warned, 1 false alert.
- **B. ara2_0f143c64, 18-20 Dec** (`failure_B_0f143c64.png`). False-alert-cluster pond: 35 false alerts and 0 of 8 onsets warned (35 of 116 seed-22 false alerts, `out/events_seed22.csv`). DO is supersaturated by day and stays above 5 mg/L at night here, yet pH falls overnight, so pond-relative pH-drop features rise (score 0.69, alert 19 Dec 05:00). A normal overnight pH decline in a high-DO pond is read as a hypoxia pattern.
- **C. ara2_3eab831e, 20-21 Dec** (`failure_C_3eab831e.png`). 0 of 32 onsets warned, 0 alerts. pH moves only 8.90-9.05 (0.01 resolution) while DO falls from 20 to 0.5 mg/L; score peaks at 0.52 and 0.67 against a fold threshold of 0.86. The 20 Dec onset (06:30) is also outside the 22-06 gate (score forced to 0 from 06:00). Low-amplitude pH ponds are missed.
- **D. ara2_858b914c, 6-9 Dec** (`failure_D_858b914c.png`; mostly a success with a false alert). Alert 04:45 on 7 Dec for onset 06:45 (120 min lead). An alert on 8 Dec 05:00 is a false alert (score 0.49 against this fold threshold); the pond has 7 warned onsets and 6 false alerts in total.
Common thread (observed in the plots, not tested): alerts that work come late in the night in ponds with a clear diel pH amplitude; the score typically crosses threshold close to onset.

## 5. Oman: false-alarm stress only (no recall scored; DO not read)
Frozen model fit on all 17 FWI ponds (random_state 20261022), threshold 0.596 (median of round-2 fold thresholds), gate on local (GMT+4) clock hours. 15-min means of pH and temperature.

| Dataset | Eligible monitored days | Gated 22-06: alerts (per day) | No gate: alerts (per day) | Alert hours, gated |
|---|---|---|---|---|
| Open pond (26 Mar-27 Apr file) | 2.65 (254 bins; only 307 of 3,083 bins have pH and temp) | 4 (1.51) | 6 (2.27) | 1, 4, 5, 22 |
| Live 180 L tank (1-10 Apr) | 7.81 (750 bins) | 3 (0.38) | 5 (0.64) | 2-4 |

Caveats: per stage0 integrity Oman has no diel DO minimum and no retained DO < 3 mg/L, so an alert here is treated as a false alarm only on that basis (assumption, not scored). The open-pond sample is about 3 nights (rate imprecise); the tank is a different system. FWI selection budget for comparison: <=0.177 per pond-day. Stress result only, not accuracy evidence.

## 6. Verified vs hypotheses
Verified (scripts run here):
1. nightgate.py logistic rows reproduce exactly; booster rows drift by <=2 onsets.
2. The 11.8%/0.156 row is not reproducible from nightgate.py, and nightgate.py gated FA/day is understated (pond-day denominator).
3. Frozen 22-06 gated booster: 10.2-13.9% warned >=30 min at 0.18-0.27 FA/pond-day over 4 fold seeds under rule R.
4. At the <2 mg/L label: 9.7-11.5% on 288 onsets, same conclusion.
5. Failure modes seen: low pH-amplitude ponds missed, high-DO ponds over-alerted, warm-up and gate edges cost onsets.
6. Oman: the frozen model alerts 0.4 (tank) to 1.5 (sparse pond) times per monitored day when gated.

Hypotheses (not tested): pH tracks DO concurrently; the gate gives a small real gain (positive at 4/4 seeds, within noise); more validation ponds would stabilise thresholds; transfer to Singapore koi ponds.
A dependable early-warning claim is still not supported.

## 7. Limitations
Fold seeds share the same 17 ponds, so the four runs are not independent. 374 onsets, one region and season. The 22-06 gate came from looking at round-2 results, so freezing tests fold sensitivity, not a clean holdout. Round-2 library versions were not recorded.