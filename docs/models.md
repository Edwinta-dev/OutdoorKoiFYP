# Model constant changes

Every change to a model's numeric constants (rates, thresholds, weights,
intervals): the old value, the new value and why. Newest first.

## Backtest baseline (issue #33)

`Backend/backtest_baseline.json` holds the backtest metrics that
`python tools/check.py backend` guards (`python -m koi.tools.backtest
--check`). A change that makes one worse than its tolerance allows must
update the baseline in the same change (`--update-baseline`, which raises
the version) and add a line here naming the version and the reason.

- Backtest baseline version 1 (issue #33): first baseline, no model
  constant changed. Demo pond (synthetic seed): water temperature RMSE
  1.00, 1.00 and 0.97 C at +1, +3 and +6 hours against persistence 0.17,
  0.48 and 0.88 C (the air-plus-offset model loses to persistence at every
  horizon on this fixture, because the seeded air swings two to three times
  as far as the water); the one logged top-up (100 L) against a modelled
  54.5 L loss. NEA table: clear-day precision 0.861, heat-holds 0.842.

## Pond-specific calibration (issue #32)

No default changes: the wind shelter factor stays 0.6, the water/air
offset -1.0 C with no lag, and both nitrification rates (0.05 and 0.035
per hour) stay as they are. A pond with no fit uses exactly these.

The worker's daily calibration job (04:00 Singapore time,
`Backend/koi/worker/poller.py`) fits per pond, from the 60 days before the
run, and appends each result to `pond_calibration` (migration 0022):

- Shelter factor (`evaporation_engine.fit_shelter_factor`): litres added
  at each logged top-up against the Penman loss since the previous top-up
  or water change, hour by hour from station air temperature, wind,
  measured rainfall and the 24-hour forecast's humidity. Least squares
  over 0.05 to 1.5 in steps of 0.005. Needs 3 usable top-ups: a logged
  volume, an interval of 1 to 45 days, 75% of its hours complete
  (missing hours are covered by scaling the complete ones). Assumes each
  top-up refilled to the same level. Error: in-sample RMSE in litres
  beside the RMSE at 0.6.
- Water/air temperature (`fit_water_air_temperature`): the pond's hourly
  water temperature against the station's hourly air temperature, lags 0
  to 12 hours, offset the mean difference at each lag. The first 70% of
  the usable hours train, the rest evaluate; the evaluation RMSE is
  reported beside persistence (water 24 hours earlier) and beside the
  default. Needs 72 usable hours and 24 evaluation hours.
- Nitrification scale (`kit_readings.fit_nitrification_scale`): one
  multiplier on both rates, from kit ammonia (TAN) and nitrite against a
  simulation of the pools from logged feedings and water changes using
  the engine's own step. Log-spaced grid 0.1 to 5 (161 points). Needs 4
  readings with ammonia or nitrite, a week after the simulation start.

