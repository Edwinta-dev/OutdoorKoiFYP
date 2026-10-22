# Handover — OutdoorKoiFYP external validation (hypoxia focus)

Date: 22 Oct 2026. Production repository **not modified**. All work is offline research code.

> ⚠️ This was run in an ephemeral cloud container. Download `hx_validation_bundle.zip` (results + code + this doc) or commit it to a `research/hypoxia_external_validation/` branch before the session ends.

## 1. Pinned sources
| Source | Revision |
|---|---|
| OutdoorKoiFYP | `52d12764e2c8327beabe42effc767b3b6304863e` |
| FWI Data-Campaign-Data | `4cfa138e725db541e7ae14b89170909bef7dafb1` |
| Oman DO-Forecasting-Tilapia | `d95207b6f129c3de1f426ec90eed5a38f46832b8` |
| Indonesia (Mendeley yd36bx6f8f v2 / Kaggle mirror) | covered in the round-1 report, not re-run |

## 2. What was done
1. **Round 1** (earlier agent, `Hypoxia_Validation_Report.html`): dataset audit, original-engine replay, baselines, Oman/Indonesia stress tests. Headline result: 14/310 onsets (4.5%) warned ≥30 min ahead, at 0.177 false alerts per pond-day.
2. **Round 2** (this work): reimplemented the same protocol and added **pond-relative pH/temperature features**. Evaluated on unseen FWI ponds with 5-fold group CV and thresholds chosen on validation ponds.
3. Dataset search, plus integrity screening of three candidate datasets. All three were rejected.
4. Night-gating analysis (fixed-threshold trade-off).

## 3. Key results (verified, FWI, unseen ponds, strict QC, 374 scored onsets)
| Configuration | Warned ≥30 min | False alerts / pond-day |
|---|---|---|
| Existing `hypoxia.py` (clock-proxy lux) | 0% | 0 (water ≥30 °C in only 0.01% of readings) |
| Round-1 comparator | 4.5% | 0.177 |
| Onset logistic, pond-relative | 13.1% (CI 8.9–17.3) | 0.189 |
| Low-DO@+60 booster, pond-relative, 22:00–06:00 gate* | 11.8% | 0.156 |
| Night clock rule 18:00–06:00 | 12.8% | 1.086 |

\*The gate window was picked by looking at test results, so treat it as a sensitivity analysis, not a validated setting.

Other verified points:
- Low DO occurred on 76% of nights (<3 mg/L). The median first low reading is ~03:30, and ~25% of onsets fall between 05:00 and 07:00.
- Detecting low DO now: AUC 0.90 / average precision 0.71 with pond-relative features, vs 0.85 / 0.57 for the clock alone. Raw pH and temperature add nothing over the clock.
- Predicting at 19:00 whether tonight will be bad is weak (AUC 0.59–0.63).
- The models' false alerts already fall at 22:00–07:00. Night gating removes few of them, and a 23:00–05:00 gate costs ~1 real warning for every 3 false alarms removed.

Hypotheses, **not** established:
- pH moves concurrently with DO, not ahead of it, which caps lead time.
- Lux, aeration and algae data would help separate bad nights from safe ones.
- Results transfer to Singapore koi ponds.

## 4. Datasets screened
| Dataset | Status | Reason |
|---|---|---|
| FWI India (17 ponds) | **Used**, the only supervised hypoxia test | — |
| Oman tilapia | False-alarm/transfer stress test only | No retained low-DO (<3 mg/L) events after quarantine |
| Indonesia aquaponics | Input-handling and domain-shift test only | No DO measured |
| Multiclass fishpond (2,153) | Rejected | Consecutive readings uncorrelated (lag-1 ≈ 0); labels unrelated to DO |
| Montería, Colombia (IoTMLCQ 2024) | Rejected | ~360 distinct values in 4,383 hourly rows; DO never below 6.34; all rows "Safe" |
| Kaggle `apgopi/pondsdata` | Rejected | No day–night cycle; 5 °C jumps within 20 min; pH–DO correlation sign reversed; station 3 is pure noise |
| Acton Lake (GEISHA) | Not tried | Lake, not a pond; optional out-of-domain check only |

Integrity checks to reuse on any new dataset (~10 min):
- lag-1 autocorrelation per site (expect >0.9 for 5–20 min sampling)
- hourly DO profile (expect a pre-dawn minimum)
- within-site pH–DO correlation (expect positive)
- count of distinct values per variable, and checks for impossible jumps

