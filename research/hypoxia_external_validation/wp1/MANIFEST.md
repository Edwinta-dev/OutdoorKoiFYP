# WP1 MANIFEST

Seed `20261022` (the only random draws: delivery shuffles in `metamorphic.py`, the noise/random sequences in `syn_tests.py`; `numpy.random.default_rng(seed)` / `random.Random(seed)`). Everything else is deterministic.
Date of runs: 2026-10-22. Writes only under `wp1/`. Nothing in `stage0/`, `wp3/`, `fieldcheck/`, `Backend/`, `MobileUI/`, `supabase/` was touched. No network access, no Supabase / NEA / PythonAnywhere. No `pip install` was needed or run.

## Production code under test (read-only import)
- Worktree `C:\Users\AgenticDev\OutdoorKoiFYP_research`, `git rev-parse HEAD` at the start of WP1: `ff6db81e1fb51129b989e77ed4931a45f3308002` (later commits on the branch are research-only; `git status --short Backend` is empty before and after).
- Last commit that touched `Backend/`: `70b5cc1fe2f15bc6c46e1b1b350a0c45b5334690` ("issue #82: merge split cycles in replay; leave out-of-window late rows out").
- Imported modules (via `sys.path.insert(Backend)`): `koi.models.{sensor_inputs, engine, pond_twin, event_ledger, profile, local_time, tds_steps, tds_prompts, device_health}`. `koi.models` imports only the standard library and numpy here, so Backend's other dependencies (flask, supabase, opencv ...) were not installed. The in-memory storage adapter was **not** needed: the sensor ledger and the intervention rows are in-memory arrays (`harness.SensorLedger`, a list of dicts) that mimic `sensor_ingest_history` (migration 0029, no disposition filter) and `pond_interventions_since`.
- Interpreter: `src/.venv` Python 3.14.7, numpy 2.5.4, pandas 3.0.6 (`stage0/requirements_used.txt` lists the venv).

