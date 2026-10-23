# Production weather ladder vs FYP Chapter 4.7

Written by the main session from the subagent's results (commit 785987d). The headline was checked at `Backend/koi/api/routes.py:1114-1116`. Test window: 2025-01-01..2026-08-19. The production code was imported unchanged. The notebook was read for its definitions only and was not executed.

## Headline
- **WINDOW and PREEMPT are dark in production.** `routes._lead_time_actions` and `notifications.lead_time_actions` pass `None` for `pooled_rank`, `dry_band_max`, `observed_tmax_c`, `temperature_regime` and `outlook_high_c`. They never pass `outlook_hot_threshold_c` at all. No code builds these inputs. On all 596 test mornings both rules return `insufficient_data` and fire 0 times.
- **The comparators and parameters match the chapter when correct inputs are supplied:**
  - WINDOW: 121 firings, 86.0% [78.6, 91.0] against a 52.4% base.
  - Hot outlook alone: 204 firings, 76.5%.
  - PREEMPT: 88.6% precision, but 79 firings instead of 87. The new-regime cut in the JSON is 31.86, while the notebook used 31.80, because the JSON was regenerated on climate rows through 2026-09-28.
- **The nowcast reproduces:** 23.95% of issuances fire, 62.1% precision against a 20.8% base, 1,722 onsets, 79.2% flagged, median warning 49 min (IQR 14-90).
- **One chapter claim needs correcting.** The 79% includes flags issued up to 29 minutes after the onset hour began. Counting only flags strictly before the onset hour, it is 66.8% (68.5% if a flag issued at the hour start counts), with a median warning of 64 min.

## Mismatches
| # | Mismatch | Severity |
|---|---|---|
| 1 | Production never computes the WINDOW/PREEMPT inputs (routes.py:1114-1116, notifications.py:312) | High: the two best-validated rules never reach the user |
| 2 | New-regime hot cut: JSON 31.86 vs notebook 31.80 | Medium, latent: PREEMPT would fire 9% fewer times |
| 3 | The parameter is named `outlook_high_c`, but its threshold 29.125 was calibrated on the mean of the lead 1-4 midpoints | Medium, latent: wiring in the literal daily high gives 79.7%, no better than "hot today" alone |
| 4 | Issuance choice: the notebook kept the earliest issuance of the day, production keeps the latest as-of. PREEMPT needs a complete-day Tmax, so it is an end-of-day decision | Medium, latent |
| 5 | `pooled_dry_rank_max` is in the JSON but never read; `dry_band_max` has no default | Low, latent |
| 6 | Chapter: "79% flagged before onset" | Medium (claim): use 66.8-68.5% |
| 7 | Chapter: "about 60 and 44 firings a year" divides by 2 calendar years, but the window is 1.64 years | Low (claim): about 74 and 53 a year |
| 8 | `forecast_utils.rain_context_from_text` uses 3 levels and maps Thundery Showers to heavy (12 mm/day credit), but the chapter finds Thundery nearly uninformative. It feeds the evaporation and chemistry hints, not the ladder | Medium |
| 9 | The notebook tiers "Slightly Hazy" as Light (a substring bug). Production is right | Low |
| 10 | Production decides at poll time; the notebook scores at issue time. 359 issuances (0.25%) have stale windows | Low |

Files: common.py, inputs.py, run_nowcast.py, run_ladder.py, run_wordings.py, diag_threshold.py, plus the CSVs in this folder.