Too little data stores a pending row and keeps the default; a pending
result never replaces a fit. Each poll applies the newest fit in force at
its time, so a rebuild of a past time uses the version that existed then.
The uncertainty scenarios (issue #31) are scaled onto a fitted value:
shelter 0.4/0.6/0.8 becomes 2/3, 1 and 4/3 of the fitted factor, and the
nitrification ranges are multiplied by the scale.

## Lead-time weather action ladder (issue #26)

`Backend/koi/models/ladder.py::react` checks the previous Singapore local
calendar day at its assigned rainfall station. It fires REACT at 50 mm or
more, only when retained station intervals cover the full day. A partial or
absent window is `insufficient_data`; a covered day below 50 mm is an
assessed no-action result.

`Backend/koi/models/ladder.py::preempt` requires both a measured daily Tmax
and an issued hot outlook. The observed hot thresholds are regime-aware:
32.60 °C for the validation's older two-station cluster and 31.86 °C for its
later full-network regime, regenerated from the committed CSV. The notebook's
saved run reports 31.80 °C for the latter regime, a 0.06 °C difference from
the committed snapshot. The validation reports held-out precision 0.885.
This evidence is a network-mean series; it has not been shown to transfer to
one pond's assigned weather station. The live API leaves PREEMPT unassessed
until a complete station-day maximum and matching as-of outlook are
available.

`Backend/koi/models/ladder.py::window` accepts the notebook's pooled 24-hour
forecast rank and compares it with the training-period 16th percentile
(rank 0.10 in the committed daily export; ties mean this is not exactly 16%
of days). It recommends a water change or scrub. The CSV's TEST rows
reproduce held-out precision 0.8595 for a dry day (<1 mm). The runtime
assigned regional forecast is not the same pooled network series, so the API
reports `insufficient_data` for WINDOW until a comparable series is retained.

`Backend/koi/models/ladder.py::nowcast` checks only the 2-hour issuance
available and valid at decision time. A rain call prompts securing exposed
feed and pausing outdoor pond work; a valid non-rain call is a no-action
result. The validation reports a median warning of about 50 minutes. This
is a short-fuse rain alert, not a pond-safety prediction.

`Backend/tools/regenerate_ladder_params.py` derives observed heat thresholds
and forecast cutoffs from committed climate and daily-label CSVs through
2024-12-31 and writes `Backend/koi/models/ladder_params.json`. The compact
daily-label CSV omits PREEMPT's future three-day heat target, so its 0.8851
held-out precision is read from the committed notebook validation export;
the test checks its recorded sample count and precision. Precision figures
are validation evidence, not runtime confidence. The runtime network/station
definition gap limits which rules can currently fire in the API.

## Salt addition and filter cleaning (issue #29)

`Backend/koi/models/engine.py`: SALT increases expected TDS by nominal
added dissolved mass `1000 * salt_grams / volume_litres` mg/L. Old engine
behaviour: no salt event, hence zero expected step. The issue amendment
corrects the old example of 0.01 ppm for 1 g in 100 L to **10 mg/L**,
a factor of 1000 larger. Grams become
milligrams by multiplying by 1000. Pond volume and salt mass must be
finite and positive. This is a model assumption: an EC-derived meter's
TDS response varies with its calibration and the dissolved ions, so the
nominal mass is not a guarantee of the measured step.

SALT uses the existing six-hour volume-event rate-check allowance;
plausible-range and stale-run checks remain in force. Its day is excluded
from chemistry trends. The evaporation cross-check uses complete daily
averages strictly after the latest salt event's local day, so a slope
never spans added mass. It reports insufficient data until at least
three post-salt days exist; subsequent concentration trends remain usable.
Both logged rows and events already posted to the engine are considered.

FILTER_CLEAN leaves model pools unchanged. For samples in the half-open
interval `[event_time, event_time + 24 hours)`, chemistry daily snapshots
persist `ph_after_maintenance` and `tds_after_maintenance` for the trusted
channels actually read; those buckets are excluded from chemistry trends.
Daily snapshot version 3 adds these flags; version 2 keeps its local-day
loader and missing flags default to false, as do legacy snapshots.
The SQL graph history derives `after_maintenance` for pH/TDS days whose
Singapore midnight boundaries overlap that interval. This deliberately
marks the whole daily average, survives sensor-row retention, and follows
intervention edits/deletions. The evaporation cross-check omits the same
maintenance days. No existing numeric rate or threshold changed.

## Device health and data confidence (issue #23)

File: `Backend/koi/models/device_health.py`. New constants; no existing
constant changed. Chosen by judgement, not fitted: no recorded node
history with known outages exists yet.

| Constant | Value | Rule |
|---|---|---|
| `WINDOW` | 24 h | Window for contacts received, missed cycles and the battery trend. |
| `SILENT_AFTER_INTERVALS` | 3 expected intervals | Newest reading older than this: assessment confidence `low`; one channel the assessment uses this old: `reduced`. A device with nothing for this long after its last contact is `silent`. |
| `GRACE_FRACTION` | 0.5 of the interval | A report counts as on time up to half an interval after it was due (wake-up, Wi-Fi and upload take time). |
| `BATTERY_TREND_MV_PER_DAY` | 50 mV/day | Least-squares slope beyond this either way: `falling` or `rising`, else `steady`. |
| `BATTERY_MIN_SAMPLES`, `BATTERY_MIN_SPAN` | 3 readings, 2 h | Fewer, or a shorter span: trend `unknown`. |

The expected interval is the one configured on the `devices` row
(`expected_interval_seconds`), else `KOI_SENSOR_CADENCE_MINUTES` for the
sensor node and the last wake the camera was given. A channel flagged by
the sensor gate's existing stale-run rule (`_STALE_RUN_LENGTH`, 4 equal
values) also reduces confidence.

