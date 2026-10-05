"""Issue #25: property-based tests (Hypothesis) for the model engines.

Random sequences of sensor samples, logged events, polled environments and
camera frames are run through the engines, and these invariants are
checked after every step:

  - nitrogen mass: TAN + NO2 + NO3 changes only by feed additions, algal
    uptake and water-change removal (relative tolerance 1e-6);
  - no nitrogen pool, evaporative loss or algae level goes negative, and
    green coverage stays in [0, 1];
  - project_forward (and the projections built on it) never changes engine
    state;
  - a snapshot round trip is exact;
  - replaying the event ledger equals applying the same events live.

Times are recent so the chemistry engine's 30-day day buckets, pruned
against the wall clock when a snapshot loads, are all kept.

Writing this suite found that ledger checkpoints shared the live engine's
day-bucket lists (fixed in DailyAggregate.to_dict; regression test in
test_event_ledger.py).

Run from Backend/: python -m pytest -q -k property
"""
import json
import math
import os
from datetime import datetime, timedelta, timezone

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models.engine import EventKind, PondConfig, PondEvent, RawSample, WaterChemistryEngine, _ammonia_mg
from koi.models.pond_twin import PondTwin
from koi.models.sensor_inputs import SensorInput

T0 = (datetime.now(timezone.utc) - timedelta(days=4)).replace(hour=0, minute=0, second=0, microsecond=0)
REL_TOL = 1e-6
ABS_TOL = 1e-9  # mg; for pools that are still at or near zero

