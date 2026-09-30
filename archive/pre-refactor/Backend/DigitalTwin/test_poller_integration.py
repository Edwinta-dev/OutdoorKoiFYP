"""End-to-end integration test for the poll cycle and event endpoints,
with Supabase mocked by an in-memory fake.

This is the test that would have caught wiring mistakes the unit tests
cannot see: a mistyped state_store function name, an argument that never
reaches the engine, an evaluation row that is never pushed, or a snapshot
that is not persisted after a mutation. It drives the real
poller._poll_user and the real app.py event handlers.

Run: python3 test_poller_integration.py
"""
import json
import sys
import types
import os
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------
# Stub external deps BEFORE importing server modules.
# ---------------------------------------------------------------
for name in ["supabase", "dotenv", "apscheduler",
             "apscheduler.schedulers", "apscheduler.schedulers.background"]:
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["apscheduler.schedulers.background"].BackgroundScheduler = object
os.environ.setdefault("SUPABASE_URL", "x")
os.environ.setdefault("SUPABASE_SERVICEROLE_KEY", "y")

import state_store  # noqa: E402

FAIL = []
USER = 455


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(label)


# ---------------------------------------------------------------
# In-memory fake for every state_store function the server calls.
# ---------------------------------------------------------------
class FakeStore:
    def __init__(self):
        self.snapshots = {}
        self.chem_evals = []
        self.evap_evals = []
        self.algae_evals = []
        self.camera_rows = []
        self.feeding_rows = []
        self.ratings = []
        self.now_offset_days = 0

    # --- snapshot persistence ---
    def load_engine_snapshot(self, user_id):
        raw = self.snapshots.get(user_id)
        # Round-trip through JSON exactly like Supabase JSONB would, so a
        # non-serialisable field fails here rather than in production.
        return json.loads(raw) if raw else None

    def save_engine_snapshot(self, user_id, snapshot):
        self.snapshots[user_id] = json.dumps(snapshot)

    # --- evaluation pushes ---
    def push_evaluation(self, user_id, d):
        self.chem_evals.append(d)

    def push_evaporation_evaluation(self, user_id, d):
        self.evap_evals.append(d)

    def push_algae_evaluation(self, user_id, d):
        self.algae_evals.append(d)

    def fetch_latest_evaluation(self, user_id):
        return self.chem_evals[-1] if self.chem_evals else None

    def fetch_latest_evaporation_evaluation(self, user_id):
        return self.evap_evals[-1] if self.evap_evals else None

    def fetch_latest_algae_evaluation(self, user_id):
        return self.algae_evals[-1] if self.algae_evals else None

    # --- config ---
    def fetch_pond_config(self, user_id):
        return {"volume_litres": 5000.0, "estimated_biomass_grams": 8000.0}

    def fetch_active_pond_configs(self):
        return [{"user_id": USER, "volume_litres": 5000.0,
                 "estimated_biomass_grams": 8000.0}]

    # --- telemetry / history ---
    def fetch_dashboard_payload(self, user_id):
        return PAYLOAD

    def fetch_image_history(self, user_id, limit=200):
        return self.camera_rows[-limit:][::-1]  # newest first, as Supabase returns

    def fetch_recent_feeding_events(self, user_id, limit=30):
        return self.feeding_rows[:limit]

    def fetch_daily_sensor_stats(self, user_id, sensor_type, days=14):
        return {"avg": {"temp": 29.0, "TDS": 220.0, "LUX": 18000.0}.get(sensor_type, 20.0),
                "min": None, "max": None, "sample_days": 10}

    def fetch_daily_sensor_series(self, user_id, sensor_type, days=30):
        base = datetime(2026, 8, 1, tzinfo=timezone.utc)
        return [{"avg_value": 220.0 + i * 0.7, "min_value": None, "max_value": None,
                 "record_date": (base + timedelta(days=i)).isoformat()}
                for i in range(10)]

    def slope_per_day(self, rows, value_key="avg_value"):
        return 0.7

    def fetch_last_intervention_time(self, user_id, event_type):
        return None

    # --- algae severity ratings ---
    def insert_algae_rating(self, user_id, severity, is_obstructed, image_id,
                            image_url, green_ratio_at_rating,
                            image_captured_at=None, notes=None):
        self._rating_seq = getattr(self, "_rating_seq", 0) + 1
        row = {
            "id": self._rating_seq, "userid": user_id, "image_id": image_id,
            "image_url": image_url, "severity": severity,
            "is_obstructed": is_obstructed,
            "green_ratio_at_rating": green_ratio_at_rating,
            "image_captured_at": image_captured_at,
            "rated_at": datetime.now(timezone.utc).isoformat(), "notes": notes,
        }
        self.ratings.append(row)
        return row

    def fetch_algae_ratings(self, user_id, limit=200):
        return self.ratings[-limit:]

    def delete_algae_rating(self, user_id, rating_id):
        self.ratings = [r for r in self.ratings if r["id"] != rating_id]
        return True

    def fetch_image_by_id(self, user_id, image_id):
        for r in self.camera_rows:
            if r.get("id") == image_id:
                return r
        return None


