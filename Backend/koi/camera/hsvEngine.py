"""
hsvEngine.py  (patched)

HSV green-coverage analysis + adaptive-cycling state machine for the
ESP32-CAM pond camera.

--------------------------------------------------------------------
WHAT CHANGED FROM THE ORIGINAL, AND WHY
--------------------------------------------------------------------
Three defects in the original evalstate() meant the state machine never
actually behaved as its own docstring described. All three are fixed
here; each is marked [FIX n] at the site.

[FIX 1] "obstruction" was an unreachable output state.
    The original used three consecutive `if` blocks, not if/elif/else:

        if delta_g > ANOMALY_THRESHOLD or current_state == "obstruction":
            state = "obstruction"          # <- assigned...
        if delta_g > DYNAMIC_RATE_THRESHOLD or current_state == "dynamic":
            state = "dynamic"              # <- ...then always overwritten
        else:
            state = "base"

    Because ANOMALY_THRESHOLD (0.40) is greater than
    DYNAMIC_RATE_THRESHOLD (0.05), any delta big enough to trip
    obstruction is necessarily also big enough to trip dynamic on the
    very next line. And the second block is an if/else, so it assigns on
    every single call. Net effect: evalstate could only ever return
    "base" or "dynamic". The obstruction branch, the
    get_obstructionstate_sleep_seconds() schedule, and the "don't poison
    the baseline with a corrupt reading" logic were all dead code.

[FIX 2] The state stickiness checks could never fire.
    camera.py stores the whole (state, smoothed) tuple into the
    `current_state` column, so it comes back out of Supabase as a JSON
    array - e.g. ["base", 0.1235]. That array was then passed straight
    back in as `current_state`, so `current_state == "obstruction"` was
    comparing a list to a string: always False. Fixed by normalising the
    incoming value (see _coerce_state) rather than trusting the caller.

[FIX 3] The EMA was being re-seeded with the raw previous reading.
    get_prev_image_data() returns `green_ratio` (the RAW ratio) as
    prev_green_ratio, but the smoothed baseline actually lives in
    current_state[1]. So the low-pass filter was blending each new
    reading with the previous RAW reading rather than with the running
    average - a 2-sample smoother, not an EMA.

    This is visible in the live data for user 15:
        row 1: green_ratio 0.0174, current_state ["base", 0.1235]
        row 2: green_ratio 0.0110, current_state ["base", 0.0161]
        0.2*0.0110 + 0.8*0.0174 = 0.01612  <- matches (used raw)
        0.2*0.0110 + 0.8*0.1235 = 0.10100  <- would have been the real EMA
    The stored baseline was tracking noise instead of trend, which is
    exactly what the filter existed to prevent.

[FIX 4, additive] Obstruction now has an exit condition.
    Once latched, the original had no way back out (had it latched at
    all). A frame is now considered clear again when it returns to
    within CLEAR_THRESHOLD of the trusted baseline, so a leaf blowing
    off the lens un-sticks the state instead of pinning the camera on
    the obstruction schedule indefinitely.

[Issue #38] Hysteresis on the dynamic state.
    One raised frame used to enter dynamic and one stable frame left it.
    Dynamic is now entered after DYNAMIC_ENTER_FRAMES consecutive raised
    frames and left after DYNAMIC_EXIT_FRAMES consecutive stable ones.
    The two streak counters are stored with the state, so current_state
    is now [label, smoothed, raised_frames, stable_frames]. Rows written
    before the change ([label, smoothed]) load with both counters at 0.

Also note: analyze_image_bytes' docstring said it "extracts the ROI" but
used the whole frame. Rather than silently change behaviour, the ROI is
now an explicit optional argument defaulting to the full frame, and the
docstring says so.
"""
import cv2
import numpy as np

DEFAULT_STATE = "base"

# --- CONFIGURATION PARAMETERS ---
# HSV Color Bounds for Green/Algae Isolation
# OpenCV Hue ranges 0-180, so 35-85 degrees maps to roughly 18-42.
LOWER_GREEN = np.array([18, 40, 40], dtype=np.uint8)
UPPER_GREEN = np.array([42, 255, 255], dtype=np.uint8)

# Smoothing & Anomaly Thresholds
ALPHA = 0.2                       # Low-pass filter smoothing factor (EMA)
ANOMALY_THRESHOLD = 0.40          # 40% jump triggers OBSTRUCTION state
DYNAMIC_RATE_THRESHOLD = 0.05     # 5% step triggers DYNAMIC schedule
CLEAR_THRESHOLD = 0.15            # [FIX 4] back within 15% of baseline = clear

# Hysteresis (issue #38), overridable through Settings
DYNAMIC_ENTER_FRAMES = 2          # consecutive raised frames to enter DYNAMIC
DYNAMIC_EXIT_FRAMES = 3           # consecutive stable frames to leave DYNAMIC


