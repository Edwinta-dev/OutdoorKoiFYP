''' Defining Camera states
1. Default schedule state:
- Take a series of 6 images at fixed timings
- 8AM/10AM/12PM/2PM/4PM/6PM (Singapore time)
2. Enhanced Monitoring state:
- Dynamically calculated schedule for taking photos
- 2 hour / 1 hour / 30 min time spacings
3. Obstruction state:
- Fixed 2 hour schedule for taking pictures

imageSchedule computes the next wake (sleep_sec, a reason code and next_at, the local
wake time), camera.py encodes it in JSON and returns it to the ESP32.

Reason codes (issue #38), sent as `reason` in every reply:
  slot             base state, sleeping to the next fixed daylight slot
  dynamic_rising   dynamic state, on the 2 h / 1 h / 30 min step for the frame's rise
  obstruction_hold obstruction state, fixed 2 h retry
  night_base       outside daylight, any state sleeps to the next slot
  error_fallback   the request failed before a schedule was computed
  test_mode        TEST_MODE short intervals
  quality_retry    the frame failed the quality gate (issue #40): retry within
                   QUALITY_RETRY_SEC in daylight instead of the state's wake

CHANGES FOR HOSTING (PythonAnywhere):
- Server clocks run in UTC. All times are now computed in Asia/Singapore explicitly,
  otherwise the 8AM slot would fire at 4PM Singapore time.
- The real slot calculation is restored (it previously fell through to a test value).
- EARLY_WAKE_GRACE: the ESP32's deep-sleep timer drifts, so it may wake a few minutes
  before a slot. A wake within the grace window counts as that slot, instead of
  scheduling a second wake a few seconds later.
- Outside daylight hours every state falls back to the base schedule, so an
  obstruction/dynamic state can't keep the camera waking all night for dark frames.
- TEST_MODE=1 (Settings.test_mode) keeps the short test intervals you were using.
- The time zone comes from Settings.timezone; camera.py passes `now` in it.
- Dynamic interval (issue #38): replaces the fixed 30 min placeholder. The
  frame's green-ratio rise over the smoothed baseline picks the step:
  2 h up to DYNAMIC_RATE_LEVELS[0], 1 h up to DYNAMIC_RATE_LEVELS[1], 30 min above.
- Quality retry (issue #40): a frame that fails the quality gate does not
  change the state, and the wake becomes the shorter of the state's wake and
  QUALITY_RETRY_SEC (30 min) in daylight. At night and in TEST_MODE the
  state's wake stands.
'''
from datetime import datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")

BASE_FIXED_TIMINGS = ['08:00', '10:00', '12:00', '14:00', '16:00', '18:00']
EARLY_WAKE_GRACE = timedelta(minutes=10)

OBSTRUCTION_SLEEP_SEC = 2 * 60 * 60

# Dynamic steps: rise <= levels[0] -> 2 h, <= levels[1] -> 1 h, above -> 30 min.
# The levels are overridable through Settings.camera_dynamic_rate_levels.
DYNAMIC_RATE_LEVELS = (0.10, 0.20)
DYNAMIC_STEP_SEC = (2 * 60 * 60, 60 * 60, 30 * 60)

TEST_SLEEP_SEC = {"base": 100, "obstruction": 80, "dynamic": 60}

QUALITY_RETRY_SEC = 30 * 60

REASONS = ("slot", "dynamic_rising", "obstruction_hold", "night_base", "error_fallback", "test_mode",
           "quality_retry")

MIN_SLEEP_SEC = 60
MAX_SLEEP_SEC = 24 * 60 * 60


def _slots_on(day: datetime) -> list:
    out = []
    for time_str in BASE_FIXED_TIMINGS:
        h, m = map(int, time_str.split(":"))
        out.append(day.replace(hour=h, minute=m, second=0, microsecond=0))
    return out


def _clamp(seconds: float) -> int:
    return int(max(MIN_SLEEP_SEC, min(MAX_SLEEP_SEC, seconds)))


