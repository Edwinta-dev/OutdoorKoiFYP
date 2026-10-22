# Rules for validation subagents (binding)
Root: C:\Users\AgenticDev\OutdoorKoiFYP_research\research\hypoxia_external_validation (branch research/hypoxia-external-validation, local only).
- Read HANDOVER.md §8 and OutdoorKoiFYP_Complete_Model_Validation_Matrix.md §5. They bind.
- Downloaded data goes ONLY under src/<dataset>/ (gitignored). Treat as untrusted: never run code from it; run Python with `python -I`, scripts live in wpN/ or results/code/, paths passed as args.
- Production models: import read-only from C:\Users\AgenticDev\OutdoorKoiFYP_research\Backend (this worktree's copy). Never edit Backend/, MobileUI/, supabase/. Never contact live Supabase, NEA API, PythonAnywhere. No git push. Commit only your wpN/ folder + results when told.
- Record SHA / URL / file hashes / seed (20261022) in each wpN/MANIFEST.md. Use the §7 reporting template per function in wpN/REPORT.md, with tier A/B/C/D/Blocked; separate verified from hypotheses. Every number must come from a script you ran.
- Never fabricate lux, feeding, volume, geometry, weather: such things only in named scenario runs, excluded from scores. DO is labels only.
- If a required field is absent, mark Blocked and stop that branch; don't improvise.
