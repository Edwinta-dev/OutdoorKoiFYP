"""Issue #20: rebuild a pond's twin from its history (koi/tools/rebuild.py).

The fixture is three days of one pond lived through the live path: each
hour the NEA job (koi.weather.job.run_product, on a fake transport) writes
weather history and the latest caches, the node stores a reading, the app
logs interventions, the camera stores frames, a keeper rates one, and the
poller runs on the payload the caches give at that hour. The rebuild then
reads the same storage and must arrive at the same twin.

Run from Backend/: python -m pytest -q -k rebuild
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from nea_fakes import FakeTransport, make_client, response

from conftest import make_settings
from koi.dev.seed import SeedHistory
from koi.models.local_time import zone
from koi.models.pond_twin import PondTwin
from koi.models.profile import ProfileHistory, profile_from_userdata_config
from koi.registry import EngineRegistry
from koi.storage import MemoryStorage
from koi.storage.base import parse_timestamp
from koi.tools import rebuild as rb
from koi.weather import job
from koi.weather.nea import SINGAPORE
from koi.worker import poller

POND = 7
START = datetime(2026, 9, 28, 0, 10, tzinfo=timezone.utc)
HOURS = 72
END = START + timedelta(hours=HOURS - 1)
STATIONS = {"air-temperature": "S43", "rainfall": "S43", "wind-speed": "S43",
            "two-hr-forecast": "Serangoon", "twenty-four-hr-forecast": "REG_CENTRAL"}
PRODUCTS = ("air-temperature", "rainfall", "wind-speed", "two-hr-forecast", "twenty-four-hr-forecast",
            "four-day-outlook")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def sgt(at: datetime) -> str:
    return at.astimezone(zone(SINGAPORE)).isoformat()


def nea_body(product: str, at: datetime, h: int) -> dict:
    """A response of product as NEA would give it at `at`, values varying by hour."""
    body = response(product)
    data = body["data"]
    if product in ("air-temperature", "rainfall", "wind-speed"):
        value = {"air-temperature": 27.0 + (h % 24) / 4, "rainfall": 0.4 if h % 9 == 0 else 0.0,
                 "wind-speed": 2.0 + (h % 5)}[product]
        data["readings"] = [{"timestamp": sgt(at - timedelta(minutes=2)),
                             "data": [{"stationId": "S43", "value": value}]}]
    elif product == "two-hr-forecast":
        issued = at - timedelta(minutes=5)
        data["items"] = [{"timestamp": sgt(issued), "update_timestamp": sgt(issued),
                          "valid_period": {"start": sgt(issued), "end": sgt(issued + timedelta(hours=2)), "text": ""},
                          "forecasts": [{"area": "Serangoon", "forecast": "Showers" if h % 7 == 0 else "Cloudy"}]}]
    elif product == "twenty-four-hr-forecast":
        record = data["records"][0]
        issued = at - timedelta(minutes=10)
        record["timestamp"] = record["updatedTimestamp"] = sgt(issued)
        record["general"]["relativeHumidity"] = {"low": 55 + h % 10, "high": 90, "unit": "Percentage"}
        record["general"]["validPeriod"] = {"start": sgt(issued), "end": sgt(issued + timedelta(days=1)), "text": ""}
        record["periods"] = []
    else:
        record = data["records"][0]
        issued = at - timedelta(minutes=15)
        record["timestamp"] = record["updatedTimestamp"] = sgt(issued)
        today = at.astimezone(zone(SINGAPORE)).replace(hour=0, minute=0, second=0, microsecond=0)
        template = record["forecasts"][0]
        record["forecasts"] = []
        for n in range(1, 5):
            day = copy.deepcopy(template)
            day["timestamp"] = (today + timedelta(days=n)).isoformat()
            day["temperature"] = {"low": 25, "high": 31 + (h // 24 + n) % 4, "unit": "Degrees Celsius"}
            record["forecasts"].append(day)
    return body


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def sensor_rows(at: datetime, h: int) -> list[dict]:
    values = {"pH": 7.4 + (h % 6) / 20, "TDS": 180 + h, "temp": 27.5 + (h % 24) / 10,
              "LUX": 0 if (h + 8) % 24 >= 12 else 20000 + 500 * (h % 12)}
    return [{"id": 1000 + h * 10 + n, "userID": POND, "sensor_type": t, "data1": v, "created_at": at.isoformat()}
            for n, (t, v) in enumerate(values.items())]


def intervention(row_id: int, kind: str, at: datetime, created: datetime, **values) -> dict:
    row = {"id": row_id, "userID": POND, "event_type": kind, "event_timestamp": at.isoformat(),
           "created_at": created.isoformat(), "volume_percentage": None, "volume_litres": None, "food_grams": None,
           "protein_percentage": None, "algae_method": None}
    row.update(values)
    return row


def interventions_at(h: int, at: datetime) -> list[dict]:
    rows = []
    if h % 12 == 3:
        rows.append(intervention(5000 + h, "FEEDING", at - timedelta(minutes=30), at - timedelta(minutes=25),
                                 food_grams=30, protein_percentage=38))
    if h == 30:
        rows.append(intervention(6001, "WATER_CHANGE", at - timedelta(minutes=40), at - timedelta(minutes=35),
                                 volume_percentage=20))
    if h == 44:  # logged nine hours late: replayed into its place
        rows.append(intervention(6002, "FEEDING", at - timedelta(hours=9), at - timedelta(minutes=5),
                                 food_grams=50, protein_percentage=40))
    if h == 50:
        rows.append(intervention(6003, "WATER_TOPUP", at - timedelta(minutes=20), at - timedelta(minutes=15),
                                 volume_litres=40))
    if h == 60:
        rows.append(intervention(6004, "ALGAE_SCRUB", at - timedelta(minutes=50), at - timedelta(minutes=45),
                                 algae_method="manual"))
    return rows


def frame_at(h: int, at: datetime) -> list[dict]:
    if h % 4 != 1:
        return []
    green = 0.03 + 0.002 * h
    return [{"id": 9000 + h, "user_ID": POND, "created_at": (at - timedelta(minutes=3)).isoformat(),
             "green_ratio": green, "current_state": json.dumps(["base", green]),
             "imageURL": f"https://example.invalid/{h}.jpg", "mask_version": 1 if h < 40 else 2}]


RATING_HOUR = 46


def live_pond(weather_from: int = 0) -> tuple[MemoryStorage, Clock]:
    """The fixture pond lived through the live path; weather is fetched
    from hour weather_from on."""
    clock = Clock(START)
    storage = MemoryStorage(clock=clock)
    storage.add_rows("UserData", [{"userID": POND, "volume": 2000, "biomass": 3.0, "ClosestStations": STATIONS}])
    settings = make_settings()
    registry = EngineRegistry(storage, settings.hypoxia_thresholds, settings.sensor_ingest)
    transport = FakeTransport()
    transport.handler = lambda product, params: nea_body(
        product, clock.at, int((clock.at - START) / timedelta(hours=1)))
    client = make_client(transport)
    everything = job.HistoryFilter(None, None)
    [config] = storage.fetch_active_pond_configs()
    profiles = ProfileHistory([profile_from_userdata_config(config)])

    for h in range(HOURS):
        at = START + timedelta(hours=h)
        clock.at = at
        if h >= weather_from:
            for product in PRODUCTS:
                assert job.run_product(storage, client, product, everything, clock).status == "ok"
        storage.add_rows("pondInterventions", interventions_at(h, at))
        storage.add_rows("imageTable", frame_at(h, at))
        if h == RATING_HOUR:
            rated_at = at - timedelta(minutes=10)
            frame = storage.fetch_image_history(POND, limit=1)[0]
            storage.add_rows("algae_severity_ratings", [{
                "id": 77, "userid": POND, "severity": "moderate", "is_obstructed": False, "image_id": frame["id"],
                "image_url": frame["imageURL"], "green_ratio_at_rating": frame["green_ratio"],
                "rated_at": rated_at.isoformat()}])

            def rate(twin: PondTwin, t: datetime = rated_at, f: dict = frame) -> dict:
                # As the rating endpoint applies it (koi/api/routes.py).
                return twin.apply_severity_rating(time=t, severity="moderate", green_ratio_at_rating=f["green_ratio"],
                                                  image_id=f["id"], rating_id=77)
            registry.with_twin(POND, rate, profiles=profiles)
        storage.add_rows("SensorData", sensor_rows(at, h))
        # The bundled payload the caches give now (the dev stack's rule).
        caches = {"weather_telemetry": storage.rows("weather_telemetry"),
                  "weather_forecasts": storage.rows("weather_forecasts"),
                  "SensorData": storage.rows("SensorData"), "UserData": storage.rows("UserData")}
        storage.set_dashboard_payload(POND, SeedHistory(caches).payload(POND, at))
        poller._poll_user(registry, POND, config, now=at)
    return storage, clock


def without_save_time(snapshot: dict) -> dict:
    return {k: v for k, v in snapshot.items() if k != "saved_at"}


@pytest.fixture(scope="module")
def lived():
    return live_pond()


@pytest.fixture(scope="module")
def rebuilt(lived):
    storage, _ = lived
    return rb.rebuild(storage, make_settings(), POND, now=END, dry_run=True)


def test_rebuilt_twin_equals_the_live_twin(lived, rebuilt):
    storage, _ = lived
    live = storage.load_engine_snapshot(POND)
    assert rebuilt.rebuilt_snapshot is not None
    assert without_save_time(rebuilt.rebuilt_snapshot) == without_save_time(live)
    # The fixture exercised every input kind.
    twin = PondTwin.from_snapshot(rebuilt.rebuilt_snapshot)
    kinds = {e.kind.value for e in twin.chemistry._events}
    assert kinds == {"feeding", "water_change", "top_up", "algal_scrub"}
    assert twin.algae._sample_count == len(storage.rows("imageTable"))
    assert [r["rating_id"] for r in twin.algae._ratings] == [77]


def test_comparison_shows_no_difference(rebuilt):
    rows = {r["item"]: r for r in rebuilt.comparison}
    assert all(r["same"] for r in rows.values()), [r for r in rows.values() if not r["same"]]
    for item in ("chemistry TAN", "chemistry NO3", "evaporation loss", "algae level (green ratio)",
                 "last chemistry assessment status", "last evaporation assessment status",
                 "last algae assessment status"):
        assert rows[item]["stored"] is not None, item
    assert rows["events applied"]["stored"] == rows["events applied"]["rebuilt"] == 10


def test_report_names_inputs_versions_and_cutoff(rebuilt):
    assert rebuilt.input_cutoff == END.isoformat()
    assert rebuilt.inputs["polls"] == HOURS and rebuilt.inputs["sensor_rows"] == HOURS * 4
    assert rebuilt.inputs["interventions"] == 10 and rebuilt.inputs["ratings"] == 1
    assert rebuilt.versions["model"].startswith("twin.")
    assert rebuilt.versions["profile"]["source"] == "UserData"
    assert rebuilt.versions["camera_mask"]["on_frames"] == [1, 2]
    assert rebuilt.weather == {"status": "complete", "incomplete": []}
    text = rb.format_report(rebuilt)
    assert "weather: complete for every poll" in text and "Nothing was written" in text


def test_dry_run_writes_nothing(lived):
    storage, _ = lived
    version = storage.fetch_snapshot_version(POND)
    snapshot = storage.load_engine_snapshot(POND)
    assert rb.main(["--pond", str(POND), "--dry-run"], storage=storage, settings=make_settings(), now=END) == 0
    assert storage.fetch_snapshot_version(POND) == version
    assert storage.load_engine_snapshot(POND) == snapshot


def test_write_saves_the_rebuilt_snapshot_as_a_new_version(capsys, caplog):
    storage, _ = live_pond()
    version = storage.fetch_snapshot_version(POND)
    live = storage.load_engine_snapshot(POND)
    ledger = list(storage.rows("sensor_ingest_ledger"))
    with caplog.at_level("INFO", logger="koi.tools.rebuild"):
        assert rb.main(["--pond", str(POND)], storage=storage, settings=make_settings(), now=END) == 0
    assert storage.fetch_snapshot_version(POND) == version + 1
    assert without_save_time(storage.load_engine_snapshot(POND)) == without_save_time(live)
    assert storage.rows("sensor_ingest_ledger") == ledger
    assert f"Wrote the rebuilt snapshot as version {version + 1}" in capsys.readouterr().out
    [record] = [r for r in caplog.records if r.getMessage() == "pond_rebuilt"]
    assert record.koi_fields["written_version"] == version + 1


def test_a_save_by_another_process_refuses_the_write(lived, capsys):
    storage, _ = lived

    class Racing(MemoryStorage):
        pass

    racing = Racing.__new__(Racing)
    racing.__dict__.update(storage.__dict__)
    stored, version = storage.load_engine_state(POND)
    racing.load_engine_state = lambda user_id: (stored, version - 1)  # type: ignore[method-assign]
    assert rb.main(["--pond", str(POND)], storage=racing, settings=make_settings(), now=END) == 1
    assert "nothing was written" in capsys.readouterr().err
    assert storage.fetch_snapshot_version(POND) == version


def test_since_starts_a_fresh_twin_at_that_date(lived):
    storage, _ = lived
    since = rb.parse_since("2026-09-30")
    assert since == datetime(2026, 9, 29, 16, 0, tzinfo=timezone.utc)  # Singapore midnight
    report = rb.rebuild(storage, make_settings(), POND, since=since, now=END)
    assert parse_timestamp(report.inputs["first_poll"]) >= since
    twin = PondTwin.from_snapshot(report.rebuilt_snapshot)
    assert all(e.time >= since for e in twin.chemistry._events)
    assert report.inputs["sensor_rows"] < HOURS * 4
    assert not all(r["same"] for r in report.comparison)


def test_missing_weather_history_is_reported_not_taken_from_the_cache():
    storage, _ = live_pond(weather_from=24)
    # Today's cache rows exist; the first day has no history behind it.
    assert storage.rows("weather_telemetry")
    report = rb.rebuild(storage, make_settings(), POND, now=END)
    assert report.weather["status"] == "incomplete"
    [gap] = report.weather["incomplete"]
    assert parse_timestamp(gap["from"]) == START
    assert parse_timestamp(gap["to"]) == START + timedelta(hours=23)
    assert gap["polls"] == 24
    assert set(gap["missing"]) == {"air_temp", "rainfall", "wind_speed", "humidity", "forecast_2hr",
                                   "outlook_4day"}
    assert "evaporation loss" in gap["affected"] and "days to top-up" in gap["affected"]
    assert "INCOMPLETE" in rb.format_report(report)

    payload, missing = rb.weather_payload_as_of(storage, STATIONS, START + timedelta(hours=3))
    assert payload["nea_telemetry"]["air_temp"] == {} and "air_temp" in missing
    payload, missing = rb.weather_payload_as_of(storage, STATIONS, END)
    assert missing == [] and payload["nea_telemetry"]["air_temp"]["value"] == 27.0 + ((HOURS - 1) % 24) / 4
    assert len(payload["nea_forecasts"]["outlook_4day"]) == 4


def test_comparison_reads_an_older_stored_snapshot():
    stored = json.loads((FIXTURES / "snapshot_v3_sensor_inputs.json").read_text(encoding="utf-8"))
    rebuilt = PondTwin.from_snapshot(stored).to_snapshot()
    rows = {r["item"]: r for r in rb.compare(stored, rebuilt, {}, {})}
    assert rows["chemistry TAN"]["same"] and rows["chemistry TAN"]["stored"] == stored["chemistry"]["tan_mg"]
    assert rows["events applied"]["stored"] is None and rows["events applied"]["rebuilt"] == 0
    bare = stored["chemistry"]
    rows = {r["item"]: r for r in rb.compare(bare, rebuilt, {}, {})}
    assert rows["chemistry NO3"]["stored"] == bare["no3_mg"]


def test_unknown_pond_fails_cleanly(lived, capsys):
    storage, _ = lived
    assert rb.main(["--pond", "999", "--dry-run"], storage=storage, settings=make_settings(), now=END) == 1
    assert "no UserData row" in capsys.readouterr().err
