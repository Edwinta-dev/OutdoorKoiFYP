# Stage 0 MANIFEST
Seed 20261022. Python 3.14 venv at src/.venv (gitignored); versions in requirements_used.txt (pandas 3.0.6, scikit-learn 1.9.1; the round-2 versions are not recorded).

## Sources
| Dataset | URL | Pinned revision | Local path |
|---|---|---|---|
| FWI India | https://github.com/fish-welfare-initiative/Data-Campaign-Data | 4cfa138e725db541e7ae14b89170909bef7dafb1 (verified via rev-parse) | src/fwi |
| Oman tilapia | https://github.com/AhmedTheNetCoder/DO-Forecasting-Tilapia-Dataset | d95207b6f129c3de1f426ec90eed5a38f46832b8 (verified) | src/oman |
| Indonesia IDN-T | Mendeley Data yd36bx6f8f, version 2, public API https://data.mendeley.com/public-api/datasets/yd36bx6f8f/files?folder_id=root&version=2 (official, no login) | file SHA256 below; hashes match the sha256_hash the Mendeley API reports | src/idn_t |
No unofficial mirrors used. (Kaggle mirror not used.)

## File SHA256
IDN-T (both match the Mendeley API values):
- pond_iot_2023.csv `ea2a2569107912f151141bcad67a39c41c25b9b4ddca775d40346d66e39f2c07` (4,431,680 B)
- pond_iot_2023_raw.csv `ac69aff715a31f3f35439247b02d9e7bf95e409652bf88e5f15329c6effb9dba` (19,026,532 B)

Oman:
- data/raw/offline/raw_readings.csv `cb265da2d65254d661c5c82e6ad44ad362e81105e41eea8d50e7f6931216d863`
- data/raw/live/raw_readings_new.csv `051d87cc7e0ea072a08ccbe8a2fae182011586b527eae1df253bb5490bddd35e`
- data/processed/aggregated_data.csv `f40e4baacfef64c6150a85b22f245d1c4ce10f8685b2dd58caa9082aaad76b35`
- data/events/event_logs_new.csv `b01c106ca69b184c6cecdd03ed0ba77f7cd9a4ccc45e2cc53e6d5b193e7d8a3f`
- data/validation/validation_log_new.csv `bc409703aaa20d8e13755d724bfd227e12dd198b03e6601bcf34560ada3f0cde`

FWI: all 17 ara2_*.csv hashes in MANIFEST_fwi_sha256.txt; other csv below.
- csv/Key_Events.csv `719f284470997c8a0dc0f779ed7b167d21e4331a4694d4a7b56945173f9ee955`
- csv/QC_Flags.csv `0385494f85c04e9c0df866ed92e5fc8591d63d00d23f01d767b4491960af3b31`
- csv/ProDSS_and_Photometer.csv `852005248309a19b4fa193759a7dec61bce7f36a2e476a57c1203db164fe89b9`
- csv/ProDSS_vs_Continuous_Monitors_Comparison.csv `7653b3558558739d14df4ded6b92a31a927b2d56383f5285282ba8c77a17fbf9`
- csv/Continuous_Monitor_vs_ProDSS_Comparison.csv `40e508e960104fd314f7a932e182b262df17734b7fc730af023d7baad0194213`

## Row counts (data rows, excl. header)
FWI (15-min, 2025-11-28 .. 2026-01-30, IST): 0677080b 3042; 0f143c64 4470; 148f1633 2298; 176528d3 4149; 29660d32 5592; 3b2f3973 4930; 3eab831e 5584; 3ed4df8e 4665; 45e1cde5 4414; 4f79fc8d 4436; 573a2826 4480; 6ca69422 3776; 858b914c 2338; b3d128ac 4435; c9cdacda 5477; ca187575 4206; d52ddb31 4458 (total 73,150).
Oman: offline raw 41,663; live raw 102,670; aggregated 897 (5-min); events 42; validation log 9,472.
IDN-T: cleaned 118,286 rows; raw 505,730 rows.

## Timezones
- FWI: IST (UTC+5:30), stated in README (column header "Date/Time (IST)").
- Oman: local Oman time, GMT+4 (data_dictionary.md).
- IDN-T: NOT documented (created_date, M/D/YYYY H:MM, presumably local Indonesia; unverified). Treat as unknown.

## Code provenance
stage0/code/ = copy of results/code/*.py. Changes: load.py SRC now env var FWI_SRC (default old path); final.py OUT now env var HX_OUT (needed so it does not write to C:\home\user\hx; I/O path only, no logic change). integrity.py and nothing else is new. Run: `FWI_SRC=<src/fwi/csv> HX_OUT=<stage0/out> python final.py` (run without -I, since the scripts import sibling modules; no downloaded code is executed, only read as CSV).
Outputs: stage0/out/ (final_stdout.txt, onset_summary_strict.csv, onset_per_pond_strict.csv, onset_scores_strict.pkl).