# 15 examples per property keeps the suite to about a minute in
# tools/check.py; set KOI_PROPERTY_EXAMPLES (say 200) for a deeper run.
PROPERTY_SETTINGS = settings(
    max_examples=int(os.environ.get("KOI_PROPERTY_EXAMPLES", "15")),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# ---------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------
def floats(lo: float, hi: float):
    return st.floats(min_value=lo, max_value=hi, allow_nan=False, allow_infinity=False)


def maybe(strategy):
    return st.one_of(st.none(), strategy)


configs = st.builds(
    PondConfig,
    volume_litres=floats(200.0, 50000.0),
    estimated_biomass_grams=floats(0.0, 50000.0),
    tap_tds_ppm=floats(0.0, 300.0),
    tap_nitrate_ppm=floats(0.0, 50.0),
)

# Channel values reach past the gate's plausible ranges, and any channel
# may be missing, so rejected, stale, rate-limited and missing readings
# are all exercised.
channels = st.fixed_dictionaries({
    "ph": maybe(floats(3.0, 11.0)),
    "tds": maybe(floats(0.0, 3500.0)),
    "temp": maybe(floats(-10.0, 50.0)),
    "lux": maybe(floats(0.0, 200000.0)),
})


def events_at(time: datetime):
    volume = st.one_of(
        st.tuples(floats(0.0, 120.0), st.none()),
        st.tuples(st.none(), floats(0.0, 60000.0)),
        st.tuples(st.none(), st.none()),
    )
    return st.one_of(
        st.builds(lambda g, p: PondEvent(kind=EventKind.FEEDING, time=time, food_grams=g, protein_percent=p),
                  maybe(floats(0.0, 2000.0)), maybe(floats(0.0, 60.0))),
        volume.map(lambda v: PondEvent(kind=EventKind.WATER_CHANGE, time=time,
                                       volume_percent=v[0], volume_litres=v[1])),
        volume.map(lambda v: PondEvent(kind=EventKind.TOP_UP, time=time,
                                       volume_percent=v[0], volume_litres=v[1])),
        st.sampled_from(["brush", "net", None]).map(
            lambda s: PondEvent(kind=EventKind.ALGAL_SCRUB, time=time, scrub_type=s)),
    )


evaporation_envs = st.builds(
    ev.DayEnvironment,
    air_temp_c=floats(15.0, 40.0),
    relative_humidity_pct=floats(20.0, 100.0),
    wind_speed_ms=floats(0.0, 15.0),
    rain_category=st.sampled_from(["unknown", "none", "light", "moderate", "heavy"]),
    water_temp_c=maybe(floats(15.0, 40.0)),
    rain_mm_measured=maybe(floats(0.0, 120.0)),
)

algae_envs = st.builds(
    ae.AlgaeDayEnvironment,
    lux=floats(0.0, 150000.0),
    temp_c=floats(0.0, 45.0),
    no3_ppm=maybe(floats(0.0, 200.0)),
)


@st.composite
def steps(draw, min_size: int = 1, max_size: int = 25):
    """A time-ordered list of steps, each at a distinct time:
    ("sample", time, channels), ("event", time, PondEvent) or
    ("environment", time, (evaporation env, algae env, camera frames))."""
    n = draw(st.integers(min_size, max_size))
    out, hours = [], 0.0
    for _ in range(n):
        hours += draw(floats(0.05, 12.0))
        time = T0 + timedelta(hours=hours)
        kind = draw(st.sampled_from(["sample", "sample", "event", "environment"]))
        if kind == "sample":
            out.append(("sample", time, draw(channels)))
        elif kind == "event":
            out.append(("event", time, draw(events_at(time))))
        else:
            frames = draw(st.lists(st.builds(
                ae.GreenSample,
                time=floats(0.0, 3.0).map(lambda back, t=time: t - timedelta(hours=back)),
                green_ratio=floats(0.0, 1.0),
                smoothed_green=maybe(floats(0.0, 1.0)),
                state=st.sampled_from([None, "base", "obstruction"]),
            ), max_size=3))
            frames.sort(key=lambda f: f.time)
            out.append(("environment", time, (draw(maybe(evaporation_envs)), draw(maybe(algae_envs)), frames)))
    return out


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def nitrogen_mg(engine: WaterChemistryEngine) -> float:
    return engine._tan_mg + engine._no2_mg + engine._no3_mg


def raw_sample(time: datetime, values: dict) -> RawSample:
    return RawSample(time=time, ph=values["ph"], tds=values["tds"], temp_c=values["temp"], lux=values["lux"])


def sensor_input(time: datetime, values: dict) -> SensorInput:
    return SensorInput(time, "ingestion", dict(values), {})


def run_twin(config: PondConfig, plan) -> PondTwin:
    twin = PondTwin.create(config)
    for kind, time, payload in plan:
        apply_step(twin, kind, time, payload)
    return twin


def apply_step(twin: PondTwin, kind: str, time: datetime, payload) -> None:
    if kind == "sample":
        twin.ingest_sensor_inputs([sensor_input(time, payload)])
    elif kind == "event":
        twin.apply_event(payload, now=time)
    else:
        evaporation_env, algae_env, frames = payload
        twin.ingest_environment(now=time, sample=None, evaporation_env=evaporation_env,
                                algae_env=algae_env, camera_samples=frames)


def as_json(snapshot: dict) -> dict:
    """The snapshot as it is stored (JSONB)."""
    return json.loads(json.dumps(snapshot))


def twin_state(twin: PondTwin) -> dict:
    snap = as_json(twin.to_snapshot())
    snap.pop("saved_at")
    return snap


class RecordingEngine(WaterChemistryEngine):
    """Records the inputs of every pool step, so the test can compute the
    algal nitrate uptake each step should have taken."""

    def __init__(self, config: PondConfig):
        super().__init__(config)
        self.steps: list[tuple[float, float | None, int]] = []

    def _advance_pools(self, hours, temp_c, lux_value):
        self.steps.append((hours, lux_value, self._algae_suppression_days_remaining))
        super()._advance_pools(hours, temp_c, lux_value)


def uptake_mg(no3_after_mg: float, hours: float, lux: float | None, suppression_days: int) -> float:
    """Nitrate taken up by algae over one step, from the nitrate left
    after it: uptake removes the fraction 1 - exp(-rate * hours) of the
    nitrate present, at 0.01/h per 10,000 lux (capped at 30,000 lux),
    scaled by 0.3 while a scrub's suppression lasts."""
    if lux is None:
        return 0.0
    rate = 0.01 * min(max(lux / 10000.0, 0.0), 3.0) * (0.3 if suppression_days > 0 else 1.0)
    return no3_after_mg * (math.exp(rate * hours) - 1.0)


def assert_close(actual: float, expected: float, what: str) -> None:
    assert math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=ABS_TOL), \
        f"{what}: {actual!r} != {expected!r}"


def assert_bounds(twin: PondTwin) -> None:
    chem = twin.chemistry
    assert chem._tan_mg >= 0.0 and chem._no2_mg >= 0.0 and chem._no3_mg >= 0.0
    assert twin.evaporation.cumulative_loss_litres >= 0.0
    assert twin.evaporation.loss_pct >= 0.0
    green = twin.algae.green_ratio
    assert green is None or 0.0 <= green <= 1.0


