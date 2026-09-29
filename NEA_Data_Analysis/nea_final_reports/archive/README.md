# Archive

The five files in this folder are source material from a separate, earlier NEA validation effort (an eleven-phase investigation covering telemetry, dosing economics, and forecast reliability) that was cross-checked against this project's own analysis while building `../UNIFIED_NEA_CHAPTER.md`.

That comparison surfaced a real defect (a station-network variance mismatch that had silently inverted a headline conclusion) and several validation techniques now folded permanently into `../../forecast_reality_pipeline.ipynb`.

`UNIFIED_NEA_CHAPTER.md` is the current, canonical write-up. These files are kept only for provenance and traceability of that reconciliation, not as a second source of truth. `eleven_phase_rain_weighting_script.py` in particular is not runnable as-is here: it depends on a `data_pipeline.py` and a `nea_validation_daily.csv` that were never part of this repository.
