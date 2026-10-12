# 0092: Algae engine on GCC: daily windows, growth slope, offset thresholds, reset on re-baseline
Status: partial · Issue: #92 · Date: 2026-10-12

## In short
The algae engine now judges the pond on GCC (the `water_gap` region's when configured, else the frame's), summarised as a daily 90th percentile and a 3-day window. Status is an offset above the pond's own quiet level, growth is a slope tested against the measured day-to-day spread, and a 3x3 cell rule reports local green patches. Any `imageTable.baseline_reset` restarts history and thresholds. The camera state-machine decision (green_ratio vs GCC) was not made: no stored frames were available to replay (see Not verified).

## Traps: why the obvious fix is wrong
- `rate_source` keeps the app's three values (`fitted_from_camera`, `measured_declining`, `literature_fallback`). `tests/api/test_contract.py::test_field_types_match_dart_casts` pins them, and `algae_status_card.dart` labels any other value "default growth rate". The issue's "legacy" marker therefore lives in `rate_source_detail` (`legacy_green_ratio` / `gcc_windowed_trend`).
- The reset search in `AlgaeGrowthEngine::_newest_unseen_reset` looks at the whole batch, not just frames past the watermark. A v5 snapshot assimilated the 2026-10-04 reset frame before it knew about resets, so a fresh-only search would never restart it. `_restart_view` puts the watermark just before the reset frame so the batch is assimilated again.
- The growth slope uses daily values, not windowed ones. The noise comes from windowed changes before the short window (`gcc_growth`). A windowed slope detected a 1.2x-threshold rise after a median of 4 days, against 3 for the daily slope, and missed 14 of 100 rises against 1 (`build/proto92b.py`). Taking noise from the whole series let the rise widen its own threshold. A robust (MAD) noise, measured in `build/proto92b.py`, gave 3x more false flags than the plain standard deviation.
- A zone cell's baseline leaves out its last `cell_run` values (`gcc_zones`), so a tint being judged does not lift its own baseline.
- Unusable `water_gap` frames give no GCC. They do not fall back to the frame GCC, which would put two measurement areas in one series.

## Components
- `Backend/koi/models/algae_engine.py::GccSignalConfig` - tuning, persisted in the algae snapshot.
- `...::_frame_gcc`, `_frame_grid`, `parse_image_rows` - GCC, source, grid and reset per row.
- `...::gcc_daily_series` - daytime local-day quantile and trailing window.
- `...::gcc_growth` - short and slow slopes, noise, threshold, flag.
- `...::resolve_gcc_thresholds` - quiet level + offsets.
- `...::gcc_zones` - reference shift, per-cell baseline, pooled MAD noise, flag/spike/pending/unreliable.
- `...::AlgaeGrowthEngine` - `_restart_view`, `_add_gcc_frames`, `_refresh_gcc`, `_assess_gcc`, `_project_gcc`, `_gcc_scrub_benefit`, snapshot keys.
- `Backend/koi/settings.py::Settings.algae_signal_config` - `KOI_ALGAE_*` settings; `TEST_MODE` switches the zone run to frames.
- `Backend/koi/worker/poller.py::_poll_user` - hands the config to the engine each cycle (`Worker.__init__` sets `EngineRegistry.algae_signal_config`).
- `Backend/koi/api/responses.py::GccGrowth`, `GccZones`, `GccCell` - response models.

## Problem and constraints
On pond 455, green_ratio measures plants and framing. The 3x/6x multipliers alarm on a near-zero baseline. A re-baseline left two views mixed in one history. Constraints: the camera upload contract is unchanged, rows without `gcc` must load, and `pond_algae_evaluations` gets no new columns, so the new blocks appear only on fresh assessments and forecasts.

## Alternatives considered
- `legacy_*` values in `rate_source` - breaks the contract test and the app's label.
- Windowed-value slope; robust (MAD) growth noise; 4 quiet differences - measured worse, see Key parameters.
- Daily aggregates instead of frames in the snapshot - less JSON, but a second code path for frame-mode zones. Frames are capped instead.