PAYLOAD = {
    "raw_sensor": {"pH": 7.64, "TDS": 220, "temp": 29.2, "LUX": 22755},
    "nea_telemetry": {"air_temp": {"value": 30.8}, "rainfall": {"value": 0},
                      "wind_speed": {"value": 6.9}},
    "nea_forecasts": {
        "forecast_2hr": {"forecast": "Fair (Day)"},
        "forecast_24hr": {"general": {"relativeHumidity": {"low": 60, "high": 90},
                                      "temperature": {"low": 26, "high": 33}}},
        "outlook_4day": [
            {"data": {"day": "Wednesday", "wind": {"speed": {"low": 10, "high": 20}},
                      "forecast": {"code": "FA", "text": "Fair (Day)"},
                      "temperature": {"low": 27, "high": 34},
                      "relativeHumidity": {"low": 55, "high": 85}}},
            {"data": {"day": "Thursday", "wind": {"speed": {"low": 10, "high": 20}},
                      "forecast": {"code": "FA", "text": "Fair (Day)"},
                      "temperature": {"low": 27, "high": 34},
                      "relativeHumidity": {"low": 55, "high": 85}}},
        ],
    },
}

fake = FakeStore()
for fn in ["load_engine_snapshot", "save_engine_snapshot", "push_evaluation",
           "push_evaporation_evaluation", "push_algae_evaluation",
           "fetch_latest_evaluation", "fetch_latest_evaporation_evaluation",
           "fetch_latest_algae_evaluation", "fetch_pond_config",
           "fetch_active_pond_configs", "fetch_dashboard_payload",
           "fetch_image_history", "fetch_recent_feeding_events",
           "fetch_daily_sensor_stats", "fetch_daily_sensor_series",
           "slope_per_day", "fetch_last_intervention_time",
           "insert_algae_rating", "fetch_algae_ratings", "delete_algae_rating",
           "fetch_image_by_id"]:
    setattr(state_store, fn, getattr(fake, fn))

import poller  # noqa: E402
import app as flask_app  # noqa: E402
from registry import registry  # noqa: E402

# Seed history: 8 days of feeding, 5 days of camera frames.
base = datetime(2026, 8, 11, tzinfo=timezone.utc)
fake.feeding_rows = [
    {"food_grams": 150.0, "protein_percentage": 40.0,
     "event_timestamp": (base + timedelta(days=i)).isoformat()}
    for i in range(8)
][::-1]
fake.camera_rows = [
    {"id": i, "created_at": (base + timedelta(days=i)).isoformat(),
     "green_ratio": round(0.03 * (1.18 ** i), 5),
     "current_state": ["base", round(0.03 * (1.18 ** i), 5)],
     "imageURL": f"https://example/{i}.jpg"}
    for i in range(6)
]

# =====================================================================
print("=== 1. First poll cycle runs end to end ===")
poller._poll_user(USER, fake.fetch_active_pond_configs()[0])

check("chemistry evaluation pushed", len(fake.chem_evals) == 1, str(len(fake.chem_evals)))
check("evaporation evaluation pushed", len(fake.evap_evals) == 1, str(len(fake.evap_evals)))
check("algae evaluation pushed", len(fake.algae_evals) == 1, str(len(fake.algae_evals)))
check("snapshot persisted", USER in fake.snapshots)

snap = json.loads(fake.snapshots[USER])
check("snapshot holds all three engines",
      all(k in snap for k in ("chemistry", "evaporation", "algae")),
      str(sorted(snap.keys())))

