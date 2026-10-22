import hashlib, sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
SRC = _common.WP1.parent / "src"
files = sorted(SRC.glob("fwi/csv/*.csv")) + [SRC/"oman/data/raw/offline/raw_readings.csv", SRC/"oman/data/raw/live/raw_readings_new.csv",
        SRC/"oman/data/events/event_logs_new.csv", SRC/"idn_t/pond_iot_2023.csv", SRC/"idn_t/pond_iot_2023_raw.csv"]
out = {}
for f in files:
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    out[str(f.relative_to(SRC)).replace("\\", "/")] = h.hexdigest()
(_common.WP1 / "out" / "source_sha256.json").write_text(json.dumps(out, indent=1))
print(len(out))
