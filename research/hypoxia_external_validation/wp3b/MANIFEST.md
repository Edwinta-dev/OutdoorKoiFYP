# WP3b MANIFEST
- Research HEAD at run: 56f43d13316fc687a786722dbea33b2f1d0b85b5. Seed 20261022, 5000 run-level bootstrap resamples. Data: PAN v1 rows.json (hashes in wp3/MANIFEST.md), same 35 scored days / 10 runs / exclusions as wp3.
- Engine imported read-only (Backend untouched). run_wp3b.py SHA-256 d065a1784cf0cf881fa6c9f50ea95104a91e06a320f3d3d57a3b4df4bd14d2c3
- Reproduce: python -X utf8 wp3b/run_wp3b.py src/pan/rows.json wp3b
- Outputs: metrics_wp3b.csv fold_params.csv daily_wp3b.csv REPORT.md
- Web search for site latitude (one query) found nothing; candidate e Blocked.
