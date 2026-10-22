# hx_robust MANIFEST
Branch research/hypoxia-external-validation (local only, no push). Freeze commit 84eb151 (frozen.json) precedes the re-runs.
Seeds: fold seeds 20261022 (ref), 20261023, 20261024, 20261025. Model random_state 20261022 (frozen). Bootstrap seed 20261022 (in summarise).
Data (read only; hashes in stage0/MANIFEST.md): FWI Data-Campaign-Data 4cfa138e725db541e7ae14b89170909bef7dafb1; Oman d95207b6f129c3de1f426ec90eed5a38f46832b8 (raw/offline/raw_readings.csv, raw/live/raw_readings_new.csv).
Env: src/.venv, Python 3.14, scikit-learn 1.9.1, pandas 3.0.6. Env vars: FWI_SRC=src/fwi/csv, HX_OUT=hx_robust/out, OMAN_SRC=src/oman/data.
Scripts: copies of stage0/code (nightgate.py edited only to read HX_OUT) plus robust.py (shared), step2_freeze.py, step2_3_run.py, step4_find.py, step4_plots.py, step5_oman.py. Order: nightgate.py, step2_freeze.py, (commit frozen.json), step2_3_run.py, step4_find.py, step4_plots.py, step5_oman.py. Each takes about 2-4 min.
Deviation: robust.py gate keeps NaN scores as NaN (nightgate.py zeroes them; REPORT section 0). stage0/ not modified.

## Output SHA256 (out/)
2b3207319eed0b22a52ec156b97815eeb86347dae10093c5027be0ecc1ba8b79  events_seed22.csv
d2b418899163c24de3054bd51a432d693aa1019682b54b496780c0aeab201d81  failure_A_0677080b.png
b65a83846e9326bf396873916ef030ac40f727f30c865a86afdca14bcaad96e1  failure_B_0f143c64.png
cf0cee2e783ec159d67364a14007609a7a5d3e154e4fe4d3c71da1cfe3df747b  failure_C_3eab831e.png
91f33ee3b6c08c7c6072cdf7d15df5f7633fc9ded996557df680e5f6a5f7315d  failure_D_858b914c.png
da4f86931370993cd85513162b1788fa9bd14a49f32aadb13f6022140fbabdb5  night_gate_summary.csv
90d8bf10d6be2fb0990608eef7fdf0cc4d60faae9968ad62b6ae1ef6c98753c4  perpond_s20261022_22-06_fixed.csv
740c1d50ed9a42b0432d7eb352e30fd56577e1ade6eca96065104853d90b5119  perpond_s20261022_22-06_reselect.csv
9907cda92a705f49a5f947a7eaf15270112a30f6a1d18eb6a15c7105db080394  perpond_s20261022_no_ga_reselect.csv
52ebe09fa0293c987c9a495e670e0d33865df15c0304630841e480b38c7d2bd5  perpond_s20261023_22-06_fixed.csv
68c9c3fdcee52fcdd90563527d4a13de4b2ffdad3a89fafe926177a6c4fa22eb  perpond_s20261023_22-06_reselect.csv
12c9465837a78ef4ae0272e7ef1f8649070a8d8bc01afa89d6551953a623baa2  perpond_s20261023_no_ga_reselect.csv
2350487f5e4fc239512d15af6a99a16fadc72c3e39435865d1509fd041acf4a8  perpond_s20261024_22-06_fixed.csv
ad93951a9f6101db6c1c0042b93cdf3ba5fdfec55753eb8e26ae69fb0d9082d0  perpond_s20261024_22-06_reselect.csv
1c7bb23666256b8a1047fb3aef1163949449d017198050ba5ba47723f383b732  perpond_s20261024_no_ga_reselect.csv
7bb8f9a4d9395e7332014306ad22e2f81dd9070bc9e763e64521349a58a54441  perpond_s20261025_22-06_fixed.csv
fb4a5978c44e41800d39cd79f37018d3dde467541b6991d48b8fd88a7c3d997e  perpond_s20261025_22-06_reselect.csv
14e1953130b5b9d0ada5385f45d715c069f8d4c71aabc92444d6b71bde8a741d  perpond_s20261025_no_ga_reselect.csv
5f6c42ab24e23da1fdf4c6ea8dfe9dfb04e743d53c338bb9c7c60cff11e480bc  seed22_gated_scores.pkl
a991d3126356bb4c41351182cd87eb84939eb7e41eb649591c01ecf948eb01c5  seed22_test_scores.pkl
e73f779ede2c1cc1b171bfdd6757fe1b2f9305b5a8bbc8b33d059426eecec76d  step2_3_stdout.txt
595cc8c1c95b577951162c27526480071cee5f4cc4de4993f6d18d1c64c813e9  step2_seeds.csv
52a60e3d37b1dca675a1816a3e59c301381459961a505223010c5e8b56479673  step3_severity.csv
a104aebf6f15adbe51d4914c85400ecb096f086070c86fce189f80e78b38ef79  step5_oman.csv
