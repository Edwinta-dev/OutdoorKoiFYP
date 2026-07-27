from flask import Flask, request
import os
import time
from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS 
from supabase import create_client, Client
import hsvEngine
import imageSchedule

load_dotenv()

app = Flask(__name__)
CORS(app)   



# Initialize Supabase
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICEROLE_KEY")
)

@app.route('/upload', methods=['POST'])
def upload_image():
    file_bytes = request.data 
    if not file_bytes:
        print("[WIFI ERROR]: Received empty data packet from ESP32")
        return "Empty data packet received", 400

    filename = f"wifi_photo_{int(time.time())}.jpg"
    bucket_name = 'imageAnalysisBucket'
    state = hsvEngine.getstate()
    print(f"State is: {state}")
    state = hsvEngine.evalstate()
    if state == "base":
        time_to_next_image = imageSchedule.get_base_schedule_sleep_seconds()
    elif state == "dynamic":
        time_to_next_image = imageSchedule.get_dynamicstate_sleep_seconds()
    elif state == "obstruction":
        time_to_next_image = imageSchedule.get_obstructionstate_sleep_seconds()
    print(f"Time to next image: {time_to_next_image}")
    try:
        # 2. Upload file bytes to Supabase Storage with explicit MIME type
        response = supabase.storage.from_(bucket_name).upload(
            path=filename,
            file=file_bytes,
            file_options={"content-type": "image/jpeg", "upsert": "true"}
        )
        
        print(f"Uploaded {filename} ({len(file_bytes)} bytes)")        
        # Http Response
        return jsonify({
            "status": "success",
            "sleep_sec": time_to_next_image
        }), 200

    except Exception as e:
        print(f"Supabase upload failed: {str(e)}")
        return f"Upload failed: {str(e)}", 500



if __name__ == '__main__':
    # Runs the network backend to listen to all incoming traffic on your local Wi-Fi router
    app.run(host='0.0.0.0', port=5000, debug=False)
