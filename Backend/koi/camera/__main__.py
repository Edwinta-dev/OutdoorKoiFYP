"""python -m koi.camera: runs the camera service on port 5000."""
from koi.camera import create_app

if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, debug=False)
