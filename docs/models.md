# Model constant changes

Every change to a model's numeric constants (rates, thresholds, weights,
intervals): the old value, the new value and why. Newest first.

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