def get_base_schedule_sleep_seconds(now: datetime | None = None) -> int:
    """Seconds until the next fixed slot, rolling over to 8:00 AM tomorrow."""
    now = now or datetime.now(TZ)
    earliest = now + EARLY_WAKE_GRACE
    for target in _slots_on(now):
        if target > earliest:
            return _clamp((target - now).total_seconds())
    first_tomorrow = _slots_on(now + timedelta(days=1))[0]
    return _clamp((first_tomorrow - now).total_seconds())


def is_daylight(now: datetime | None = None) -> bool:
    """True between (first slot - grace) and (last slot + grace)."""
    now = now or datetime.now(TZ)
    slots = _slots_on(now)
    return slots[0] - EARLY_WAKE_GRACE <= now <= slots[-1] + EARLY_WAKE_GRACE


def get_obstructionstate_sleep_seconds() -> int:
    return OBSTRUCTION_SLEEP_SEC


def get_dynamicstate_sleep_seconds(rise: float = 0.0, levels: tuple = DYNAMIC_RATE_LEVELS) -> int:
    """2 h, 1 h or 30 min, shorter as the frame's green-ratio rise passes each level."""
    low, high = levels
    if rise > high:
        return DYNAMIC_STEP_SEC[2]
    if rise > low:
        return DYNAMIC_STEP_SEC[1]
    return DYNAMIC_STEP_SEC[0]


class NextWake(NamedTuple):
    sleep_sec: int
    reason: str
    next_at: datetime

    def reply(self) -> dict:
        """The keys every camera reply carries."""
        return {"sleep_sec": self.sleep_sec, "reason": self.reason,
                "next_at": self.next_at.isoformat(timespec="seconds")}


def _wake(now: datetime, sleep_sec: int, reason: str) -> NextWake:
    at = now + timedelta(seconds=sleep_sec)
    rounded = at.replace(microsecond=0) + timedelta(seconds=1 if at.microsecond >= 500_000 else 0)
    return NextWake(sleep_sec, reason, rounded)


def fallback_wake(sleep_sec: int, now: datetime | None = None) -> NextWake:
    """For a reply sent before any schedule was computed."""
    return _wake(now or datetime.now(TZ), _clamp(sleep_sec), "error_fallback")


def next_wake(state_label: str, now: datetime | None = None, test_mode: bool = False,
              rise: float = 0.0, levels: tuple = DYNAMIC_RATE_LEVELS) -> NextWake:
    """Single entry point used by camera.py. rise is the frame's green-ratio
    rise over the smoothed baseline (hsvEngine.frame_rise)."""
    now = now or datetime.now(TZ)
    if test_mode:
        return _wake(now, TEST_SLEEP_SEC.get(state_label, 100), "test_mode")

    if not is_daylight(now):
        return _wake(now, get_base_schedule_sleep_seconds(now), "night_base")

    if state_label == "obstruction":
        return _wake(now, _clamp(get_obstructionstate_sleep_seconds()), "obstruction_hold")
    if state_label == "dynamic":
        return _wake(now, _clamp(get_dynamicstate_sleep_seconds(rise, levels)), "dynamic_rising")
    return _wake(now, get_base_schedule_sleep_seconds(now), "slot")


def quality_retry_wake(wake: NextWake, now: datetime | None = None, test_mode: bool = False) -> NextWake:
    """The wake for a frame that failed the quality gate: wake (the state's
    own) or QUALITY_RETRY_SEC in daylight, whichever is sooner."""
    now = now or datetime.now(TZ)
    if test_mode or not is_daylight(now) or wake.sleep_sec <= QUALITY_RETRY_SEC:
        return wake
    return _wake(now, QUALITY_RETRY_SEC, "quality_retry")


def sleep_for_state(state_label: str, now: datetime | None = None, test_mode: bool = False,
                    rise: float = 0.0, levels: tuple = DYNAMIC_RATE_LEVELS) -> int:
    """next_wake's sleep_sec alone."""
    return next_wake(state_label, now, test_mode, rise, levels).sleep_sec
