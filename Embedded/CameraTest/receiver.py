from flask import Flask, request
import os
import time

app = Flask(__name__)
SAVE_DIR = "uploaded_photos"

if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

@app.route('/upload', methods=['POST'])
def upload_image():
    # Grab raw binary image array out of the incoming HTTP request payload body
    file_bytes = request.data
    
    if not file_bytes:
        return "Empty data packet received", 400

    filename = f"wifi_photo_{int(time.time())}.jpg"
    filepath = os.path.join(SAVE_DIR, filename)

    # Save directly to your laptop hard drive
    with open(filepath, "wb") as f:
        f.write(file_bytes)

    print(f"[WIFI SUCCESS]: Compiled and saved {filename} ({len(file_bytes)} bytes)")
    return "Image written successfully!", 200

if __name__ == '__main__':
    # Runs the network backend to listen to all incoming traffic on your local Wi-Fi router
    app.run(host='0.0.0.0', port=5000, debug=False)