## Camera obstruction off-ramp (issue #80)

Files: `Backend/koi/camera/hsvEngine.py`, `Backend/koi/camera/camera.py`.
New constants; no existing constant changed.

| Constant | Value | Why |
|---|---|---|
| Frames to confirm a new level (`OBSTRUCTION_CONFIRM_FRAMES`, `CAMERA_OBSTRUCTION_CONFIRM_FRAMES`) | 3 (0 = off) | An obstruction (a jump of more than 0.40 over the smoothed baseline) froze the baseline and cleared only when a frame came back within 0.15 of it. The app reset meant for a genuine change of view was never connected, so pond 455 stayed latched at a 0.0125 baseline while reading about 0.19 (2026-10-03). Three obstructed frames in a row that agree now restart the baseline at their mean and record `baseline_reset = 'obstruction_persisted'`. |
| Agreement band (`OBSTRUCTION_TOLERANCE`, `CAMERA_OBSTRUCTION_TOLERANCE`) | 0.05 green ratio | A frame within 0.05 of the running mean of the current run extends it; any other frame starts a new run at itself, so a lone leaf, bird or ripple never confirms. |

While latched, dG/dt stays frozen at the last trusted baseline as before;
frames that fail the quality gate carry the run unchanged. Obstruction rows
grow to `[label, smoothed, 0, 0, candidate_mean, count]`; rows without the
candidate load with none, and the app only reads element 0.

## Camera frame quality gate (issue #40)

Files: `Backend/koi/camera/quality.py`, `Backend/koi/camera/imageSchedule.py`.
New constants; no existing constant changed. The thresholds were chosen
against synthetic frames (noise-textured water and algae, darkened,
brightened, glared and Gaussian-blurred copies), not yet against real
ESP32-CAM frames from the pond. Every stored result carries its metrics
and the thresholds in force, so frames can be re-judged once real
frames have been reviewed.

| Constant | Value | Rule |
|---|---|---|
| `V_MEAN_MIN` | 40 (V, 0..255) | Mean V below this: `too_dark`. |
| `V_MEAN_MAX` | 220 | Mean V above this: `too_bright`. |
| `CLIP_LOW_V`, `CLIP_HIGH_V` | 5, 250 | A pixel at or below / at or above these counts as clipped. |
| `CLIPPED_FRACTION_MAX` | 0.25 | More clipped pixels than this: `clipped`. A sun-glare patch over about 15% of the frame still passes. |
| `DETAIL_MIN_V_STD` | 8 | Below this V spread the frame is featureless and is not judged for blur (a calm, evenly lit pond). |
| `BLUR_LAPLACIAN_MIN` | 5.0 | Variance of the Laplacian below this, on a frame with detail: `blurred`. Low on purpose: a single sharp edge between water and algae scores about 8 to 20. |
| `QUALITY_RETRY_SEC` | 30 min | Wake after a failed frame in daylight, when the state's own wake is later. At night the night schedule stands. |
| `THUMBNAIL_WIDTH`, `THUMBNAIL_JPEG_QUALITY` | 320 px, 80 | Thumbnail stored beside each frame. |

