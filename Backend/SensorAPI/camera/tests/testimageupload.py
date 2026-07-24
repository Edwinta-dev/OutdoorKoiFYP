import os
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_PATH = os.path.join(BASE_DIR, "image.jpg")

API_URL = os.getenv("TEST_API_URL", "http://127.0.0.1:5000/upload")

def test_image_upload():
    if not os.path.exists(IMAGE_PATH):
        print(f"[FAIL] Test image not found at {IMAGE_PATH}")
        return

    headers = {"Content-Type": "image/jpeg"}

    with open(IMAGE_PATH, "rb") as img_file:
        raw_data = img_file.read()
        response = requests.post(API_URL, headers=headers, data=raw_data)

    print(f"Status Code: {response.status_code}")
    print(f"Response Body: {response.text}")
    
    assert response.status_code == 200, "Upload endpoint test failed!"

if __name__ == "__main__":
    test_image_upload()