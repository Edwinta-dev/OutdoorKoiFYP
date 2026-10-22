# WP3 MANIFEST
- Branch research/hypoxia-external-validation; commit before WP3 44e76a58e8d6ef809e8ebccb9e57272bd247ee12 (engine file last changed d75e32f6). Seed 20261022, 5000 bootstrap reps (run-level).
- Engine (read-only, unchanged): Backend/koi/models/evaporation_engine.py, SHA-256 baafc94eedca9ef71454cac01c1947be9eada860459f407598c0ba5af180ca74
- Data: Figshare 10329347 v1 (https://ndownloader.figshare.com/files/18810497, CC BY 4.0), md5 87feecefa93fb930b1f63751103812f3. SHA-256 verified against fieldcheck/MANIFEST.md: pan_v1.xlsx e9b5ed77a6ee007f2d83b7acd095fefea3730b9de119042958ed88e594c411d8 (match); _meta.json db5a4207bcd73ca7... and _meta_v1.json 610d7872d701b6cc... (match). rows.json (derived by fieldcheck/read_xlsx.py) 62aeb4e237a699f52615ce6cbbe518ee21a158da802d826bef1bec788962784c. Data is gitignored, not committed.
- Script: run_wp3.py SHA-256 fcb3ae9513ff2f9a83d434c25945c7f3b05c89e1c7fda582a83c3b03b31ede7a
- Reproduce: `python -X utf8 wp3/run_wp3.py src/pan/rows.json wp3` (numpy, matplotlib; not run with -I because matplotlib needs the user-site dateutil; the script reads rows.json as data only and runs no downloaded code).
- Outputs: metrics.csv daily.csv exclusions.csv runs.csv regimes.csv cumulative_daily.csv cumulative_final.csv temp_fit.csv posthoc_factors_sf1.0.csv posthoc_factors_sf0.6.csv results.json fig_pred_vs_obs.png REPORT.md
- No network access, no Backend/MobileUI/supabase edits, stage0/ untouched.