A failed frame does not change the state machine: its `current_state`
repeats the previous accepted frame's, and the next frame is evaluated
against the newest frame that did not fail (searching the last 50 rows,
`PREV_SCAN_ROWS`; after 50 failed frames in a row the baseline restarts at
0.15 as for a first frame). Failed frames are also left out of the algae
fit (`algae_engine.parse_image_rows`).

## Camera colour observations and cast gate (issue #90)

Files: `Backend/koi/camera/quality.py`, `Backend/koi/settings.py`.
Migration `0021_camera_colour.sql` adds nullable `imageTable.gcc real` and
`colour jsonb`. Legacy rows load with null observations; no backfill is
invented. The HSV band, `green_ratio` computation and camera state machine
input remain unchanged, pending replay evidence in #92. The camera's
upload request, authentication and reply schema remain unchanged.

GCC (green chromatic coordinate) is the **mean of each pixel's
G / (R + G + B)**, rather than the ratio of channel means. Neutral grey
has GCC 1/3; uniformly scaling brightness preserves GCC. This reduces
illumination sensitivity without removing sensitivity to white balance.
The frame means use the same water-mask pixels as green_ratio, or the
whole frame when there is no mask, excluding pixels with HSV V <= 5 or
V >= 250 (`CLIP_LOW_V` / `CLIP_HIGH_V`, unchanged).

The version 1 `colour` object holds mean `r`, `g`, `b` (channel intensities
divided by 255) and mean normalised ExG. For those scaled channels,
ExG = 2g - r - b spans -2..2, so the stored 0..1 value is
**(2g - r - b + 2) / 4**. Neutral grey is 0.5. Means with no usable pixels
are null, including GCC; no zero or NaN is substituted.

`colour.grid = {"rows": 3, "cols": 3, "gcc": [nine values]}` records
per-cell GCC over the **whole frame**, ignoring the water mask but excluding
clipped pixels. Cells are row-major (top-left index 0, bottom-right index
8), with integer boundaries `i * dimension // count` so odd frame sizes
retain every pixel. Empty or entirely clipped cells store null. The grid
includes floor and rim as potential neutral references for #92. Its size
comes from `CAMERA_COLOUR_GRID_ROWS` / `CAMERA_COLOUR_GRID_COLS` (default 3
each), and the actual dimensions are stored with every frame.

Quality result version changes **1 -> 2** to add `colour_cast`; Python and
SQL readers continue recognising version 1. A frame fails this rule when
its unclipped mean GCC lies outside the inclusive configured band, or
its mean HSV saturation over all mask pixels (including clipped pixels,
as with the existing exposure metrics) lies outside its inclusive band.
All-clipped frames have no GCC to judge and still fail the existing
clipping rule. Metrics `gcc` and `s_mean`, plus the thresholds in force,
are stored in every quality result. Rejected frames keep the previous
trusted state and remain excluded from the algae fit.

| Setting / new default | Starting value | Stored threshold |
|---|---|---|
| `CAMERA_GCC_MIN` / `GCC_MIN` | 0.30 | `gcc_min` |
| `CAMERA_GCC_MAX` / `GCC_MAX` | 0.45 | `gcc_max` |
| `CAMERA_S_MEAN_MIN` / `S_MEAN_MIN` | 0 (0..255 scale) | `s_mean_min` |
| `CAMERA_S_MEAN_MAX` / `S_MEAN_MAX` | 200 | `s_mean_max` |

These are wide, provisional bands, not thresholds fitted to the four
normal pond frames in the issue. Their GCC was 0.360..0.366 despite large
green_ratio changes; the dim green-cast frame's GCC was 0.546. The upper
saturation bound initially catches highly saturated casts; the lower
bound allows neutral grey. Per-pond replay in #92 must establish useful
thresholds. No existing model numeric constant changed.

