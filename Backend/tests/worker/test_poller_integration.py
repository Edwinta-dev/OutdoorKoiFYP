"""End-to-end integration test for the poll cycle and event endpoints,
against the MemoryStorage fixture pond from conftest.py (no patching:
the poller and the API share the app's own storage and registry).

This is the test that would have caught wiring mistakes the unit tests
cannot see: a mistyped storage method name, an argument that never
reaches the engine, an evaluation row that is never pushed, or a snapshot
that is not persisted after a mutation. It drives the real
poller._poll_user and the real event handlers.

The tests walk one pond through a sequence of polls and events, so they
share module-scoped state and must run in file order (pytest's default).
Run from Backend/: python -m pytest tests/worker/test_poller_integration.py
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, api_client
from koi.worker import poller


@pytest.fixture(scope="module")
def store(pond_app):
    """The fixture pond's storage, plus 8 days of feeding and 6 camera frames."""
    storage = pond_app.extensions["koi_storage"]
    base = datetime(2026, 8, 11, tzinfo=timezone.utc)
    storage.add_rows("pondInterventions", [
        {"userID": USER, "event_type": "FEEDING", "food_grams": 150.0, "protein_percentage": 40.0,
         "event_timestamp": (base + timedelta(days=i)).isoformat()}
        for i in range(8)
    ])
    add_frames(storage, [
        {"id": i, "created_at": (base + timedelta(days=i)).isoformat(),
         "green_ratio": round(0.03 * (1.18 ** i), 5),
         "current_state": ["base", round(0.03 * (1.18 ** i), 5)],
         "imageURL": f"https://example/{i}.jpg"}
        for i in range(6)
    ])
    return storage


@pytest.fixture(scope="module")
def registry(pond_app):
    return pond_app.extensions["koi_registry"]


@pytest.fixture(scope="module")
def client(store, pond_app):
    return api_client(pond_app)


@pytest.fixture(scope="module")
def carried():
    """Values one section records for a later section to compare against."""
    return {}


def add_frames(storage, frames):
    storage.add_rows("imageTable", [{"user_ID": USER, **f} for f in frames])


def frames(store):
    return store.rows("imageTable")


def chem_evals(store):
    return store.rows("pond_chemistry_evaluations")


def evap_evals(store):
    return store.rows("pond_evaporation_evaluations")


def algae_evals(store):
    return store.rows("pond_algae_evaluations")


def ratings(store):
    return store.rows("algae_severity_ratings")


def snapshot(store):
    return store.load_engine_snapshot(USER)


def poll(store, registry):
    poller._poll_user(registry, USER, store.fetch_active_pond_configs()[0])


def persisted_green(store):
    return snapshot(store)["algae"]["green_ratio"]


def rewind(registry, user_id, days):
    """Pushes the engines' last-advance times backwards, which is
    equivalent to that many days elapsing between polls."""
    def fn(twin):
        twin.evaporation._last_advance_time -= timedelta(days=days)
        twin.algae._last_advance_time -= timedelta(days=days)
        twin.chemistry._last_ingest_time -= timedelta(days=days)
        return None
    registry.with_twin(user_id, fn)


def test_first_poll_cycle_runs_end_to_end(store, registry):
    poll(store, registry)

    assert len(chem_evals(store)) == 1, f"chemistry evaluation pushed: {len(chem_evals(store))}"
    assert len(evap_evals(store)) == 1, f"evaporation evaluation pushed: {len(evap_evals(store))}"
    assert len(algae_evals(store)) == 1, f"algae evaluation pushed: {len(algae_evals(store))}"
    snap = snapshot(store)
    assert snap is not None, "snapshot persisted"
    assert all(k in snap for k in ("chemistry", "evaporation", "algae")), \
        f"snapshot holds all three engines: {sorted(snap.keys())}"

    ev_row = evap_evals(store)[-1]
    al_row = algae_evals(store)[-1]
    assert al_row["rate_source"] == "fitted_from_camera", \
        f"algae grounded on camera, not fallback: {al_row['rate_source']}"
    assert al_row["sample_count"] == 6, \
        f"algae assimilated the seeded frames: {al_row['sample_count']}"
    assert ev_row["feed_cap_grams"] > 0, "evaporation reports a feed cap"
    assert ev_row["days_to_topup"] is not None, \
        f"evaporation projected a top-up date: {ev_row['days_to_topup']}"
    assert al_row["days_to_scrub"] is not None, \
        f"algae projected a scrub date: {al_row['days_to_scrub']}"