## Trade-offs
- After a rise has lasted longer than the short window, its own changes enter the quiet period, so the growth flag can clear while the pond is still greening. Status offsets still catch the level.
- Label-calibrated thresholds stay in green_ratio units. Once calibrated, green_ratio drives status again (`_gcc_status_applies`).
- The GCC projection ignores weather and nitrate.
- The app still reads `current_green_ratio` against GCC thresholds and a null trajectory `green_ratio` as 0. Its algae chart is wrong in GCC mode until the app reads `current_gcc` and `gcc`.

## Key parameters
Defaults are in `GccSignalConfig`, which equals the `Settings` defaults (`test_settings_build_the_signal_config`).
- `growth_k` 3, `min_quiet_diffs` 7 (`KOI_ALGAE_GROWTH_MIN_QUIET_DIFFS`). `build/proto92d.py`: 200 synthetic flat ponds (noise 0.004/frame, 6 frames a day), judged daily. False flags: 0.48% with 4 differences, 0.32% with 7, 0.29% with 10. Detection of a 1.2x-threshold rise took a median of 3 days in each case. Raise k to 3.5 for about 0.13% false flags at the same lag.
- `growth_min_rate` 0.001 GCC/day: a floor for noiseless series. Not fitted.
- `watch_offset` 0.03 and `action_offset` 0.06 GCC: starting guesses, not fitted. A 16% green tint on water-like RGB (0.25/0.35/0.30) moves GCC by about 0.036 (`test_algae_gcc.py::cell_gcc`).
- `cell_k` 3, `cell_run` 2, `cell_unreliable_ratio` 4, `ZONE_MIN_BASELINE` 3, `ZONE_NOISE_FLOOR` 0.001: issue defaults or chosen floors. On the synthetic scene, 2-day tints flagged 7/50 ponds at 2%, 41/50 at 4% and 50/50 from 8% (`build/proto92c.py`).
- `reference_cells` (6, 7, 8): the bottom row is assumed to be floor; not checked on pond 455 frames.
- `history_days` 30, `MAX_GCC_FRAMES` 1000: snapshot size bound.

## Assumptions
- Frames in a batch are in time order (`parse_image_rows` sorts them). Zone keys rely on it in frame mode.
- `camera.py::upload_image` writes `baseline_reset` on mask/region version changes and on persisted obstruction. If a view change happens without one, the views mix again.
- Synthetic noise levels resemble real frames. If they do not, the measured rates do not carry over.

