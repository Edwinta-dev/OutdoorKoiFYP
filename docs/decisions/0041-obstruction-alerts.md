# 0041: Obstruction alerts into the notification outbox
Status: implemented · Issue: #41 · Date: 2026-10-10

## In short

When the pond camera enters the obstruction state, the worker now writes one outbox warning per episode ("Camera view blocked since 14:10. Check the lens and the view."). It does not repeat while the view stays blocked. When the view clears after more than 2 hours of obstruction, it writes an info-level "Camera view is clear" entry. Shorter obstructions clear without a second message.

## Problem and constraints

Issue #36 added a `camera_obstructed` trigger keyed `camera_obstructed:camera` with a 24 h cool-down. That repeated once a day during a long obstruction and said nothing when the view cleared. It also compared `current_state` with the string `"obstruction"`, but `camera.py` stores a list (`["obstruction", baseline, raised, stable, mean, count]`), so real frames never triggered it. Constraints:
- The camera's `POST /upload` response includes the tuple `evalstate` returns, so its shape cannot change. The episode start therefore cannot be stored in the state.
- The outbox `kind` list is fixed by migration 0024. The scope has no migration in it, so no new kind can be added.
- The worker does not import OpenCV.

## Approach

Everything is in `Backend/koi/notifications.py`. `Backend/koi/camera/hsvEngine.py::evalstate` is unchanged.
- `notifications::_frame_state` reads the state label from an `imageTable.current_state` value in any of the three forms: list, JSON text or bare string. It does the same job as `hsvEngine::_coerce_state` without importing cv2.
- `notifications::obstruction_episode` sorts the rows the poller already read (newest 50) and finds the latest run of consecutive `obstruction` frames. It returns:
  - `since`: the first frame of the run.
  - `until`: the last frame of the run.
  - `cleared_at`: the first frame after the run, or none while the obstruction lasts.
  - `complete`: false when the run reaches the oldest row of a full window, so `since` is only a bound.
- `notifications::camera_obstructed` then decides:
  - **Still obstructed**, the newest frame at most `OBSTRUCTION_MAX_AGE` old and `complete`: a warning keyed `camera_obstructed:camera:<since>`. The time is pond-local, and the date is added when it is not today ("since 14:10 on 9 Oct").
  - **Cleared**, more than `OBSTRUCTION_CLEAR_AFTER` after `since`, by a frame at most `OBSTRUCTION_MAX_AGE` old: an `info` entry of kind `camera_obstructed`, keyed `camera_obstructed:clear:<cleared_at>`, with `data.state = "clear"`.
- `COOLDOWNS[camera_obstructed]` went from 24 h to 365 days. Each episode has its own key, so the cool-down only has to outlast one episode. A new episode starts at a different frame, gets a new key and notifies again.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Store the obstruction start in `evalstate`'s state tuple | The tuple is returned by `POST /upload`, which is frozen. |
| New outbox kind `camera_clear` | It needs a migration that changes the kind check, and that is outside the scope. Severity `info` plus `data.state` tells the two entries apart. |
| Keep the fixed key `camera_obstructed:camera` with a long cool-down | A second obstruction within the cool-down would be silently dropped. |
| Import `hsvEngine._coerce_state` | It would pull OpenCV into the worker process. |

## Trade-offs

- The episode is rebuilt from the last 50 frames on each poll. An episode whose start has scrolled out of a full window is not warned about again. This is correct when its warning was written at the start, but if the worker was down for that whole stretch, the warning is never written. At the camera's 2 h obstruction retry interval, 50 frames covers about 4 days.
- `IMAGE_WINDOW` (50) repeats the poller's `fetch_image_history(limit=50)`. If the two drift apart, the whole-history check is wrong (see Edge cases).

## Key parameters

| Parameter | Value | How chosen | Raise / lower |
|---|---|---|---|
| `OBSTRUCTION_CLEAR_AFTER` | 2 h | From the issue | Higher: fewer "clear" messages. Lower: clears after short blockages also notify. |
| `COOLDOWNS[camera_obstructed]` | 365 days | Longer than any episode the 50-frame window can show; it fits the SQL integer | Lowering it below the length of an episode repeats the warning during that episode. |
| `IMAGE_WINDOW` | 50 | Matches `poller._poll_user` | Must equal the poller's limit. |
| `OBSTRUCTION_MAX_AGE` | 24 h (unchanged) | From #36; now also limits the age of the clearing frame | As in 0036. |
| Severities | warning (blocked), info (clear) | "low-severity" in the issue | Only affects presentation. |