ev_row = fake.evap_evals[-1]
al_row = fake.algae_evals[-1]
print(f"  chem  : {fake.chem_evals[-1]['status']}/{fake.chem_evals[-1]['category']}")
print(f"  evap  : {ev_row['status']}/{ev_row['category']} "
      f"loss={ev_row['loss_litres']}L feed_cap={ev_row['feed_cap_grams']}g "
      f"days_to_topup={ev_row['days_to_topup']}")
print(f"  algae : {al_row['status']}/{al_row['category']} "
      f"green={al_row['green_ratio']} src={al_row['rate_source']} "
      f"days_to_scrub={al_row['days_to_scrub']}")

check("algae grounded on camera, not fallback",
      al_row["rate_source"] == "fitted_from_camera", al_row["rate_source"])
check("algae assimilated the seeded frames", al_row["sample_count"] == 6,
      str(al_row["sample_count"]))
check("evaporation reports a feed cap", ev_row["feed_cap_grams"] > 0)
check("evaporation projected a top-up date",
      ev_row["days_to_topup"] is not None, str(ev_row["days_to_topup"]))
check("algae projected a scrub date",
      al_row["days_to_scrub"] is not None, str(al_row["days_to_scrub"]))

# =====================================================================
print("\n=== 2. Second cycle advances state (not a fresh start) ===")
registry.evict(USER)  # force a reload from the persisted snapshot
poller._poll_user(USER, fake.fetch_active_pond_configs()[0])
check("second chemistry evaluation pushed", len(fake.chem_evals) == 2)
check("second evaporation evaluation pushed", len(fake.evap_evals) == 2)
al2 = fake.algae_evals[-1]
check("no duplicate camera assimilation after reload",
      al2["sample_count"] == 6, str(al2["sample_count"]))

# =====================================================================
print("\n=== 3. Environmental change moves the evaporation state ===")
before_loss = fake.evap_evals[-1]["loss_litres"]
# Advance wall clock by pushing the engine's last-advance time backwards,
# which is equivalent to a day elapsing between polls.
def rewind(user_id, days):
    def fn(twin):
        twin.evaporation._last_advance_time -= timedelta(days=days)
        twin.algae._last_advance_time -= timedelta(days=days)
        twin.chemistry._last_ingest_time -= timedelta(days=days)
        return None
    registry.with_twin(user_id, fn)

for _ in range(6):
    rewind(USER, 1)
    poller._poll_user(USER, fake.fetch_active_pond_configs()[0])

after_loss = fake.evap_evals[-1]["loss_litres"]
print(f"  loss {before_loss} L -> {after_loss} L over 6 simulated days")
check("evaporation loss grew across polls", after_loss > before_loss,
      f"{before_loss} -> {after_loss}")
check("loss stays physically plausible", after_loss < 500, f"{after_loss} L")

# =====================================================================
print("\n=== 4. Logging a TOP_UP resets state via the real endpoint ===")
flask_app.app.config["TESTING"] = True
client = flask_app.app.test_client()

loss_before = fake.evap_evals[-1]["loss_litres"]
resp = client.post("/events/top-up", json={"user_id": USER, "volume_percent": 25.0})
check("top-up endpoint returns 200", resp.status_code == 200, str(resp.status_code))
body = resp.get_json()
check("response carries all three domains",
      all(k in body for k in ("chemistry", "evaporation", "algae")),
      str(sorted(body.keys())))
print(f"  loss {loss_before} L -> {body['evaporation']['loss_litres']} L")
check("top-up cleared accrued evaporation loss",
      body["evaporation"]["loss_litres"] == 0.0,
      str(body["evaporation"]["loss_litres"]))
check("evaporation status back to Green",
      body["evaporation"]["status"] == "Green", body["evaporation"]["status"])
check("a fresh evaporation row was pushed immediately",
      fake.evap_evals[-1]["loss_litres"] == 0.0)
check("reset persisted to the snapshot",
      json.loads(fake.snapshots[USER])["evaporation"]["cumulative_loss_litres"] == 0.0)

# =====================================================================
print("\n=== 5. Logging an ALGAE_SCRUB resets algae via the endpoint ===")
green_before = fake.algae_evals[-1]["green_ratio"]
resp = client.post("/events/algal-scrub",
                   json={"user_id": USER, "scrub_type": "Manual Scrub"})
