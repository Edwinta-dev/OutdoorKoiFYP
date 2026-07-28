DEFAULT_STATE = "base"
state = DEFAULT_STATE  # Global variable

import cv2
import numpy as np

# --- CONFIGURATION PARAMETERS ---
# HSV Color Bounds for Green/Algae Isolation
# OpenCV Hue ranges from 0 to 180 (35-85 degrees maps to 18-42)
LOWER_GREEN = np.array([18, 40, 40], dtype=np.uint8)
UPPER_GREEN = np.array([42, 255, 255], dtype=np.uint8)

# Smoothing & Anomaly Thresholds
ALPHA = 0.2                       # Low-pass filter smoothing factor (EMA)
ANOMALY_THRESHOLD = 0.40          # 40% jump triggers OBSTRUCTION state
DYNAMIC_RATE_THRESHOLD = 0.05     # 5% hourly rate triggers DYNAMIC schedule

def analyze_image_bytes(image_bytes: bytes) -> float:
    """
    Decodes raw binary image bytes into OpenCV format, extracts the ROI,
    applies the HSV green mask, and returns the green coverage ratio (0.0 to 1.0).
    
    roi_bounds: Use entire image frame
    """
    # 1. Decode JPEG image bytes directly in RAM
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)    
    if img is None:
        raise ValueError("Failed to decode image bytes from ESP32 payload.")
    roi = img
    # 3. Convert to HSV Color Space
    hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    # 4. Apply Binary Mask for Green Hue Ranges
    mask = cv2.inRange(hsv_roi, LOWER_GREEN, UPPER_GREEN)
    # 5. Compute Green Pixel Coverage Ratio
    total_pixels = mask.size
    green_pixels = cv2.countNonZero(mask)
    green_ratio = green_pixels / float(total_pixels)
    return round(green_ratio, 4)


def evalstate(
    current_green_ratio: float, 
    last_smoothed_green: float, 
    current_state: str) -> tuple[str, float]:
    """
    Evaluates state hierarchy transitions and updates low-pass filter baseline.
    Returns:
        tuple: (new_state, updated_smoothed_green)
    """
    # Calculate step change relative to last baseline
    delta_g = current_green_ratio - last_smoothed_green
    # --- PRIORITY 1: OBSTRUCTION CHECK ---
    # Triggered by sudden physical blockages (>40% delta jump)
    if delta_g > ANOMALY_THRESHOLD or current_state == "obstruction":
        # Do NOT update smoothed baseline with corrupt/obstructed reading
        state = "obstruction"
        current_green = last_smoothed_green
    # --- PRIORITY 2: DYNAMIC ENHANCED MONITORING CHECK ---
    # Triggered by biological rate acceleration
    if delta_g > DYNAMIC_RATE_THRESHOLD or current_state == "dynamic":
        # Update low-pass filter (Exponential Moving Average)
        state = "dynamic"
        current_green = round((ALPHA * current_green_ratio) + ((1 - ALPHA) * last_smoothed_green), 4)
    # --- PRIORITY 3: BASE SCHEDULE (DEFAULT FALLBACK) ---
    else:
        state = "base"
        current_green = round((ALPHA * current_green_ratio) + ((1 - ALPHA) * last_smoothed_green),4)

    return (state, current_green)

def getstate():
    global state
    return state

