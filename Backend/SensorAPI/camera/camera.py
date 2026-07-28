from flask import Flask, request, jsonify
import os
import time
from dotenv import load_dotenv
from flask_cors import CORS 
from supabase import create_client, Client
import hsvEngine
import imageSchedule

load_dotenv()

app = Flask(__name__)
CORS(app) 

# Initialize Supabase Client
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICEROLE_KEY")
)

# Helper Function: Fetch Previous Image Data
def get_prev_image_data(user_id: str) -> tuple[float, str]:
    try:
        response = (
            supabase.table("imageTable")
            .select("green_ratio, current_state")
            .eq("user_ID", user_id)          
            .order("created_at", desc=True) 
            .limit(1)                        
            .execute()
        )
        
        # Check if historical data exists for this user
        if response.data and len(response.data) > 0:
            latest = response.data[0]
            prev_green_ratio = latest.get("green_ratio", 0.15)
            prev_state = latest.get("current_state", "base")
            return prev_green_ratio, prev_state
        else:
            print("First reading or no previous data found. Defaulting baselines.")
            return 0.15, "base" # Return default baselines for new user

    except Exception as e:
        print(f"[SUPABASE ERROR] get_prev_image_data failed: {str(e)}")
        return 0.15, "base" # Safe fallback

# Helper Function: Insert Current Reading Record
def push_current_data(green_ratio: float, user_id: str, state: str, public_url: str):
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

@app.route('/upload', methods=['POST'])
def upload_image():
    file_bytes = request.data 
    if not file_bytes:
        print("[WIFI ERROR]: Received empty data packet from ESP32")
        return jsonify({"error": "Empty data packet"}), 400

    user_id = request.headers.get('X-User-ID', 'default_user')
    print(f"Processing upload for User_ID: {user_id}")

    # 1. Compute HSV Green Ratio for current frame
    green_ratio = hsvEngine.analyze_image_bytes(file_bytes)

    # 2. Fetch historical state & baseline
    prev_green_ratio, prev_state = get_prev_image_data(user_id)

    # 3. Compute new state transition
    state = hsvEngine.evalstate(green_ratio, prev_green_ratio, prev_state)
    print(f"New computed state: {state}")

    # 4. Calculate next dynamic sleep interval based on state
    if state[0] == "base":
        time_to_next_image = imageSchedule.get_base_schedule_sleep_seconds()
    elif state[0] == "dynamic":
        time_to_next_image = imageSchedule.get_dynamicstate_sleep_seconds()
    elif state[0] == "obstruction":
        time_to_next_image = imageSchedule.get_obstructionstate_sleep_seconds()
    else:
        time_to_next_image = 7200 # Fallback 2 hours

    print(f"Next scheduled sleep: {time_to_next_image} seconds")

    try:
        # 5. Upload image to Supabase Storage
        filename = f"{user_id}/{int(time.time())}_photo.jpg"
        bucket_name = 'imageAnalysisBucket'

        supabase.storage.from_(bucket_name).upload(
            path=filename,
            file=file_bytes,
            file_options={"content-type": "image/jpeg", "upsert": "true"}
        )

        public_url = supabase.storage.from_(bucket_name).get_public_url(filename)
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
        print(f"[PIPELINE ERROR] Upload pipeline failed: {str(e)}")
        # Safe fallback so ESP32 still gets a sleep time and doesn't loop continuously
        return jsonify({
            "status": "error",
            "message": str(e),
            "sleep_sec": 7200
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)