## Data (untouched, read as CSV only; paths under `src/`, gitignored)
Pinned by stage0 (`stage0/MANIFEST.md`): FWI `4cfa138e725db541e7ae14b89170909bef7dafb1` (https://github.com/fish-welfare-initiative/Data-Campaign-Data), Oman `d95207b6f129c3de1f426ec90eed5a38f46832b8` (https://github.com/AhmedTheNetCoder/DO-Forecasting-Tilapia-Dataset), IDN-T Mendeley `yd36bx6f8f` v2 (https://data.mendeley.com/datasets/yd36bx6f8f/2).
SHA-256 of the 27 files read are in `out/source_sha256.json` (script `hash_sources.py`); all 27 equal the hashes recorded in `stage0/MANIFEST.md` / `MANIFEST_fwi_sha256.txt`.
Files: `src/fwi/csv/ara2_*.csv` (17), `src/oman/data/raw/offline/raw_readings.csv`, `src/oman/data/raw/live/raw_readings_new.csv`, `src/oman/data/events/event_logs_new.csv`, `src/idn_t/pond_iot_2023.csv`, `src/idn_t/pond_iot_2023_raw.csv`.
Used as a label only, never fed to a model: FWI `DO (mg/L)` (only in step 4: the "DO moved inside a stale run" check), FWI `QC_Flag_*` columns, Oman `do_mgL` (read by the loader, dropped by the adapter).

## Assumptions (all named; none is a measurement)
| id | assumption | where |
|---|---|---|
| A1 | `created_at := sampled_at :=` the source timestamp as aware UTC (zero delivery latency, `time_basis == "sample"`); no `device_id/boot_id/cycle_seq`, so rows group by effective time (legacy path of `_group_key`) | `adapters.py` |
| A2 | FWI clock = IST (`Asia/Kolkata`, documented). Oman clock = GMT+4 (`Asia/Muscat`, documented). **IDN-T clock = UNKNOWN**: primary run assumes `Asia/Jakarta` (WIB, UTC+7), sensitivity run assumes the stamps are UTC | `adapters.py` |
| A3 | Channel mapping: pH -> `"pH"`, temperature -> `"temp"`, TDS -> `"TDS"`; **no `"LUX"` row is ever created; DO is never emitted**. NaN -> no row (absent) | `adapters.py` |
| A4 | FWI "zeros kept" (primary, rows as published, so the gate sees the README's reset-zero artefacts) and "zeros -> absent" (variant) | `adapters.fwi_source` |
| A5 | Profiles: IDN-T 700 L (matrix section 2/4, methods paper) and Oman tank 180 L (data dictionary) are attached as an undated `PondProfile`. All other volumes/biomass/fish are **unknown**. `PondConfig`/`PondProfile` need a positive volume and a biomass, so a **placeholder** (`PLACEHOLDER_VOLUME_L = 1000`, biomass 0 g) is used only to construct the objects; `test_harness.test_placeholder_volume_does_not_change_gate_or_ingest` shows gate, ingestion and state hash are unchanged when it is 500 or 2000. No chemistry value from these runs is scored | `harness.py` |
| A6 | Delivery: poll-style scans of 2000 rows ordered by `(created_at, id)` with the real `complete_groups` (`IngestConfig.batch_rows` default); the "single pass" reference of step 3 is ONE batch with all rows | `harness.prod_batches` |
| A7 | Expected node interval for `device_health`: FWI 900 s (documented 15 min), Oman native 6 s / 5-min grid 300 s (documented 5-7 s), IDN-T 900 s (the production default; the data are 1-10 min bursts). Thinned runs (`adapters.thinned`) keep only the rows of the last distinct sample instant per 15-min (or 5-min) bin: real rows, no averaging | `adapters.py`, `run_step2.py` |
| A8 | `PromptConfig(enabled=True)` (production default is disabled) so `tds_prompts` is exercised; `detect_steps` floors 10/20/40 ppm (20 = `PromptConfig.min_step_ppm`) | `run_step2.py` |
| A9 | Oman events: only the tank-window events that map to a production type are fed: 4 `WATER_CHANGE` (90 %, 90 %, 90 %, 100 %, percentage taken from the event name) and 6 `FEEDING` with **no quantity** (`food_grams=None`, which `apply_event` turns into 0 g). `Other`, `Actuator`, `Fish Weight`, `Maintenance` have no production type and are not fed. An event is visible to `reconcile_events` only from its `timestamp_logged` on | `adapters.oman_tank_events` |
| A10 | `harness.frozen_clock(now)`: while a poll cycle runs, `datetime.now()` inside `koi.models.engine/pond_twin` returns the cycle's `now` (the production poller passes wall-clock `now`; historical replay must pass data-time `now`). Reason: finding F1 (`from_snapshot` prunes against the real clock). Not a Backend edit (module attribute swapped and restored) | `harness.py` |
| A11 | SYN fixtures are hand-specified smooth series (`syn_tests.syn_table`) and hand-specified events; they never stand for field data | `syn_tests.py` |

## Files in `wp1/`
| file | role |
|---|---|
| `adapters.py`, `test_adapters.py` | step 1: source -> production sensor-row shape (`RowTable`, columnar because 1.5 M rows do not fit as dicts on this machine) + unit tests (7) |
| `harness.py`, `test_harness.py` | instrumented poll-style replay, state fingerprints, frozen clock + harness tests (6) |
| `run_step2.py` | step 2 single-pass replays (`out/step2/*.json`, `step2_summary_*.csv`, `verdicts/*.npz`) |
| `metamorphic.py`, `run_step3.py` | step 3A delivery variants and state comparison (`out/step3/step3_results.json`) |
| `syn_tests.py` | step 3B + step 5 SYN (D-tier) tests (`out/syn/syn_results.json`) |
| `run_step4.py` | step 4 B-tier agreement tables (`out/step4/`) |
| `run_step5.py` | step 5 chemistry invariants on external replays (`out/step5/`) |
| `reproducers.py` | standalone reproducers of the findings (`out/reproducers_output.txt`), `debug_variant.py` / `debug_ckpt.py` are the diagnostic scripts used to minimise mismatches |
| `build_tables.py` | writes `out/tables.md`, from which `REPORT.md` tables are taken |
| `hash_sources.py` | `out/source_sha256.json` |

## Reproduce (from the `hypoxia_external_validation` folder)
```
src/.venv/Scripts/python.exe -I wp1/test_adapters.py && ... wp1/test_harness.py
src/.venv/Scripts/python.exe -I wp1/run_step2.py fwi fwi0 oman thin idn     # ~4 min
src/.venv/Scripts/python.exe -I wp1/run_step3.py                            # ~25 min
src/.venv/Scripts/python.exe -I wp1/syn_tests.py
src/.venv/Scripts/python.exe -I wp1/run_step4.py ; ... wp1/run_step5.py ; ... wp1/reproducers.py ; ... wp1/build_tables.py
```
Machine: 16 logical CPUs, 13.9 GB RAM of which < 1 GB free during the runs (hence the columnar row store and the 2000-row batches).