## Assumptions

- Quality-failed frames carry the previous label (`camera.py::carried_state`), so they neither start nor end an episode.
- A baseline reset to `base` (by the off-ramp or a mask change) counts as the view clearing.
- `created_at` order is capture order.

## Edge cases

- A brief obstruction (2 h or less) gets only the blocked warning. Tested.
- Exactly 2 h does not count as "more than 2 hours". Tested.
- A cleared run that filled the window says "blocked since at least …". Tested.
- If the poller's limit were changed to below 50, a pond whose whole history is one obstruction run would never be warned about. Not handled.
- A camera that stops on an obstructed frame stops notifying after 24 h. As in #36.

## Changes outside the scope

- `Backend/tests/worker/test_poller_integration.py::test_obstruction_rating_discards_the_frame` now polls first when no snapshot exists. The acceptance command `pytest -k obstruction` selects this test on its own. Its module-scoped fixtures expected earlier tests to have polled, so it failed already at HEAD (44 passed, 1 failed). The full-file run is unchanged.
- `Backend/tests/worker/test_notification_outbox.py`: the pinned `COOLDOWNS` value for `camera_obstructed` was updated.

## How it was verified

- `python -m pytest -q -k obstruction`: 53 passed (before: 44 passed, 1 failed).
- New file `Backend/tests/worker/test_obstruction_alerts.py` with 8 tests. They cover:
  - the entry text and key;
  - all three `current_state` shapes;
  - dedupe across 15 obstructed frames;
  - a single clear entry, and re-arming on a new episode;
  - the 2 h boundary and the age limits;
  - the full-window bound and the date in the text;
  - the real poller writing a warning and then a clear entry.
- `python tools/check.py all`: all PASS (backend pytest 1767 passed), with the three ESP32 compile steps skipped because the esp32 core is not installed.
- SQL outbox tests passed on the local stack. An ad hoc call on the local stack, rolled back, confirmed that `enqueue_notification` accepts severity `info` with a 31,536,000 s cool-down and suppresses a repeat 200 days later.
- Not verified: real camera frames from the live pond, and the live database.

## To change this

- To report shorter blockages clearing, change `OBSTRUCTION_CLEAR_AFTER` from 2 h to a lower value. Cost: more messages.
- To make "clear" its own kind, add a migration that extends the kind check, add the kind to `MemoryStorage.NOTIFICATION_KINDS`, and change the clear entry's kind.
- Likely bug sources:
  - `IMAGE_WINDOW` drifting from the poller's limit.
  - A new `current_state` shape that `_frame_state` does not read.

## Rollback

Reverting restores #36's behaviour: a daily repeat, no clear entry, and list-shaped states never triggering. Rows already written stay valid, because the kind and severity are allowed values. Nothing reads `data.state` yet.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-10 09:18 UTC for issue #41 attempt 5; compared HEAD..working tree (base 6f9f3ad).

**Changed components**

| Component | Change | Lines +/- | Tags | Description |
|---|---|---|---|---|
| `Backend/koi/notifications.py::_frame_state` | added | +15/-0 |  | The state machine's label stored in an imageTable row's |
| `Backend/koi/notifications.py::obstruction_episode` | added | +23/-0 |  | The camera's latest obstruction episode in a newest-first window of |
| `Backend/koi/notifications.py::_clock` | added | +6/-0 |  | at as pond-local HH:MM, with the date when it is not now's day. |
| `Backend/koi/notifications.py::camera_obstructed` | modified | +33/-12 |  | An entry for the camera's obstruction episode (obstruction_episode): |
| `Backend/koi/notifications.py::COOLDOWNS` | modified | +2/-1 |  |  |
| `Backend/koi/notifications.py::OBSTRUCTION_CLEAR_AFTER` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::OBSTRUCTION` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::IMAGE_WINDOW` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::<module>` | modified | +23/-5 |  |  |

Plus 3 test file(s).

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/camera/hsvEngine.py::evalstate`, `Backend/koi/notifications.py`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), scope check, dangling reference check

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0041-obstruction-alerts.md` gives the commit.
<!-- verified-facts:end -->