def test_second_cycle_advances_state_after_reload(store, registry):
    registry.evict(USER)  # force a reload from the persisted snapshot
    poll(store, registry)
    assert len(chem_evals(store)) == 2, "second chemistry evaluation pushed"
    assert len(evap_evals(store)) == 2, "second evaporation evaluation pushed"
    al2 = algae_evals(store)[-1]
    assert al2["sample_count"] == 6, \
        f"no duplicate camera assimilation after reload: {al2['sample_count']}"


def test_environmental_change_moves_evaporation_state(store, registry):
    before_loss = evap_evals(store)[-1]["loss_litres"]
    for _ in range(6):
        rewind(registry, USER, 1)
        poll(store, registry)

    after_loss = evap_evals(store)[-1]["loss_litres"]
    assert after_loss > before_loss, \
        f"evaporation loss grew across polls: {before_loss} -> {after_loss}"
    assert after_loss < 500, f"loss stays physically plausible: {after_loss} L"


def test_top_up_endpoint_resets_evaporation(store, client):
    resp = client.post(f"/v1/ponds/{USER}/events/top-up", json={"user_id": USER, "volume_percent": 25.0})
    assert resp.status_code == 200, f"top-up endpoint returns 200: {resp.status_code}"
    body = resp.get_json()
    assert all(k in body for k in ("chemistry", "evaporation", "algae")), \
        f"response carries all three domains: {sorted(body.keys())}"
    assert body["evaporation"]["loss_litres"] == 0.0, \
        f"top-up cleared accrued evaporation loss: {body['evaporation']['loss_litres']}"
    assert body["evaporation"]["status"] == "Green", \
        f"evaporation status back to Green: {body['evaporation']['status']}"
    assert evap_evals(store)[-1]["loss_litres"] == 0.0, \
        "a fresh evaporation row was pushed immediately"
    assert snapshot(store)["evaporation"]["cumulative_loss_litres"] == 0.0, \
        "reset persisted to the snapshot"


def test_algal_scrub_endpoint_resets_algae(store, client):
    green_before = algae_evals(store)[-1]["green_ratio"]
    resp = client.post(f"/v1/ponds/{USER}/events/algal-scrub",
                       json={"user_id": USER, "scrub_type": "Manual Scrub"})
    assert resp.status_code == 200, f"scrub endpoint returns 200: {resp.status_code}"
    green_after = resp.get_json()["algae"]["green_ratio"]
    assert green_after < green_before, \
        f"scrub knocked the green level down: {green_before} -> {green_after}"
    # The assessment rounds green_ratio to 5 dp for transport; the snapshot
    # keeps full precision. Compare at the transported precision.
    persisted = persisted_green(store)
    assert abs(persisted - green_after) < 1e-5, \
        f"scrub reset persisted: persisted={persisted} transported={green_after}"


def test_cached_assessment_endpoints_serve_pushed_rows(client):
    for path in [f"/v1/ponds/{USER}/assessments/chemistry", f"/v1/ponds/{USER}/assessments/evaporation",
                 f"/v1/ponds/{USER}/assessments/algae"]:
        r = client.get(path)
        assert r.status_code == 200, f"GET {path} -> 200: {r.status_code}"

    allbody = client.get(f"/v1/ponds/{USER}/assessments").get_json()
    assert all(allbody.get(k) is not None for k in ("chemistry", "evaporation", "algae")), \
        f"GET /assessment/all returns all three domains: " \
        f"{ {k: (v is not None) for k, v in allbody.items()} }"


