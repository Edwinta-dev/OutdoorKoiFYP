''' Defining Camera states
1. Default schedule state:
- Take a series of 6 images at fixed timings
- 8AM/10AM/12PM/2PM/4PM/6PM (Singapore time)
2. Enhanced Monitoring state:
- Dynamically calculated schedule for taking photos
- 2 hour / 1 hour / 30 min time spacings
3. Obstruction state:
- Fixed 2 hour schedule for taking pictures

imageSchedule computes a sleep duration, camera.py encodes it in JSON and returns it to the ESP32.

CHANGES FOR HOSTING (PythonAnywhere):
- Server clocks run in UTC. All times are now computed in Asia/Singapore explicitly,
  otherwise the 8AM slot would fire at 4PM Singapore time.
- The real slot calculation is restored (it previously fell through to a test value).
- EARLY_WAKE_GRACE: the ESP32's deep-sleep timer drifts, so it may wake a few minutes
  before a slot. A wake within the grace window counts as that slot, instead of
  scheduling a second wake a few seconds later.
- Outside daylight hours every state falls back to the base schedule, so an
  obstruction/dynamic state can't keep the camera waking all night for dark frames.
- TEST_MODE=1 (env var) keeps the short test intervals you were using.
'''
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")

BASE_FIXED_TIMINGS = ['08:00', '10:00', '12:00', '14:00', '16:00', '18:00']
EARLY_WAKE_GRACE = timedelta(minutes=10)

OBSTRUCTION_SLEEP_SEC = 2 * 60 * 60
DYNAMIC_SLEEP_SEC = 30 * 60        # PLACEHOLDER until the 2h/1h/30min dynamic rule is designed

TEST_SLEEP_SEC = {"base": 100, "obstruction": 80, "dynamic": 60}

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


def get_dynamicstate_sleep_seconds() -> int:
    return DYNAMIC_SLEEP_SEC


def sleep_for_state(state_label: str, now: datetime | None = None) -> int:
    """Single entry point used by camera.py."""
    # Read at call time: camera.py loads .env after importing this module
    if os.environ.get("TEST_MODE", "0") == "1":
        return TEST_SLEEP_SEC.get(state_label, 100)

    now = now or datetime.now(TZ)
    if not is_daylight(now):
        return get_base_schedule_sleep_seconds(now)

    if state_label == "obstruction":
        return _clamp(get_obstructionstate_sleep_seconds())
    if state_label == "dynamic":
        return _clamp(get_dynamicstate_sleep_seconds())
    return get_base_schedule_sleep_seconds(now)


if __name__ == "__main__":
    sleep_sec = get_base_schedule_sleep_seconds()
    print(f"ESP32 should deep sleep for: {sleep_sec} seconds ({sleep_sec / 3600:.2f} hours)")
