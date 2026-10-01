"""Camera service GET /metrics and logging: uploads, analysis time and
state transitions (issue #10)."""
import io
import json
import logging
import re

from test_camera_app import _jpeg

from conftest import make_settings
from koi.camera import create_app
from koi.logs import configure_logging
from koi.storage import MemoryStorage


def _value(text, sample):
    match = re.search(rf"^{re.escape(sample)} (\S+)$", text, re.M)
    assert match, f"{sample} not in:\n{text}"
    return float(match.group(1))


def test_camera_metrics_count_uploads_analysis_time_and_transitions():
    storage = MemoryStorage()
    storage.add_rows("imageTable", [{"user_ID": 15, "green_ratio": 0.2,
                                     "current_state": ["obstruction", 0.2], "imageURL": "x"}])
    client = create_app(make_settings(test_mode=True), storage=storage).test_client()
    resp = client.post("/upload", data=_jpeg((0, 200, 0)), headers={"X-User-ID": "15"})
    assert resp.status_code == 200
    new_state = resp.get_json()["state"][0]
    assert client.post("/upload", data=b"").status_code == 400
    assert client.post("/upload", data=b"not a jpeg").status_code == 422

    text = client.get("/metrics").get_data(as_text=True)
    assert _value(text, 'koi_camera_uploads_total{result="stored"}') == 1
    assert _value(text, 'koi_camera_uploads_total{result="empty_body"}') == 1
    assert _value(text, 'koi_camera_uploads_total{result="image_unreadable"}') == 1
    assert _value(text, "koi_camera_analysis_seconds_count") == 1
    assert _value(text, "koi_camera_analysis_seconds_sum") > 0
    assert _value(text, f'koi_camera_state_transitions_total{{from_state="obstruction",to_state="{new_state}"}}') == 1
    assert _value(text, 'koi_http_requests_total{service="camera",route="/upload",method="POST",status="200"}') == 1


def test_camera_logging_tags_lines_with_the_pond_and_request():
    client = create_app(make_settings(test_mode=True), storage=MemoryStorage()).test_client()
    buf = io.StringIO()
    handler = configure_logging(make_settings(), "camera", stream=buf)
    try:
        client.post("/upload", data=_jpeg((0, 200, 0)), headers={"X-User-ID": "15", "X-Request-ID": "cam-1"})
    finally:
        logging.getLogger().removeHandler(handler)
    lines = [json.loads(line) for line in buf.getvalue().splitlines()]
    events = {e["event"]: e for e in lines}
    assert {"frame_analysed", "frame_stored", "request"} <= set(events)
    for event in ("frame_analysed", "frame_stored", "request"):
        assert events[event]["pond_id"] == "15" and events[event]["request_id"] == "cam-1"
        assert events[event]["service"] == "camera"
    assert events["frame_analysed"]["analysis_ms"] >= 0
