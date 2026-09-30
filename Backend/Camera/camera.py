import os
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from supabase import Client, create_client

import hsvEngine
import imageSchedule

# CHANGED: load .env from this file's folder explicitly (override=True: .env always
# wins over anything already set in the environment). On PythonAnywhere the
# working directory is not the project folder.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

# Storage bucket the analysed frames are written to.
#
# Lifted out of upload_image() so it is not a literal known only to this
# file - the Flutter client builds its own URLs against the same bucket
# and the two need to agree:
#   Backend .env   -> POND_IMAGE_BUCKET=imageAnalysisBucket
#   Flutter build  -> POND_IMAGE_BUCKET in MobileUI/mobile_app/env/*.json
# Default matches the bucket in use, so nothing breaks if it is unset.
IMAGE_BUCKET = os.environ.get("POND_IMAGE_BUCKET", "imageAnalysisBucket")

# NEW: the server is now on the public internet. If DEVICE_TOKEN is set in .env,
# uploads without a matching X-Device-Token header are rejected.
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")

# NEW: sleep time returned whenever something fails, so the ESP32 never loops fast
FALLBACK_SLEEP_SEC = 7200
MAX_UPLOAD_BYTES = 1_000_000

app = Flask(__name__)
CORS(app)

# Initialize Supabase Client
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICEROLE_KEY")
)


def error_reply(message: str, code: int):
    """Every error still carries a sleep time for the ESP32."""
    print(f"[ERROR] {message}")
    return jsonify({"status": "error", "message": message,
                    "sleep_sec": FALLBACK_SLEEP_SEC}), code


# Helper Function: Fetch Previous Image Data
def get_prev_image_data(user_id: str):
    """Returns (prev_raw_green_ratio, prev_current_state).

    The whole current_state value is returned untouched and handed
    straight to evalstate(), which recovers the smoothed baseline from it
    itself. The raw ratio is still returned as a fallback for the very
    first reading, when no pair exists yet.
    """
    try:
        response = (
            supabase.table("imageTable")
            .select("green_ratio, current_state")
            .eq("user_ID", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if response.data and len(response.data) > 0:
            latest = response.data[0]
            prev_green_ratio = latest.get("green_ratio", 0.15)
            prev_state = latest.get("current_state", hsvEngine.DEFAULT_STATE)
            return prev_green_ratio, prev_state

        print("First reading or no previous data found. Defaulting baselines.")
        return 0.15, hsvEngine.DEFAULT_STATE

    except Exception as e:
        print(f"[SUPABASE ERROR] get_prev_image_data failed: {str(e)}")
        return 0.15, hsvEngine.DEFAULT_STATE


# Helper Function: Insert Current Reading Record
def push_current_data(green_ratio: float, user_id: str, state, public_url: str):
    try:
        supabase.table("imageTable").insert({
            "user_ID": user_id,
            "green_ratio": green_ratio,
            "current_state": state,
            "imageURL": public_url
        }).execute()
        print("[DATABASE SUCCESS] Current data inserted successfully.")
    except Exception as e:
        print(f"[SUPABASE ERROR] push_current_data failed: {str(e)}")


@app.route('/', methods=['GET'])
def health():
    return "OutdoorKoi camera API OK"


@app.route('/upload', methods=['POST'])
def upload_image():
    if DEVICE_TOKEN and request.headers.get("X-Device-Token") != DEVICE_TOKEN:
        return error_reply("unauthorised", 401)

    file_bytes = request.data
    if not file_bytes:
        return error_reply("Empty data packet from ESP32", 400)
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        return error_reply(f"Payload too large ({len(file_bytes)} bytes)", 413)

    user_id = request.headers.get('X-User-ID', 'default_user')
    print(f"Processing upload for User_ID: {user_id}")

    # 1. Compute HSV Green Ratio for current frame
    # CHANGED: a corrupt JPEG used to raise here, outside any try, and the ESP32
    # got an HTML 500 page with no sleep time in it.
    try:
        green_ratio = hsvEngine.analyze_image_bytes(file_bytes)
    except Exception as e:
        return error_reply(f"Image analysis failed: {e}", 422)

    # 2. Fetch historical state & baseline
    prev_green_ratio, prev_state = get_prev_image_data(user_id)

    # 3. Compute new state transition.
    state = hsvEngine.evalstate(green_ratio, prev_green_ratio, prev_state)
    print(f"New computed state: {state}")

    # 4. Next sleep interval
    # CHANGED: one call, Singapore time, night-time fallback to base schedule
    time_to_next_image = imageSchedule.sleep_for_state(state[0])
    print(f"Next scheduled sleep: {time_to_next_image} seconds")

    try:
        # 5. Upload image to Supabase Storage
        # Object path is "{user_id}/{unix_seconds}_photo.jpg" - the same
        # id the app onboards with, so the client can resolve a user's
        # frames without any help from this service.
        filename = f"{user_id}/{int(time.time())}_photo.jpg"
        bucket_name = IMAGE_BUCKET

        supabase.storage.from_(bucket_name).upload(
            path=filename,
            file=file_bytes,
            file_options={"content-type": "image/jpeg", "upsert": "true"}
        )

        public_url = supabase.storage.from_(bucket_name).get_public_url(filename)
        # supabase-py has historically appended a bare "?" here, which
        # makes cache keys inconsistent downstream. Strip it at the source.
        public_url = public_url.rstrip("?&")
        print(f"Storage Upload Complete: {public_url}")

        # 6. Save telemetry metrics to Supabase Table
        push_current_data(green_ratio, user_id, state, public_url)

        # 7. Return payload to ESP32
        return jsonify({
            "status": "success",
            "state": state,
            "sleep_sec": time_to_next_image
        }), 200

    except Exception as e:
        # The analysis succeeded, so the computed schedule is still valid
        print(f"[PIPELINE ERROR] Upload pipeline failed: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e),
            "sleep_sec": time_to_next_image
        }), 500


# Only used when running on your laptop. PythonAnywhere imports `app` via its WSGI file.
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