## 5. Files (`/home/user/hx/`)
- `results/FINDINGS.md`: full round-2 write-up, including an FYP paragraph
- `results/onset_summary_strict.csv`, `onset_summary_relaxedQC.csv`, `onset_per_pond_strict.csv`
- `results/night_gate_summary.csv`, `gate_tradeoff.txt`, `nowcast_unseen_pond.txt`, `night_level.txt`, `night_table.csv`
- `results/fig_warning_vs_fa.png`
- `results/code/`, as run from `work/`:
  - `load.py`: FWI loader and QC
  - `harness.py`: features, episode labels, alert policy, metrics, CV
  - `final.py`: main evaluation and bootstrap
  - `nowcast.py`, `night.py`, `variants.py`, `nightgate.py`, `fig.py`

Reproduce:
1. Clone the three repositories into `src/` at the pinned SHAs.
2. `pip install scikit-learn pandas matplotlib`
3. From the folder holding the scripts, run `python final.py`, then `nowcast.py`, `night.py` and `nightgate.py`. `load.py` hard-codes `SRC`; edit that path as needed.
4. Seed: 20261022.

## 6. Next steps — hypoxia
**Decision (recommended):** keep hypoxia in the digital twin, redesigned. Don't present it as an early-warning predictor.

1. **Code change** (needs your approval; do it on a branch, not `main`). About 0.5–1 day.
   - Replace the ≥30 °C gate in `Backend/koi/models/hypoxia.py` with a **standing night advisory**, about 22:00–06:00 local: "DO is not measured; low oxygen is common before dawn; aerate overnight."
   - Add a pond-relative pH indicator that runs in **shadow mode** only: logged, not shown to users.
   - Return `unknown` during the first 24 h of history and whenever pH or temperature is stale. Reuse the freshness rules from `sensor_inputs.py`, and make the worker and API paths agree.
   - Keep the legacy fields `level`, `flagged`, `explanation` and `advice`. Add `model_id`, `horizon`, `support` and `issue_time`.
   - Keep the bounded feature history in snapshots, with versioning. Old snapshots should load with empty state and return `unknown` until warmed up.
   - Unit tests:
     - no DO input is accepted
     - future-data invariance
     - warm-up returns `unknown`
     - gate boundaries cross midnight in Asia/Singapore
2. **Report section** (1.5–2 pages, about 1 day):
   - method
   - the results table above
   - the trade-off figure
   - the dataset screening table
   - limitations: one region and season, probe DO taken as truth, gate chosen post hoc
3. **Optional analysis to make the conclusions more robust** (cheap, 0.5 day each):
   - Freeze the 22:00–06:00 gate and threshold, then re-run with a *different* fold seed to test whether it holds up.
   - Repeat at the 2 mg/L threshold (severity sensitivity).
   - Write up 3–4 failure cases, e.g. `ara2_0677080b`, 14 Dec.
4. **Not worth further effort now:** more ML complexity, more searching for public datasets (screening has been done thoroughly), or the dusk "bad-night" model.

## 7. Next steps — other engines
From `OutdoorKoiFYP_Complete_Model_Validation_Matrix.md`. The earlier effort estimates still apply.

| Work package | Action | Agent effort | P(credible) | Notes |
|---|---|---|---|---|
| WP1 state replay (IDN-T / FWI / OMAN) | **Do** | 1.5–3 d | 85–90% | Reuse `load.py` for FWI. Folds in chemistry mass-balance and non-negativity tests. |
| WP3 evaporation (PAN) | **Do, after a 1–2 h field check** | 2–4 d | 50–60% | The only other likely A-tier result. First confirm radiation, humidity, wind, rain and level fields exist. |
| WP4 camera (IDN-V) | **Agent runs the pipeline; you label** 200–400 crops blind | 2–3 d + 4–8 h of your time | 60–70% | Split by original photo; freeze thresholds; no growth analysis on drone crops. |
| WP5 algae (FAB) | Treatment-ranking slice only | 1–1.5 d | 40–50% | Don't treat PAR as lux, or surface area as green-pixel ratio. |
| WP6 chemistry / TAN | **Write up as a limitation; don't build** | — | ~15% quantitative | The FWI TAN→free-NH₃ conversion is already explored. It adds nothing beyond a measurement-availability control. |
| WP7 weather ladder (NEA) | Only if issue-time forecasts were archived | 2–4 d | 30–50% | Using forecasts published after the event is leakage. |

Suggested order: WP1 together with the PAN field check → WP3 → WP4 (with your labelling) → the WP5 slice. Hypoxia code and report changes can run in parallel.

## 8. Rules any follow-on agent must keep
- DO (and anything derived from it) is **labels only**, never an input to the deployable model.
- Split by pond/site and by time; never random row splits. Choose thresholds on validation ponds only.
- Don't fabricate lux, feeding, volume or geometry. Any such assumption goes in a named *scenario* run, kept out of accuracy scores.
- Report evidence tiers A/B/C/D/Blocked and separate verified findings from hypotheses.
- Don't modify `Edwinta-dev/OutdoorKoiFYP` or Supabase without explicit approval.