def test_forecast_endpoints_compute_from_live_state(store, client):
    r = client.get(f"/v1/ponds/{USER}/forecasts/evaporation?horizon_days=14")
    assert r.status_code == 200, f"GET /forecast/evaporation -> 200: {r.status_code}"
    fe = r.get_json()
    assert len(fe.get("trajectory", [])) == 14, \
        f"evaporation forecast has a trajectory: {len(fe.get('trajectory', []))}"
    assert fe["starting_loss_litres"] == 0.0, \
        f"forecast starts from the post-top-up state (zero loss): {fe['starting_loss_litres']}"
    assert "tds_cross_check" in fe, "TDS cross-check present"

    r = client.get(f"/v1/ponds/{USER}/forecasts/algae?horizon_days=21")
    assert r.status_code == 200, f"GET /forecast/algae -> 200: {r.status_code}"
    fa = r.get_json()
    assert len(fa.get("trajectory", [])) == 21, \
        f"algae forecast has a trajectory: {len(fa.get('trajectory', []))}"
    assert "scrub_benefit" in fa, "scrub benefit present"
    assert fa.get("latest_image_url") is not None, "latest image url surfaced"

    r = client.get(f"/v1/ponds/{USER}/forecasts/chemistry?horizon_days=21")
    assert r.status_code == 200, f"GET /forecast (chemistry) -> 200: {r.status_code}"

    # Read-only forecasts must not have changed persisted state.
    snap_before = store.rows("pond_chemistry_state")
    client.get(f"/v1/ponds/{USER}/forecasts/evaporation")
    client.get(f"/v1/ponds/{USER}/forecasts/chemistry")
    assert store.rows("pond_chemistry_state") == snap_before, \
        "evaporation/chemistry forecasts did not rewrite the snapshot"


def test_new_camera_frame_corrects_model_through_poller(store, registry):
    # `pytest -k camera` selects this test without the earlier ones, so
    # run the first poll here when no snapshot has been persisted yet.
    if snapshot(store) is None:
        poll(store, registry)
    last_t = datetime.fromisoformat(frames(store)[-1]["created_at"])
    modelled = persisted_green(store)
    add_frames(store, [{
        "id": 99, "created_at": (last_t + timedelta(days=7)).isoformat(),
        "green_ratio": 0.004, "current_state": ["base", 0.004],
        "imageURL": "https://example/99.jpg",
    }])
    rewind(registry, USER, 1)
    poll(store, registry)
    corrected = persisted_green(store)
    assert corrected < modelled, "new frame pulled the model toward the measurement"
    assert algae_evals(store)[-1]["sample_count"] == 7, \
        f"sample count incremented: {algae_evals(store)[-1]['sample_count']}"


def test_validation_rejects_bad_event_bodies(client):
    r = client.post(f"/v1/ponds/{USER}/events/top-up", json={"user_id": USER})
    assert r.status_code == 400, f"top-up without a volume -> 400: {r.status_code}"
    r = client.post(f"/v1/ponds/{USER}/events/feeding", json={"user_id": USER, "food_grams": 100})
    assert r.status_code == 400, f"feeding without protein -> 400: {r.status_code}"
    r = client.post(f"/v1/ponds/{USER}/events/feeding",
                    json={"user_id": USER, "food_grams": 100, "protein_percent": 400})
    assert r.status_code == 400, f"feeding with impossible protein % -> 400: {r.status_code}"
    r = client.post(f"/v1/ponds/{USER}/events/feeding",
                    json={"user_id": USER, "food_grams": 150, "protein_percent": 40,
                          "timestamp": "not-a-timestamp"})
    assert r.status_code == 400, f"malformed timestamp is rejected, not replaced: {r.status_code}"
    assert r.get_json()["error"]["details"]["fields"][0]["field"] == "timestamp"


def test_poll_loop_survives_one_broken_user(store, registry):
    pushed = len(chem_evals(store))
    store.failing.add("fetch_pending_sensor_rows")
    try:
        poller._poll_once(registry)   # must not raise
    finally:
        store.failing.discard("fetch_pending_sensor_rows")
    assert len(chem_evals(store)) == pushed, "a user whose sensor rows cannot be read is not advanced"


def test_poll_loop_survives_a_storage_failure(store, registry):
    pushed = len(chem_evals(store))
    store.failing.add("fetch_dashboard_payload")
    try:
        poller._poll_once(registry)   # must not raise
    finally:
        store.failing.discard("fetch_dashboard_payload")
    assert len(chem_evals(store)) == pushed, "a user whose payload read fails is skipped"


def test_optional_evaluation_logs_do_not_stop_a_poll(store, registry):
    """The evaporation and algae logs are fail-soft; the chemistry row and
    the snapshot are not optional and still land."""
    optional = {"push_evaporation_evaluation", "push_algae_evaluation"}
    before = (len(chem_evals(store)), len(evap_evals(store)), len(algae_evals(store)))
    snap_before = store.rows("pond_chemistry_state")
    store.failing |= optional
    try:
        rewind(registry, USER, 1)
        poll(store, registry)
    finally:
        store.failing -= optional
    after = (len(chem_evals(store)), len(evap_evals(store)), len(algae_evals(store)))
    assert after == (before[0] + 1, before[1], before[2]), f"only the chemistry row was pushed: {after}"
    assert store.rows("pond_chemistry_state") != snap_before, "snapshot still persisted"