check("scrub endpoint returns 200", resp.status_code == 200, str(resp.status_code))
body = resp.get_json()
green_after = body["algae"]["green_ratio"]
print(f"  green {green_before} -> {green_after}")
check("scrub knocked the green level down", green_after < green_before,
      f"{green_before} -> {green_after}")
# The assessment rounds green_ratio to 5 dp for transport; the snapshot
# keeps full precision. Compare at the transported precision.
persisted_green = json.loads(fake.snapshots[USER])["algae"]["green_ratio"]
print(f"  persisted {persisted_green!r} vs transported {green_after!r}")
check("scrub reset persisted",
      abs(persisted_green - green_after) < 1e-5,
      f"persisted={persisted_green} transported={green_after}")

# =====================================================================
print("\n=== 6. Cached assessment endpoints serve the pushed rows ===")
for path, label in [(f"/assessment/{USER}", "chemistry"),
                    (f"/assessment/evaporation/{USER}", "evaporation"),
                    (f"/assessment/algae/{USER}", "algae")]:
    r = client.get(path)
    check(f"GET {path} -> 200", r.status_code == 200, str(r.status_code))

r = client.get(f"/assessment/all/{USER}")
allbody = r.get_json()
check("GET /assessment/all returns all three domains",
      all(allbody.get(k) is not None for k in ("chemistry", "evaporation", "algae")),
      str({k: (v is not None) for k, v in allbody.items()}))

# =====================================================================
print("\n=== 7. Forecast endpoints compute from live engine state ===")
r = client.get(f"/forecast/evaporation/{USER}?horizon_days=14")
check("GET /forecast/evaporation -> 200", r.status_code == 200, str(r.status_code))
fe = r.get_json()
check("evaporation forecast has a trajectory", len(fe.get("trajectory", [])) == 14,
      str(len(fe.get("trajectory", []))))
check("forecast starts from the post-top-up state (zero loss)",
      fe["starting_loss_litres"] == 0.0, str(fe["starting_loss_litres"]))
check("TDS cross-check present", "tds_cross_check" in fe)
print(f"  tds cross-check: {fe['tds_cross_check']['verdict']}")

r = client.get(f"/forecast/algae/{USER}?horizon_days=21")
check("GET /forecast/algae -> 200", r.status_code == 200, str(r.status_code))
fa = r.get_json()
check("algae forecast has a trajectory", len(fa.get("trajectory", [])) == 21,
      str(len(fa.get("trajectory", []))))
check("scrub benefit present", "scrub_benefit" in fa)
check("latest image url surfaced", fa.get("latest_image_url") is not None)
print(f"  predicted scrub in {fa.get('predicted_scrub_days_from_now')} days; "
      f"scrubbing now buys {fa['scrub_benefit'].get('days_bought')} days")

r = client.get(f"/forecast/{USER}?horizon_days=21")
check("GET /forecast (chemistry) -> 200", r.status_code == 200, str(r.status_code))

# Read-only forecasts must not have changed persisted state.
snap_before = fake.snapshots[USER]
client.get(f"/forecast/evaporation/{USER}")
client.get(f"/forecast/{USER}")
check("evaporation/chemistry forecasts did not rewrite the snapshot",
      fake.snapshots[USER] == snap_before)

# =====================================================================
print("\n=== 8. New camera frame corrects the model through the poller ===")
last_t = datetime.fromisoformat(fake.camera_rows[-1]["created_at"])
modelled = json.loads(fake.snapshots[USER])["algae"]["green_ratio"]
fake.camera_rows.append({
    "id": 99, "created_at": (last_t + timedelta(days=7)).isoformat(),
    "green_ratio": 0.004, "current_state": ["base", 0.004],
    "imageURL": "https://example/99.jpg",
})
rewind(USER, 1)
poller._poll_user(USER, fake.fetch_active_pond_configs()[0])
corrected = json.loads(fake.snapshots[USER])["algae"]["green_ratio"]
print(f"  modelled {modelled:.5f} -> camera 0.004 -> corrected {corrected:.5f}")
check("new frame pulled the model toward the measurement", corrected < modelled)
check("sample count incremented", fake.algae_evals[-1]["sample_count"] == 7,
      str(fake.algae_evals[-1]["sample_count"]))

