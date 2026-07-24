''' Defining Camera states
1. Default schedule state:
-Take a series of 5 images at fixed timings
- 8AM/10AM/12PM/2PM/4PM/6PM
2. Enhanced Monitoring state:
- Dynamically calculated schedule for taking photos
- 2 hour / 1 hour / 30 min time spacings
3. Obstruction state:
- Fixed 1 hours schedule for taking pictures

imageSchedule will compute a time to target , encode in JSON and respond to the ESP32
'''
import datetime

BASE_FIXED_TIMINGS = ['08:00', '10:00', '12:00', '14:00', '16:00', '18:00']
# Hardcoding Obstruction sleep state to 2
obstructionstate_sleep = 2

def get_base_schedule_sleep_seconds() -> int:
    """
    Calculates the exact number of seconds the ESP32 should deep-sleep
    to wake up at the next target window (8am, 10am, 12pm, 2pm, 4pm, 6pm).
    Handles overnight rollover to 8:00 AM tomorrow automatically.
    """
    now = datetime.datetime.now()
    today = datetime.date.today()
    
    # Look for the next upcoming slot today
    for time_str in BASE_FIXED_TIMINGS:
        target_time = datetime.datetime.strptime(time_str, "%H:%M").time()
        target_dt = datetime.datetime.combine(today, target_time)
        if target_dt > now:
            # Next slot today
            sleep_duration = (target_dt - now).total_seconds()
            return int(sleep_duration)
            
    # Next Slot tomorrow 
    tomorrow = today + datetime.timedelta(days=1)
    first_slot_time = datetime.datetime.strptime(BASE_FIXED_TIMINGS[0], "%H:%M").time()
    next_day_target_dt = datetime.datetime.combine(tomorrow, first_slot_time)
    
    sleep_duration = (next_day_target_dt - now).total_seconds()
    return int(sleep_duration)


def get_obstructionstate_sleep_seconds()-> int:
    return obstructionstate_sleep * 60 * 60 

def get_dynamicstate_sleep_seconds()-> int:
    smart_dynamic_state=0
    return smart_dynamic_state
if __name__ == "__main__":
    sleep_sec = get_base_schedule_sleep_seconds()
    print(f"ESP32 should deep sleep for: {sleep_sec} seconds ({sleep_sec / 3600:.2f} hours)")