# ---------------------------------------------------------------------
# Nitrogen mass balance
# ---------------------------------------------------------------------
@PROPERTY_SETTINGS
@given(config=configs, plan=steps())
def test_property_nitrogen_changes_only_by_feed_uptake_and_water_change(config, plan):
    engine = RecordingEngine(config)
    for kind, time, payload in plan:
        if kind == "environment":
            continue  # the chemistry engine has no environment input
        before = nitrogen_mg(engine)
        if kind == "sample":
            engine.steps.clear()
            engine.ingest_sensor_sample(raw_sample(time, payload))
            assert len(engine.steps) <= 1
            removed = sum(uptake_mg(engine._no3_mg, h, lux, days) for h, lux, days in engine.steps)
            assert removed >= 0.0
            assert_close(nitrogen_mg(engine), before - removed, f"sample at {time}")
            continue

        event = payload
        engine.apply_event(event)
        if event.kind == EventKind.FEEDING:
            added = _ammonia_mg(event.food_grams or 0.0, event.protein_percent or 0.0)
            assert_close(nitrogen_mg(engine), before + added, "feeding")
        elif event.kind == EventKind.WATER_CHANGE:
            if event.volume_percent is not None:
                fraction = min(max(event.volume_percent / 100.0, 0.0), 1.0)
            elif event.volume_litres is not None:
                fraction = min(max(event.volume_litres / config.volume_litres, 0.0), 1.0)
            else:
                fraction = 0.0
            # The replaced water leaves with its share of every pool, and
            # the tap water brings its nitrate.
            expected = before * (1.0 - fraction) + config.tap_nitrate_ppm * config.volume_litres * fraction
            assert_close(nitrogen_mg(engine), expected, "water change")
        else:
            assert_close(nitrogen_mg(engine), before, event.kind.value)


@PROPERTY_SETTINGS
@given(config=configs, plan=steps(max_size=12), avg_daily_tan_mg=floats(0.0, 20000.0),
       temps=st.lists(maybe(floats(-5.0, 45.0)), max_size=5), fallback_temp_c=floats(-5.0, 45.0),
       horizon_days=st.integers(1, 30), steps_per_day=st.integers(1, 6))
def test_property_projection_without_light_adds_only_the_feed(config, plan, avg_daily_tan_mg, temps,
                                                              fallback_temp_c, horizon_days, steps_per_day):
    """With no light there is no algal uptake, so the projected nitrogen
    grows by exactly the assumed daily feed."""
    twin = run_twin(config, plan)
    start = nitrogen_mg(twin.chemistry)
    projection = twin.chemistry.project_forward(
        avg_daily_tan_mg=avg_daily_tan_mg, daily_temp_forecast_c=temps, fallback_temp_c=fallback_temp_c,
        fallback_lux=None, horizon_days=horizon_days, steps_per_day=steps_per_day)
    for day in projection["trajectory"]:
        total = (day["tan_ppm"] + day["no2_ppm"] + day["no3_ppm"]) * config.volume_litres
        assert_close(total, start + avg_daily_tan_mg * day["days_from_now"], f"day {day['days_from_now']}")


# ---------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------
@PROPERTY_SETTINGS
@given(config=configs, plan=steps())
def test_property_pools_losses_and_algae_never_go_negative(config, plan):
    twin = PondTwin.create(config)
    for kind, time, payload in plan:
        apply_step(twin, kind, time, payload)
        assert_bounds(twin)


@PROPERTY_SETTINGS
@given(config=configs, plan=steps(max_size=15), avg_daily_tan_mg=floats(0.0, 20000.0),
       lux=maybe(floats(0.0, 150000.0)), temp=floats(-5.0, 45.0),
       evaporation_days=st.lists(evaporation_envs, min_size=1, max_size=5),
       algae_days=st.lists(algae_envs, min_size=1, max_size=5), horizon_days=st.integers(1, 30))
def test_property_projections_stay_in_bounds(config, plan, avg_daily_tan_mg, lux, temp,
                                             evaporation_days, algae_days, horizon_days):
    twin = run_twin(config, plan)
    chem = twin.chemistry.project_forward(avg_daily_tan_mg=avg_daily_tan_mg, fallback_temp_c=temp,
                                          fallback_lux=lux, horizon_days=horizon_days)
    for day in chem["trajectory"]:
        assert day["tan_ppm"] >= 0.0 and day["no2_ppm"] >= 0.0 and day["no3_ppm"] >= 0.0
    evap = twin.evaporation.project_forward(daily_environment=evaporation_days, horizon_days=horizon_days)
    for day in evap["trajectory"]:
        assert day["cumulative_loss_litres"] >= 0.0 and day["cumulative_loss_pct"] >= 0.0
    algae = twin.algae.project_forward(daily_environment=algae_days, horizon_days=horizon_days)
    for day in algae.get("trajectory", []):
        assert 0.0 <= day["green_ratio"] <= 1.0


# ---------------------------------------------------------------------
# project_forward is read-only
# ---------------------------------------------------------------------
@PROPERTY_SETTINGS
@given(config=configs, plan=steps(max_size=15), avg_daily_tan_mg=floats(0.0, 20000.0),
       lux=maybe(floats(0.0, 150000.0)), temp=floats(-5.0, 45.0),
       evaporation_days=st.lists(evaporation_envs, min_size=1, max_size=5),
       algae_days=st.lists(algae_envs, min_size=1, max_size=5), horizon_days=st.integers(1, 30))