# =====================================================================
print("\n=== 9. Validation rejects bad event bodies ===")
r = client.post("/events/top-up", json={"user_id": USER})
check("top-up without a volume -> 400", r.status_code == 400, str(r.status_code))
r = client.post("/events/feeding", json={"user_id": USER, "food_grams": 100})
check("feeding without protein -> 400", r.status_code == 400, str(r.status_code))
r = client.post("/events/feeding",
                json={"user_id": USER, "food_grams": 100, "protein_percent": 400})
check("feeding with impossible protein % -> 400", r.status_code == 400, str(r.status_code))
r = client.post("/events/feeding",
                json={"user_id": USER, "food_grams": 150, "protein_percent": 40,
                      "timestamp": "not-a-timestamp"})
check("malformed timestamp falls back to server time instead of 500",
      r.status_code == 200, str(r.status_code))

# =====================================================================
print("\n=== 10. Whole poll loop survives one broken user ===")
original = fake.fetch_dashboard_payload
fake.fetch_dashboard_payload = lambda uid: None
state_store.fetch_dashboard_payload = fake.fetch_dashboard_payload
try:
    poller._poll_once()   # must not raise
    check("poll loop tolerates a user with no payload", True)
except Exception as exc:  # noqa: BLE001
    check("poll loop tolerates a user with no payload", False, str(exc))
fake.fetch_dashboard_payload = original
state_store.fetch_dashboard_payload = original

# =====================================================================
print("\n=== 11. Severity rating endpoint corrects the model ===")
before = json.loads(fake.snapshots[USER])["algae"]["green_ratio"]
latest_img = fake.camera_rows[-1]
r = client.post("/events/algae-rating", json={
    "user_id": USER, "severity": "none",
    "image_id": latest_img["id"], "green_ratio": latest_img["green_ratio"],
})
check("rating endpoint returns 200", r.status_code == 200, str(r.status_code))
body = r.get_json()
after = body["green_ratio_after"]
print(f"  green {before:.5f} -> {after:.5f} after rating 'none'")
check("rating moved the modelled level", abs(after - before) > 1e-9)
check("rating persisted a row", len(fake.ratings) == 1, str(len(fake.ratings)))
check("row pins the rated frame",
      fake.ratings[0]["image_id"] == latest_img["id"], str(fake.ratings[0]["image_id"]))
check("row stores the paired measurement",
      fake.ratings[0]["green_ratio_at_rating"] == latest_img["green_ratio"])
check("response carries all three assessments",
      all(k in body["assessments"] for k in ("chemistry", "evaporation", "algae")))
check("engine state persisted after rating",
      abs(json.loads(fake.snapshots[USER])["algae"]["green_ratio"] - after) < 1e-5)
check("no spurious warning when the table exists", body.get("warning") is None)

print("\n=== 12. Undo restores through the endpoint ===")
rating_id = body["rating_id"]
r = client.post("/events/algae-rating/undo",
                json={"user_id": USER, "rating_id": rating_id})
check("undo returns 200", r.status_code == 200, str(r.status_code))
restored = json.loads(fake.snapshots[USER])["algae"]["green_ratio"]
print(f"  restored to {restored:.5f} (was {before:.5f})")
check("level restored to pre-rating value", abs(restored - before) < 1e-9,
      f"{restored} vs {before}")
check("rating row deleted", len(fake.ratings) == 0, str(len(fake.ratings)))
r = client.post("/events/algae-rating/undo", json={"user_id": USER})
check("second undo is refused with 409", r.status_code == 409, str(r.status_code))

print("\n=== 13. Obstruction rating discards the frame ===")
bad_t = datetime.fromisoformat(fake.camera_rows[-1]["created_at"]) + timedelta(days=1)
fake.camera_rows.append({
    "id": 500, "created_at": bad_t.isoformat(), "green_ratio": 0.72,
    "current_state": ["base", 0.72], "imageURL": "https://example/leaf.jpg",
})
rewind(USER, 1)
poller._poll_user(USER, fake.fetch_active_pond_configs()[0])
poisoned = json.loads(fake.snapshots[USER])["algae"]["green_ratio"]
print(f"  poisoned to {poisoned:.5f} by the 0.72 frame")
r = client.post("/events/algae-rating", json={
    "user_id": USER, "severity": "obstruction",
    "image_id": 500, "green_ratio": 0.72,
})
check("obstruction rating returns 200", r.status_code == 200, str(r.status_code))
ob = r.get_json()
check("a frame was removed", ob["removed_frames"] == 1, str(ob["removed_frames"]))
cleaned = json.loads(fake.snapshots[USER])["algae"]["green_ratio"]
print(f"  cleaned to {cleaned:.5f}")
check("level rolled back off the obstructed frame", cleaned < poisoned)
check("obstruction row stores severity NULL",
      fake.ratings[-1]["severity"] is None and fake.ratings[-1]["is_obstructed"] is True)