## Edge cases
- Handled: unusable gap, failed quality, obstruction, night frames, missing days, an object cell, an unreliable cell, no reference cell (`note`), too little baseline (`insufficient`), a reset already passed (v5 snapshot), a repeated reset batch.
- Not handled: frames whose GCC is above `CAMERA_GCC_MAX` (0.45, #90) fail quality and never reach the engine, so a very green pond's level stops updating. Ratings made before a reset still calibrate.

## Not verified
- No replay over stored pond 455 frames: the camera state machine stays on green_ratio, and the issue's stored-frame demo (an issue comment) was not available. The zone tests use a synthetic stand-in.
- The offsets, the reference row and the false-flag rate on real frames are not checked.
- The `build/proto92*.py` measurements were one-off scripts in the ignored `build/` folder, not committed. The committed tests check the same rules with fewer ponds.
- `pond_algae_evaluations` still has no rows after 2026-08-27 (owner check, #83).

## Changes outside the scope
- `pond_twin.py` (`SNAPSHOT_VERSION` 6), as the issue asks.
- `responses.py`, `docs/api/openapi.yaml` (regenerated) and `v1_dashboard.json` (regenerated) for the new fields.
- `.env.example` and `docs/models.md` for documentation.
- `poller.py::Worker.__init__` puts the config on the registry, and `koi/registry.py::EngineRegistry` gains a typed `algae_signal_config` attribute (None by default) beside `tds_prompt_config`. A dynamic attribute failed mypy.
- Three tests updated for snapshot version 6.

## How it was verified
- `Backend/tests/models/test_algae_gcc.py` (34 tests): parsing, daily p90/window, the flat false-flag rate, a rise at 1.2x the threshold, offsets, the near-zero case, live-shaped resets (GCC and legacy), the v5 snapshot restart, legacy rows and snapshots, the zone rule (clean, spike, sustained 16%/25%, gap day, frame mode, uniform shift, uniform rise, unreliable), projection, label takeover, settings and `_poll_user`.
- The full non-SQL backend suite, then `python tools/check.py all`.

## To change this
- To favour fewer false growth flags, raise `KOI_ALGAE_GROWTH_K` from 3 to 3.5. Cost: smaller rises flag later.
- To catch fainter patches, lower `KOI_ALGAE_CELL_K`. Cost: nine cells give nine chances of a false alarm (the issue says keep k).
- Fit the offsets from replay. Likely bug sources: local-day keys, the watermark after a restart, units (GCC vs green_ratio) in shared columns.

## Rollback
Revert the commit. The pre-#92 `AlgaeGrowthEngine.from_snapshot` reads keys with `.get`, so it should ignore the new algae keys (read in the code, not tested). After reverting, check that `rate_source`, the OpenAPI document and the dashboard fixture match again.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-12 08:48 UTC for issue #92 attempt 6; compared HEAD..working tree (base 9b0c24a). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 6 file(s), 66 component(s), +774/-6 lines
- plus 5 test file(s), 3 doc file(s)

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/models/algae_engine.py::AlgaeGrowthEngine`, `Backend/koi/models/algae_engine.py::resolve_thresholds`, `Backend/koi/worker/poller.py::_poll_user`, `Backend/koi/settings.py`. Verdict: violation.
- out of scope: `Backend/koi/api/responses.py::GccGrowth` (added); explained in the record: yes
- out of scope: `Backend/koi/api/responses.py::GccCell` (added); explained in the record: yes
- out of scope: `Backend/koi/api/responses.py::GccZones` (added); explained in the record: yes
- out of scope: `Backend/koi/api/responses.py::AlgaeAssessment` (modified); explained in the record: yes
- out of scope: `Backend/koi/api/responses.py::AlgaeDay` (modified); explained in the record: yes
- out of scope: `Backend/koi/api/responses.py::ScrubBenefit` (modified); explained in the record: yes
- out of scope: `Backend/koi/api/responses.py::AlgaeForecast` (modified); explained in the record: yes
- out of scope: `Backend/koi/models/algae_engine.py::GreenSample` (modified); explained in the record: no
- out of scope: `Backend/koi/models/algae_engine.py::parse_image_rows` (modified); explained in the record: no
- out of scope: `Backend/koi/models/algae_engine.py::AlgaeAssessment` (modified); explained in the record: no
- out of scope: `Backend/koi/models/algae_engine.py::<module>` (added); explained in the record: no
- out of scope: `Backend/koi/models/pond_twin.py::PondTwin.from_snapshot` (modified); explained in the record: no
- out of scope: `Backend/koi/models/pond_twin.py::SNAPSHOT_VERSION` (modified); explained in the record: no
- out of scope: `Backend/koi/registry.py::EngineRegistry.__init__` (modified); explained in the record: yes
- out of scope: `Backend/koi/worker/poller.py::Worker.__init__` (modified); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Consistency**

0 error(s), 18 warning(s) between the rationale above and the change.
- WARN: record says Status: partial but the agent result says STATUS: COMPLETE
- WARN: ambiguous reference: algae_status_card.dart matches 2 files (MobileUI/mobile_app/lib/widgets/detail_graph/algae_status_card.dart, archive/pre-refactor/MobileUI/mobile_app/lib/widgets/detail_graph/algae_status_card.dart); use the repo-relative path
- WARN: Backend/koi/models/algae_engine.py::GreenSample (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/algae_engine.py::gcc_daily_series (added) changed but not mentioned in the record
- WARN: Backend/koi/models/algae_engine.py::resolve_gcc_thresholds (added) changed but not mentioned in the record
- WARN: Backend/koi/models/algae_engine.py::AlgaeAssessment (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/algae_engine.py::AlgaeGrowthEngine.__init__ (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/algae_engine.py::AlgaeGrowthEngine.ingest_camera_samples (modified) changed but not mentioned in the record
- WARN: Backend/koi/models/algae_engine.py::AlgaeGrowthEngine.signal_config (added) changed but not mentioned in the record
- WARN: Backend/koi/models/algae_engine.py::AlgaeGrowthEngine.signal_config#2 (added) changed but not mentioned in the record
- ... 8 more in the JSON record

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0092-algae-engine-on-gcc.md` gives the commit.
<!-- verified-facts:end -->