## Local calendar days in the chemistry engine (issue #15)

No numeric constant changed. `DAILY_RETENTION_DAYS` is still 30, and the
`has_enough_data` minimum is still 6 samples a day.

The chemistry engine's day buckets (`WaterChemistryEngine._daily`, which
feed pH reactivity, its trend and the TDS trend) used to be keyed on the
calendar fields of each timestamp's own offset. Poller samples are UTC,
so in practice each day ran from 08:00 to 08:00 SGT. A day now runs from
local midnight to local midnight in `PondConfig.time_zone` (default
`Asia/Singapore`), which is the definition `aggregate_daily_sensor_data()`
already used for `daily_sensor_averages`. Keys are zero-padded ISO dates.
Retention now keeps exactly 30 local dates, whatever time of day the
first sample of each bucket arrived. A calendar date is not a night:
20:00 on one day and 06:00 the next are on different dates. No model
needs a per-night key, and `night_ph` (per calendar day) is not read.

Evaporation, algae, the feed-rate estimate and the API's daily series
were reviewed and need no change: they use elapsed time or relative day
indices, or read `daily_sensor_averages`, whose dates were already local.

Snapshots: the chemistry snapshot now carries `daily_version: 2`, each
bucket's `basis` and `calendar_day`, and `config.time_zone`. A snapshot
without `daily_version` (recorded example:
`Backend/tests/fixtures/snapshot_v2_utc_days.json`) still loads:

- Pools, events, gate state and config load unchanged. A missing
  `time_zone` takes the default.
- Old buckets hold sample values without timestamps, so they cannot be
  re-bucketed into local days, and splitting or merging their means
  would not be accurate. Each one is kept unchanged under a
  `legacy:YYYY-MM-DD` key, marked `basis: legacy`, and still counts in
  the trend. It is never merged with a local bucket for the same date.
  Legacy buckets leave the 30-day window within 30 days of the upgrade.
- Limitation: until then the trend mixes days bounded at 08:00 SGT with
  days bounded at midnight. On the upgrade day one UTC-bounded bucket
  and one local bucket can each hold part of the same hours, so either
  may fall below the 6-sample minimum and be skipped.
  `WaterChemistryEngine.daily_provenance()` lists the legacy dates still
  held, and loading such a snapshot logs `legacy_daily_aggregates_kept`.

The SQL side changed in `0006_hourly_history_utc_instants.sql`:
`get_pond_telemetry_history` returns real UTC instants (defect D9), so a
session whose time zone is not UTC no longer shifts the hourly buckets.

## Camera state machine and capture schedule (issue #38)

Files: `Backend/koi/camera/hsvEngine.py`, `Backend/koi/camera/imageSchedule.py`,
`Embedded/camera_node/camera_node.ino`.

| Constant | Old | New | Why |
|---|---|---|---|
| Frames to enter dynamic (`DYNAMIC_ENTER_FRAMES`, `CAMERA_DYNAMIC_ENTER_FRAMES`) | 1 | 2 | One noisy frame (a ripple, a cloud) put the camera on the fast schedule. Two raised frames in a row (green-ratio rise above 0.05 over the smoothed baseline) are now needed. |
| Frames to leave dynamic (`DYNAMIC_EXIT_FRAMES`, `CAMERA_DYNAMIC_EXIT_FRAMES`) | 1 | 3 | One stable frame dropped back to the base schedule mid-bloom. Three stable frames in a row are now needed. |
| Dynamic interval (`DYNAMIC_SLEEP_SEC`, removed) | 30 min fixed, marked PLACEHOLDER | 2 h, 1 h or 30 min (`DYNAMIC_STEP_SEC`) | The interval now shortens as the frame's green-ratio rise grows: 2 h up to a rise of 0.10, 1 h up to 0.20, 30 min above (`DYNAMIC_RATE_LEVELS`, `CAMERA_DYNAMIC_RATE_LEVELS`). The rise is measured per frame against the smoothed baseline, not per hour. |
| Firmware lower clamp on the server's sleep (`MIN_SLEEP_SEC`) | 30 s | 15 min, from `CAMERA_MIN_SLEEP_SEC` in `secrets.h`; down to 30 s only with `CAMERA_TEST_BUILD` | A misconfigured or test-mode server could otherwise wake the camera every 30 s and drain the battery. |