print("\n=== 14. Rating context endpoint feeds the card ===")
r = client.get(f"/ratings/algae/{USER}")
check("GET /ratings/algae -> 200", r.status_code == 200, str(r.status_code))
ctxb = r.get_json()
for key in ("ratings", "latest_rating", "calibration", "latest_image", "severity_levels"):
    check(f"context exposes {key}", key in ctxb)
check("severity_levels lists all five options",
      len(ctxb["severity_levels"]) == 5, str(ctxb["severity_levels"]))
cal = ctxb["calibration"]
for key in ("label_counts", "labels_needed", "thresholds", "camera_drift", "class_targets"):
    check(f"calibration exposes {key}", key in cal)
check("latest_image carries an id for pinning",
      ctxb["latest_image"] is not None and "id" in ctxb["latest_image"])

print("\n=== 15. Calibration reached after enough labels ===")
# Feed two of each class, pinned to real frames.
pairs = [("none", 0.004), ("none", 0.005), ("minor", 0.03), ("minor", 0.033),
         ("moderate", 0.09), ("moderate", 0.095), ("severe", 0.25), ("severe", 0.27)]
for i, (sev, g) in enumerate(pairs):
    iid = 900 + i
    fake.camera_rows.append({
        "id": iid,
        "created_at": (bad_t + timedelta(days=2 + i)).isoformat(),
        "green_ratio": g, "current_state": ["base", g],
        "imageURL": f"https://example/c{i}.jpg",
    })
    rr = client.post("/events/algae-rating", json={
        "user_id": USER, "severity": sev, "image_id": iid, "green_ratio": g,
    })
    assert rr.status_code == 200, rr.get_json()

cal = client.get(f"/ratings/algae/{USER}").get_json()["calibration"]
print(f"  mode={cal['thresholds']['mode']} watch={cal['thresholds']['watch']:.4f} "
      f"action={cal['thresholds']['action']:.4f}")
print(f"  counts={cal['label_counts']}")
check("thresholds became label-calibrated",
      cal["thresholds"]["mode"] == "label_calibrated", cal["thresholds"]["mode"])
check("no classes still needed",
      sum(cal["labels_needed"].values()) == 0, str(cal["labels_needed"]))
check("watch below action", cal["thresholds"]["watch"] < cal["thresholds"]["action"])
# minor median 0.0315, moderate median 0.0925 -> watch ~0.062
check("watch sits between minor and moderate medians",
      abs(cal["thresholds"]["watch"] - (0.0315 + 0.0925) / 2) < 0.01,
      str(round(cal["thresholds"]["watch"], 4)))

algae_row = fake.algae_evals[-1]
check("evaluation row reports the label count",
      algae_row["label_count"] >= 8, str(algae_row.get("label_count")))

print("\n=== 16. Calibration survives a registry eviction ===")
registry.evict(USER)
cal2 = client.get(f"/ratings/algae/{USER}").get_json()["calibration"]
check("thresholds reload from the ratings table",
      cal2["thresholds"]["mode"] == "label_calibrated", cal2["thresholds"]["mode"])
check("label counts reload",
      cal2["label_counts"] == cal["label_counts"], str(cal2["label_counts"]))

print("\n=== 17. Bad rating bodies rejected ===")
r = client.post("/events/algae-rating", json={"user_id": USER, "severity": "apocalyptic"})
check("invalid severity -> 400", r.status_code == 400, str(r.status_code))
r = client.post("/events/algae-rating", json={"user_id": USER})
check("missing severity -> 400", r.status_code == 400, str(r.status_code))

print("\n" + "=" * 62)
if FAIL:
    print(f"{len(FAIL)} CHECK(S) FAILED:")
    for f in FAIL:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL INTEGRATION CHECKS PASSED")