def test_snapshot_write_failure_reaches_the_caller(store, client, registry):
    store.failing.add("save_engine_snapshot")
    try:
        r = client.post(f"/v1/ponds/{USER}/events/top-up", json={"user_id": USER, "volume_percent": 5.0})
        assert r.status_code == 503, f"lost snapshot write -> 503: {r.status_code}"
        error = r.get_json()["error"]
        assert (error["code"], error["details"]["operation"]) == ("storage_unavailable", "save_engine_snapshot")
        assert r.headers["Retry-After"] == str(error["details"]["retry_after_sec"])
    finally:
        store.failing.discard("save_engine_snapshot")
        registry.evict(USER)  # drop the unsaved top-up; later tests start from the snapshot


def test_missing_evaluation_tables_read_as_no_assessment(store, client):
    """Reads the old state_store swallowed still come back empty, not 500."""
    store.failing.add("fetch_latest_algae_evaluation")
    try:
        r = client.get(f"/v1/ponds/{USER}/assessments/algae")
        allbody = client.get(f"/v1/ponds/{USER}/assessments").get_json()
    finally:
        store.failing.discard("fetch_latest_algae_evaluation")
    assert r.status_code == 404, f"failed algae read -> 404 no assessment: {r.status_code}"
    assert allbody["algae"] is None and allbody["chemistry"] is not None


def test_severity_rating_endpoint_corrects_the_model(store, client, carried):
    before = persisted_green(store)
    latest_img = frames(store)[-1]
    r = client.post(f"/v1/ponds/{USER}/events/algae-rating", json={
        "user_id": USER, "severity": "none",
        "image_id": latest_img["id"], "green_ratio": latest_img["green_ratio"],
    })
    assert r.status_code == 200, f"rating endpoint returns 200: {r.status_code}"
    body = r.get_json()
    after = body["green_ratio_after"]
    assert abs(after - before) > 1e-9, "rating moved the modelled level"
    assert len(ratings(store)) == 1, f"rating persisted a row: {len(ratings(store))}"
    assert ratings(store)[0]["image_id"] == latest_img["id"], \
        f"row pins the rated frame: {ratings(store)[0]['image_id']}"
    assert ratings(store)[0]["green_ratio_at_rating"] == latest_img["green_ratio"], \
        "row stores the paired measurement"
    assert all(k in body["assessments"] for k in ("chemistry", "evaporation", "algae")), \
        "response carries all three assessments"
    assert abs(persisted_green(store) - after) < 1e-5, "engine state persisted after rating"
    assert body.get("warning") is None, "no spurious warning when the table exists"
    carried["green_before_rating"] = before
    carried["rating_id"] = body["rating_id"]


def test_undo_restores_through_the_endpoint(store, client, carried):
    before = carried["green_before_rating"]
    r = client.post(f"/v1/ponds/{USER}/events/algae-rating/undo",
                    json={"user_id": USER, "rating_id": carried["rating_id"]})
    assert r.status_code == 200, f"undo returns 200: {r.status_code}"
    restored = persisted_green(store)
    assert abs(restored - before) < 1e-9, \
        f"level restored to pre-rating value: {restored} vs {before}"
    assert len(ratings(store)) == 0, f"rating row deleted: {len(ratings(store))}"
    r = client.post(f"/v1/ponds/{USER}/events/algae-rating/undo", json={"user_id": USER})
    assert r.status_code == 409, f"second undo is refused with 409: {r.status_code}"


def test_obstruction_rating_discards_the_frame(store, client, carried, registry):
    bad_t = datetime.fromisoformat(frames(store)[-1]["created_at"]) + timedelta(days=1)
    carried["obstructed_frame_time"] = bad_t
    add_frames(store, [{
        "id": 500, "created_at": bad_t.isoformat(), "green_ratio": 0.72,
        "current_state": ["base", 0.72], "imageURL": "https://example/leaf.jpg",
    }])
    rewind(registry, USER, 1)
    poll(store, registry)
    poisoned = persisted_green(store)
    r = client.post(f"/v1/ponds/{USER}/events/algae-rating", json={
        "user_id": USER, "severity": "obstruction",
        "image_id": 500, "green_ratio": 0.72,
    })
    assert r.status_code == 200, f"obstruction rating returns 200: {r.status_code}"
    ob = r.get_json()
    assert ob["removed_frames"] == 1, f"a frame was removed: {ob['removed_frames']}"
    cleaned = persisted_green(store)
    assert cleaned < poisoned, "level rolled back off the obstructed frame"
    assert ratings(store)[-1]["severity"] is None and ratings(store)[-1]["is_obstructed"] is True, \
        "obstruction row stores severity NULL"