def analyze_image_bytes(image_bytes: bytes, roi_bounds: tuple | None = None) -> float:
    """Decodes raw JPEG bytes, optionally crops to a region of interest,
    applies the HSV green mask and returns green coverage as a ratio 0..1.

    roi_bounds: (x, y, w, h) in pixels, or None for the entire frame
    (the default, matching existing deployed behaviour).

    Cropping to just the water surface is worth doing when the camera
    frame includes decking, planting or sky - green_ratio is a fraction
    of TOTAL pixels, so non-water background permanently dilutes the
    signal and makes absolute thresholds meaningless across installs.
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Failed to decode image bytes from ESP32 payload.")

    if roi_bounds is not None:
        x, y, w, h = roi_bounds
        roi = img[y:y + h, x:x + w]
        if roi.size == 0:
            raise ValueError(f"ROI {roi_bounds} is outside the {img.shape} frame.")
    else:
        roi = img

    hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv_roi, LOWER_GREEN, UPPER_GREEN)
    green_pixels = cv2.countNonZero(mask)
    return round(green_pixels / float(mask.size), 4)


def _coerce_state(current_state) -> str:
    """[FIX 2] Normalises whatever came back out of Supabase's
    `current_state` column into a plain state string.

    Accepts: "base", ["base", 0.1235], ("base", 0.1235), '["base",0.12]',
    None. Anything unrecognised falls back to DEFAULT_STATE rather than
    raising - a malformed history row should not take the camera down.
    """
    if current_state is None:
        return DEFAULT_STATE
    if isinstance(current_state, str):
        s = current_state.strip()
        if s.startswith("["):
            try:
                import json
                parsed = json.loads(s)
                if isinstance(parsed, list) and parsed:
                    return str(parsed[0])
            except (ValueError, TypeError):
                return DEFAULT_STATE
        return s or DEFAULT_STATE
    if isinstance(current_state, (list, tuple)) and current_state:
        return str(current_state[0])
    return DEFAULT_STATE


def _coerce_baseline(current_state, fallback: float) -> float:
    """[FIX 3] Pulls the SMOOTHED baseline out of the stored
    (state, smoothed) pair. Falls back to the supplied value (normally
    the previous raw green_ratio) only when the pair is unavailable,
    which is the correct behaviour for the very first reading."""
    candidate = None
    if isinstance(current_state, str) and current_state.strip().startswith("["):
        try:
            import json
            current_state = json.loads(current_state)
        except (ValueError, TypeError):
            current_state = None
    if isinstance(current_state, (list, tuple)) and len(current_state) >= 2:
        candidate = current_state[1]
    try:
        return float(candidate) if candidate is not None else float(fallback)
    except (TypeError, ValueError):
        return float(fallback)


def _coerce_counters(current_state) -> tuple[int, int]:
    """(raised_frames, stable_frames) stored after the baseline, or (0, 0)
    for a row written before issue #38 or a malformed one."""
    if isinstance(current_state, str) and current_state.strip().startswith("["):
        try:
            import json
            current_state = json.loads(current_state)
        except (ValueError, TypeError):
            return 0, 0
    if not isinstance(current_state, (list, tuple)) or len(current_state) < 4:
        return 0, 0
    try:
        return max(0, int(current_state[2])), max(0, int(current_state[3]))
    except (TypeError, ValueError):
        return 0, 0


def frame_rise(current_green_ratio: float, last_smoothed_green: float, current_state=DEFAULT_STATE) -> float:
    """Green-ratio rise of this frame over the smoothed baseline: the rate
    of change per frame that sets the dynamic capture interval."""
    return current_green_ratio - _coerce_baseline(current_state, last_smoothed_green)


def evalstate(
    current_green_ratio: float,
    last_smoothed_green: float,
    current_state=DEFAULT_STATE,
    enter_frames: int = DYNAMIC_ENTER_FRAMES,
    exit_frames: int = DYNAMIC_EXIT_FRAMES,
) -> tuple:
    """Evaluates state transitions and updates the low-pass baseline.

    Returns (new_state, updated_smoothed_green, raised_frames, stable_frames).

    Priority order, now genuinely exclusive ([FIX 1] if/elif/else):
      1. obstruction - a physically implausible jump, or still latched
         from a previous frame and not yet cleared
      2. dynamic     - real biological acceleration, poll faster. Entered
         after enter_frames consecutive raised frames (rise above
         DYNAMIC_RATE_THRESHOLD), left after exit_frames consecutive
         stable ones
      3. base        - nothing unusual, keep the fixed daily schedule

    raised_frames and stable_frames are the current streaks of raised and
    stable frames; at most one is non-zero. An obstruction frame resets both.

    last_smoothed_green should be the SMOOTHED baseline from the previous
    row, not its raw green_ratio. If the caller passes the stored
    (state, smoothed, ...) list as current_state, the correct baseline and
    counters are recovered from it automatically ([FIX 3]).
    """
    state_label = _coerce_state(current_state)
    baseline = _coerce_baseline(current_state, last_smoothed_green)
    raised, stable = _coerce_counters(current_state)

    delta_g = current_green_ratio - baseline

    # --- PRIORITY 1: OBSTRUCTION ---------------------------------
    if delta_g > ANOMALY_THRESHOLD:
        # A jump this large is not algae growing - it is something in
        # front of the lens. Do NOT let it touch the baseline.
        return ("obstruction", round(baseline, 4), 0, 0)

    if state_label == "obstruction":
        # [FIX 4] Latched from a previous frame - stay latched until the
        # view returns close to the trusted baseline.
        if abs(delta_g) > CLEAR_THRESHOLD:
            return ("obstruction", round(baseline, 4), 0, 0)
        # Cleared. Resume smoothing from this frame; the streaks restart here.
        state_label, raised, stable = "base", 0, 0

    smoothed = round((ALPHA * current_green_ratio) + ((1 - ALPHA) * baseline), 4)

    # --- PRIORITY 2: DYNAMIC (with hysteresis) --------------------
    if delta_g > DYNAMIC_RATE_THRESHOLD:
        raised, stable = raised + 1, 0
    else:
        raised, stable = 0, stable + 1

    if state_label == "dynamic":
        # Stay in dynamic until the pond has held still for exit_frames.
        if stable >= exit_frames:
            return ("base", smoothed, raised, stable)
        return ("dynamic", smoothed, raised, stable)

    if raised >= enter_frames:
        return ("dynamic", smoothed, raised, stable)

    # --- PRIORITY 3: BASE ----------------------------------------
    return ("base", smoothed, raised, stable)
