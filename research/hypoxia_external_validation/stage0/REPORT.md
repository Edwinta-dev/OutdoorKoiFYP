# Stage 0 REPORT

## 1. Round-2 reproduction: PASS on the headline rows, with a small unexplained drift in 3 booster rows
Re-ran final.py unchanged (except I/O paths, see MANIFEST) on FWI @4cfa138e, seed 20261022, Python 3.14.7 / pandas 3.0.6 / scikit-learn 1.9.1. Two runs gave byte-identical output (deterministic). Nothing tuned.

| Model (strict QC, 374 onsets) | Original (results/onset_summary_strict.csv) warned30 / % (CI) / FA per pond-day | Re-run | Match |
|---|---|---|---|
| B5 booster, prior-style | 25 / 6.7 (3.4-11.0) / 0.342 | identical | exact |
| **Onset logistic, pond-relative** | 49 / 13.1 (8.9-17.3) / 0.189 | identical (warned60 32, occupancy 0.053) | exact |
| Onset booster, pond-relative | 56 / 15.0 (8.8-20.9) / 0.267 | 55 / 14.7 (8.5-20.7) / 0.270 | diff -1 onset |
| Low-DO@+60 booster, core+pond-rel | 50 / 13.4 (8.4-18.9) / 0.225 | 50 / 13.4 (8.4-19.0) / 0.218 | warned30 same; FA 0.225 vs 0.218 |
| Low-DO@+60 booster, clock+pond-rel | 53 / 14.2 (8.7-20.3) / 0.193 | identical | exact |
| Night rule 18:00-06:00 | 48 / 12.8 (9.3-16.8) / 1.086 | identical | exact |
| Original hypoxia.py clock-proxy | 0 / 0 / 0 | identical | exact |

Also: events 374 and pond-days 596.4 match. warned60 differs on 2 booster rows (45 vs 43, 30 vs 31); ponds_with_any_warning 12 vs 11 on one row. Per-pond csv does not byte-match (119 rows, same shape) because of those rows.
Cause of difference (explanation, not tested): the three tree-booster rows depend on the scikit-learn/numpy version (gradient-boosting split ties and float order); the original library versions were not recorded. Logistic and rule rows are unaffected. The differences are within +/-1 onset and well inside the CIs. All numbers quoted in HANDOVER section 3 for 0%, 4.5% (round-1, not re-run), 13.1% (8.9-17.3), 0.189, 12.8%/1.086 and 374 onsets reproduce exactly.
Not re-run: the 11.8% / 0.156 row (22:00-06:00 gated booster; produced by nightgate.py, which was outside this task) and nowcast/night scripts. Relaxed-QC rows were produced by the same run (stage0/out/final_stdout.txt) but not compared to the original relaxed csv.

## 2. Integrity (details: stage0/integrity.md)
- FWI: PASS overall. Lag-1 autocorr DO 0.95-0.99 in all 17 ponds, pH 0.89-1.0 after zero removal (exceptions below), DO minimum at 06-07 h IST (pre-dawn/dawn) in 17/17, DO afternoon max, pH-DO positive in 17/17 (0.10-0.93; median ~0.85). Weak/suspect ponds: 3ed4df8e (pH-DO 0.10, 58 pH jumps >0.7), 29660d32 (0.31, known damaged pH bulb), 6ca69422 (83 pH jumps, 135 temp jumps >2 C; lag-1 pH 0.29), 573a2826 (99 DO jumps >5 mg/L). These are covered by the strict QC already; no new action but flag for WP1.
- Oman: FAIL as a pond-like DO/pH source. Open-pond file (Mar 26-Apr 27, only 904 five-minute bins with data): DO lag-1 0.86, hourly DO minimum at 21 h with a flat 04-06 h mean (8.0 mg/L), pH-DO correlation -0.55 (sign reversed). Live 180 L tank: DO min 22 h, pH-DO +0.19, DO never below 4.9. Temperature/pH continuity good (lag-1 ~0.997). Usable for sensor-stream/false-alarm stress, not for diel-cycle validation. Consistent with the "no retained low-DO events" statement.
- IDN-T (no DO): MIXED. Temperature clean (lag-1 0.997, no jumps). TDS fine in cleaned file. pH is noisy: several readings per minute differ by up to 0.7 (e.g. 7.60/6.92/7.30/6.96), 17 jumps >0.7 per 10 min in the cleaned file, 132 in raw; raw has pH up to 15.78 and TDS up to 1813. Cleaned file is clipped to pH 6.5-8.5 and TDS 245-495, i.e. it is processed. Row ids have 23 large gaps and ~6 rows/minute at times, so whether this is one probe or several is undocumented. Timezone undocumented.

## 3. Data inventory (for WP1)
| Dataset | Fields | Units | Cadence | Range | Timezone |
|---|---|---|---|---|---|
| FWI, 17 ponds (ara2_*) | DO, pH, temperature + QC_Flag_DateTime/DO/pH; side files: Key_Events, QC_Flags, ProDSS_and_Photometer (ammonia/photometer), monitor-vs-ProDSS comparisons | mg/L, pH, C | 15 min | 2025-11-28 to 2026-01-30 (each pond ~26-60 d; 73,150 rows) | IST |
| Oman raw offline (open pond, N. Al Sharqiyah) | timestamp, node_id, temp_c, ph, do_mgL, events | C, pH, mg/L | 5-7 s | 2026-03-26 to ~04-01 per dictionary; file spans to 04-27, 904 5-min bins | GMT+4 |
| Oman raw live (180 L tank, live tilapia) | same | same | 5-7 s | 2026-04-01 to 04-10, 2,279 5-min bins | GMT+4 |
| Oman aggregated_data | do/temp/ph mean,min,max; clock features; aerator_on, water_change, feeding, maintenance flags; reading_count; valid_window | mg/L, C, pH | 5 min | 897 windows, 2026-03-26 to 04-27 | GMT+4 |
| Oman events / validation log | event, note, start/end (42 events); forecasts vs actual DO (9,472) | | | | GMT+4 |
| IDN-T cleaned / raw | id, created_date, water_pH, TDS, water_temp | pH, TDS (ppm, unit unstated), C | irregular, ~1-10 min bursts (2,516 / 5,769 ten-min bins) | 2023-01-26 to 2023-03-22 | undocumented |
| PAN (not touched this stage) | src/pan/_meta.json only (a figshare Pan_evaporation_data 10329347 metadata record, files list empty) | | 10 min per description | 2016-2019 | not checked |

## 4. Blocked / caveats
- Nothing Blocked for downloads: IDN-T v2 is public on Mendeley via the official API, no login; hashes match the API.
- Blocked for WP1 on IDN-T: no DO, no pond identifier, undocumented timezone/units; any "state replay" there is input-handling only.
- Round-2 library versions not recorded, so the 3 booster-row drift cannot be pinned to a specific version change.
- src/pan/_meta.json was already present (not created by me); the Mendeley id yd36bx6f8f is a different dataset (IDN-T) from PAN.
- final.py takes about 2-3 minutes; I used no -I for the project scripts because they import sibling modules; data files were only read as CSV.
