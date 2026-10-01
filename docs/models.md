# Model constant changes

Every change to a model's numeric constants (rates, thresholds, weights,
intervals): the old value, the new value and why. Newest first.

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