def test_rating_context_endpoint_feeds_the_card(client):
    r = client.get(f"/v1/ponds/{USER}/ratings/algae")
    assert r.status_code == 200, f"GET /ratings/algae -> 200: {r.status_code}"
    ctxb = r.get_json()
    for key in ("ratings", "latest_rating", "calibration", "latest_image", "severity_levels"):
        assert key in ctxb, f"context exposes {key}"
    assert len(ctxb["severity_levels"]) == 5, \
        f"severity_levels lists all five options: {ctxb['severity_levels']}"
    cal = ctxb["calibration"]
    for key in ("label_counts", "labels_needed", "thresholds", "camera_drift", "class_targets"):
        assert key in cal, f"calibration exposes {key}"
    assert ctxb["latest_image"] is not None and "id" in ctxb["latest_image"], \
        "latest_image carries an id for pinning"


def test_calibration_reached_after_enough_labels(store, client, carried):
    bad_t = carried["obstructed_frame_time"]
    # Feed two of each class, pinned to real frames.
    pairs = [("none", 0.004), ("none", 0.005), ("minor", 0.03), ("minor", 0.033),
             ("moderate", 0.09), ("moderate", 0.095), ("severe", 0.25), ("severe", 0.27)]
    for i, (sev, g) in enumerate(pairs):
        iid = 900 + i
        add_frames(store, [{
            "id": iid,
            "created_at": (bad_t + timedelta(days=2 + i)).isoformat(),
            "green_ratio": g, "current_state": ["base", g],
            "imageURL": f"https://example/c{i}.jpg",
        }])
        rr = client.post(f"/v1/ponds/{USER}/events/algae-rating", json={
            "user_id": USER, "severity": sev, "image_id": iid, "green_ratio": g,
        })
        assert rr.status_code == 200, rr.get_json()

    cal = client.get(f"/v1/ponds/{USER}/ratings/algae").get_json()["calibration"]
    assert cal["thresholds"]["mode"] == "label_calibrated", \
        f"thresholds became label-calibrated: {cal['thresholds']['mode']}"
    assert sum(cal["labels_needed"].values()) == 0, \
        f"no classes still needed: {cal['labels_needed']}"
    assert cal["thresholds"]["watch"] < cal["thresholds"]["action"], "watch below action"
    # minor median 0.0315, moderate median 0.0925 -> watch ~0.062
    assert abs(cal["thresholds"]["watch"] - (0.0315 + 0.0925) / 2) < 0.01, \
        f"watch sits between minor and moderate medians: {round(cal['thresholds']['watch'], 4)}"

    # label_count is in the API response; pond_algae_evaluations has no
    # column for it, so the stored row does not carry it.
    algae_assessment = rr.get_json()["assessments"]["algae"]
    assert algae_assessment["label_count"] >= 8, \
        f"assessment reports the label count: {algae_assessment.get('label_count')}"
    assert "label_count" not in algae_evals(store)[-1], "stored row keeps only table columns"
    carried["calibration"] = cal


def test_calibration_survives_a_registry_eviction(client, carried, registry):
    cal = carried["calibration"]
    registry.evict(USER)
    cal2 = client.get(f"/v1/ponds/{USER}/ratings/algae").get_json()["calibration"]
    assert cal2["thresholds"]["mode"] == "label_calibrated", \
        f"thresholds reload from the ratings table: {cal2['thresholds']['mode']}"
    assert cal2["label_counts"] == cal["label_counts"], \
        f"label counts reload: {cal2['label_counts']}"


def test_bad_rating_bodies_rejected(client):
    r = client.post(f"/v1/ponds/{USER}/events/algae-rating", json={"user_id": USER, "severity": "apocalyptic"})
    assert r.status_code == 400, f"invalid severity -> 400: {r.status_code}"
    r = client.post(f"/v1/ponds/{USER}/events/algae-rating", json={"user_id": USER})
    assert r.status_code == 400, f"missing severity -> 400: {r.status_code}"