def test_property_project_forward_never_changes_engine_state(config, plan, avg_daily_tan_mg, lux, temp,
                                                             evaporation_days, algae_days, horizon_days):
    twin = run_twin(config, plan)
    before = twin_state(twin)
    twin.chemistry.project_forward(avg_daily_tan_mg=avg_daily_tan_mg, fallback_temp_c=temp,
                                   fallback_lux=lux, horizon_days=horizon_days)
    twin.no3_projection(avg_daily_tan_mg, temp, horizon_days)
    twin.evaporation.project_forward(daily_environment=evaporation_days, horizon_days=horizon_days)
    twin.algae.project_forward(daily_environment=algae_days, horizon_days=horizon_days)
    twin.algae.project_scrub_benefit(daily_environment=algae_days, horizon_days=horizon_days)
    assert twin_state(twin) == before


# ---------------------------------------------------------------------
# Snapshot round trip
# ---------------------------------------------------------------------
@PROPERTY_SETTINGS
@given(config=configs, plan=steps(), more=steps(max_size=5))
def test_property_snapshot_round_trip_is_exact(config, plan, more):
    twin = run_twin(config, plan)
    snapshot = as_json(twin.to_snapshot())
    loaded = PondTwin.from_snapshot(snapshot)
    assert twin_state(loaded) == twin_state(twin)

    # Each engine on its own as well.
    for engine, cls in ((twin.chemistry, WaterChemistryEngine), (twin.evaporation, ev.EvaporationFeedEngine),
                        (twin.algae, ae.AlgaeGrowthEngine)):
        alone = as_json(engine.to_snapshot())
        assert as_json(cls.from_snapshot(alone).to_snapshot()) == alone

    # The loaded twin carries on exactly as the original does. The extra
    # steps start after the plan's last step.
    shift = (plan[-1][1] - T0) + timedelta(minutes=1)
    for kind, time, payload in more:
        later = time + shift
        if kind == "event":
            payload = PondEvent.from_dict({**payload.to_dict(), "time": later.isoformat()})
        elif kind == "environment":
            evaporation_env, algae_env, frames = payload
            frames = [ae.GreenSample(f.time + shift, f.green_ratio, f.smoothed_green, f.state) for f in frames]
            payload = (evaporation_env, algae_env, frames)
        apply_step(twin, kind, later, payload)
        apply_step(loaded, kind, later, payload)
    assert twin_state(loaded) == twin_state(twin)


# ---------------------------------------------------------------------
# Ledger replay equals live application
# ---------------------------------------------------------------------
def chemistry_state(twin: PondTwin) -> dict:
    """The chemistry state a replay must reproduce. The engine's event
    list is compared as a set: its order is application order."""
    snap = as_json(twin.chemistry.to_snapshot())
    snap["events"] = sorted(json.dumps(e, sort_keys=True) for e in snap["events"])
    return snap


@st.composite
def ledger_plans(draw):
    """Sensor inputs and events with ids, at distinct times (events at one
    instant apply in the order recorded), plus an order to post the events
    in after every reading has been ingested."""
    plan = [s for s in draw(steps(min_size=2, max_size=30)) if s[0] != "environment"]
    assume(plan)
    events = [(f"e{n}", s[2]) for n, s in enumerate(s for s in plan if s[0] == "event")]
    post_order = draw(st.permutations(events))
    return plan, events, post_order


@PROPERTY_SETTINGS
@given(config=configs, ledger_plan=ledger_plans())
def test_property_ledger_replay_equals_applying_live(config, ledger_plan):
    plan, events, post_order = ledger_plan
    ids = iter(event_id for event_id, _ in events)
    readings = [sensor_input(time, payload) for kind, time, payload in plan if kind == "sample"]

    live = PondTwin.create(config)
    for kind, time, payload in plan:
        if kind == "sample":
            live.ingest_sensor_inputs([sensor_input(time, payload)])
        else:
            assert live.record_event(next(ids), payload, now=time)["status"] == "applied"

    def history(after, until):
        return [i for i in readings if (after is None or i.time > after) and i.time <= until]

    replayed = PondTwin.create(config)
    replayed.ingest_sensor_inputs(readings)
    now = plan[-1][1] + timedelta(minutes=1)
    for event_id, event in post_order:
        outcome = replayed.record_event(event_id, event, now=now, history=history)
        assert outcome["status"] == "applied" and outcome["placed_late"] is False

    assert chemistry_state(replayed) == chemistry_state(live)
    assert sorted(e.event_id for e in replayed.ledger.live()) == sorted(e for e, _ in events)