Unchanged: `ALPHA` 0.2, `DYNAMIC_RATE_THRESHOLD` 0.05, `ANOMALY_THRESHOLD`
0.40, `CLEAR_THRESHOLD` 0.15, the obstruction interval (2 h), the base slots
and the server's 60 s .. 24 h clamp.

The stored `imageTable.current_state` grows from `[label, smoothed]` to
`[label, smoothed, raised_frames, stable_frames]`. Rows in the old shape load
with both counters at 0; the app only reads element 0.


## Projection sensitivity ranges (issue #31)

The central constants remain unchanged: TAN nitrification 0.05/hour,
nitrite nitrification 0.035/hour, wind shelter factor 0.6, assumed depth
1.2 m, and fallback algae intrinsic rate 0.45/day.
`Backend/koi/models/uncertainty.json` records low/central/high values and
a source comment for each range. Nitrification and fallback algae rates
use an explicitly assumed +/-50% sensitivity range; shelter uses
0.4/0.6/0.8. These are not measured confidence intervals. Assumed depth
uses the existing documented typical 1.0 to 1.5 m range; a measured
profile or query depth stays fixed. Camera-fitted and declining algae
rates stay fixed; only the literature fallback varies.

Forecasts take the pointwise minimum and maximum of three paired
scenarios, including the central one. Pairing all low constants and all
high constants bounds these scenarios, not every possible combination
of parameters. First-crossing ranges retain day-zero breaches and mark
the upper day unknown if any scenario stays below threshold through the
horizon. The live engines, existing snapshots and default kinetics use
the same central values as before.
## TDS owner questions (issue #35)

No existing numeric constants change. New prompt defaults are: minimum
step 20 ppm, signal confidence threshold 0.8 and per-pond cool-down 24 hours.
They are settings, and `KOI_TDS_PROMPTS=false` disables observation and answers.
The viability detector's existing 12-reading median baseline and two-reading
persistence are unchanged. Its floor is the greater of the configured minimum
and three times the baseline's max-minus-min spread. Step signal confidence
is `max(0, 1 - spread / abs(delta))`; this is a heuristic, not a calibrated
probability of an intervention.

A slope uses the latest 12 readings: its net change must exceed the minimum
step, no adjacent change may exceed that minimum, and the linear fit's
R-squared must meet the confidence threshold. The fitted rate is ppm/hour,
using reading timestamps. Both rises and falls are candidates. Missing,
nonfinite and out-of-range TDS (outside the existing 5..5000 ppm channel
bounds) are excluded. Duplicate or older timestamps do not advance the baseline.

An applied ledger event within six hours before the signal start through
its confirmation supplies an explanation, so no question is created.
Cool-down begins at confirmation time, including when the question is later
dismissed. Detected steps reset the baseline even when their questions are
suppressed. A rise suggests salt or filter cleaning; a fall suggests water
change, top-up or filter cleaning. These are hypotheses, not event detections.
An owner can name any supported event and must provide the normal event
amounts. No mass or volume is inferred from the TDS signal.

Snapshot version 5 stores the rolling baseline, pending deviation, slope
window, questions, answers and rejections in each pond's existing snapshot.
Older versions load empty prompt state without changing engine history.
`none of these` suppresses all offered event kinds for later signals of the
same type and direction on this pond. `dismiss` closes only that question.
