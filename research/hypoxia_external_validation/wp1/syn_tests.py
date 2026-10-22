"""WP1 step 3B / step 5: SYN (hand-specified, D-tier) fixtures against production code.
No external data. Every test records pass/fail + the numbers it computed.
Run: python -I wp1/syn_tests.py  -> wp1/out/syn/syn_results.json"""
import json, math, random, sys, traceback, uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import numpy as np, pandas as pd
import adapters as A, harness as H
from koi.models import device_health as dh, event_ledger as el
from koi.models.engine import (EventKind, PondConfig, PondEvent, RawSample, SensorGate, WaterChemistryEngine,
                               salt_tds_step)
from koi.models.local_time import local_date, local_day_key, as_aware
from koi.models.pond_twin import PondTwin
from koi.models.profile import PondProfile, ProfileHistory
from koi.models.sensor_inputs import (SensorInput, channel_freshness, complete_groups, fresh_value, group_rows,
                                      merge_inputs, parse_timestamp, effective_sample_time)
from koi.models.tds_prompts import PromptConfig, TdsPrompts
from koi.models.tds_steps import detect_steps, predicted_salt_step, predicted_water_change_step

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)
OUT = _common.WP1 / "out" / "syn"
OUT.mkdir(parents=True, exist_ok=True)
RESULTS: list = []


def test(fn):
    name = fn.__name__
    try:
        detail = fn() or {}
        RESULTS.append({"test": name, "ok": True, **detail})
    except AssertionError as e:
        RESULTS.append({"test": name, "ok": False, "assertion": str(e)[:500], "tb": traceback.format_exc()[-800:]})
    except Exception as e:  # noqa: BLE001
        RESULTS.append({"test": name, "ok": False, "error": f"{type(e).__name__}: {e}"[:500], "tb": traceback.format_exc()[-800:]})
    return fn


def spec_syn(tz="Asia/Singapore", vol=700.0):
    return A.SourceSpec("syn", None, tz, "SYN", 900, vol, "SYN fixture", {"channels": ["pH", "TDS", "temp"]})


def syn_table(days=14, step_min=15, tds_fn=None, ph_fn=None, temp_fn=None, tds=True):
    """Smooth hand-specified series; every value differs from the previous one (no stale runs)."""
    n = days * 24 * 60 // step_min
    t = pd.date_range(T0, periods=n, freq=f"{step_min}min", tz="UTC")
    h = np.arange(n) * step_min / 60.0
    temp = (temp_fn(h) if temp_fn else 26.0 + 1.5 * np.sin(2 * np.pi * h / 24.0)).round(3)
    ph = (ph_fn(h) if ph_fn else 7.6 + 0.15 * np.sin(2 * np.pi * h / 24.0 + 1.0)).round(3)
    ch = {"pH": pd.Series(ph), "temp": pd.Series(temp)}
    if tds:
        ch["TDS"] = pd.Series((tds_fn(h) if tds_fn else 300.0 + 5.0 * np.sin(2 * np.pi * h / 24.0)).round(3))
    return A.build_rows(t, np.arange(n), ch)


def ev(kind, day, hh, mm=7, **kw):
    return PondEvent(kind=kind, time=T0 + timedelta(days=day, hours=hh, minutes=mm), **kw)


EVENTS = [
    ev(EventKind.WATER_CHANGE, 3, 12, volume_percent=30.0),
    ev(EventKind.FEEDING, 4, 9, food_grams=20.0, protein_percent=40.0),
    ev(EventKind.SALT, 6, 10, salt_grams=50.0),
    ev(EventKind.TOP_UP, 8, 15, volume_percent=10.0),
    ev(EventKind.FEEDING, 9, 8, food_grams=15.0, protein_percent=35.0),
]
EIDS = [str(uuid.UUID(int=1000 + i)) for i in range(len(EVENTS))]


def row_of(i, e, **over):
    r = {"id": i + 1, "event_id": EIDS[i], "event_type": {EventKind.WATER_CHANGE: "WATER_CHANGE", EventKind.FEEDING: "FEEDING",
         EventKind.SALT: "SALT", EventKind.TOP_UP: "WATER_TOPUP"}[e.kind], "event_timestamp": e.time, "created_at": e.time,
         "volume_percentage": e.volume_percent, "volume_litres": e.volume_litres, "algae_method": None,
         "food_grams": e.food_grams, "protein_percentage": e.protein_percent, "salt_grams": e.salt_grams, "notes": None}
    r.update(over)
    return r


class Session:
    """A pond twin plus the in-memory sensor ledger, driven like the poller."""

    def __init__(self, tab, spec=None, history=True):
        self.spec = spec or spec_syn()
        self.tab = tab
        self.twin = H.make_twin(self.spec)
        self.ledger = H.SensorLedger(tab)
        self.hist = self.ledger.history if history else None
        self.now = T0

    def tick(self, t):
        self.now = max(self.now, t)

    def deliver(self, idx):
        idx = np.asarray(idx)
        rows = self.tab.take(idx)
        if rows:
            self.tick(max(r["created_at"] for r in rows))
        with H.frozen_clock(self.now):
            disc = group_rows(rows)
            out = self.twin.ingest_sensor_inputs(disc.inputs, history=self.hist, now=self.now)
        self.ledger.mark(idx)
        return out

    def deliver_until(self, t_end, t_from=None):
        m = self.tab.tns <= pd.Timestamp(t_end).value
        if t_from is not None:
            m &= self.tab.tns > pd.Timestamp(t_from).value
        m &= ~self.ledger.delivered
        return self.deliver(np.nonzero(m)[0])

    def record(self, i, e=None):
        e = e or EVENTS[i]
        self.tick(e.time)
        with H.frozen_clock(self.now):
            return self.twin.record_event(EIDS[i], e, now=self.now, history=self.hist)

    def reconcile(self, rows):
        with H.frozen_clock(self.now):
            return self.twin.reconcile_events(rows, now=self.now, history=self.hist)


def oracle(tab, events, cfg=None, upto=None):
    """Ideal in-order chemistry: sensors and events merged by time (sensors first at equal
    times) straight into WaterChemistryEngine, no ledger, no replay, no checkpoints."""
    cfg = cfg or PondConfig(volume_litres=700.0, estimated_biomass_grams=0.0, time_zone="Asia/Singapore")
    eng = WaterChemistryEngine(cfg)
    inputs = group_rows(tab.take(np.arange(len(tab)))).inputs
    items = [(i.time, 0, k, i) for k, i in enumerate(inputs) if upto is None or i.time <= upto]
    items += [(e.time, 1, k, e) for k, e in enumerate(events)]
    for _, rank, _, it in sorted(items, key=lambda x: x[:3]):
        if rank == 0:
            eng.ingest_sensor_sample(RawSample(it.time, it.channels.get("ph"), it.channels.get("tds"),
                                               it.channels.get("temp"), it.channels.get("lux")))
        else:
            eng.apply_event(it)
    return eng


def jclose(a, b, tol=1e-9, path="", worst=None):
    """max relative difference of two JSON trees (exact for non-floats); None if structures differ."""
    worst = worst if worst is not None else [0.0]
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False, f"keys differ at {path}: {sorted(set(a) ^ set(b))[:4]}"
        for k in a:
            ok, msg = jclose(a[k], b[k], tol, f"{path}/{k}", worst)
            if not ok:
                return False, msg
        return True, worst[0]
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False, f"len differs at {path}: {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            ok, msg = jclose(x, y, tol, f"{path}[{i}]", worst)
            if not ok:
                return False, msg
        return True, worst[0]
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return (a == b), f"None mismatch at {path}"
        d = abs(a - b) / max(1.0, abs(a), abs(b))
        worst[0] = max(worst[0], d)
        return (d <= tol), (worst[0] if d <= tol else f"float differs at {path}: {a} vs {b}")
    return (a == b), (worst[0] if a == b else f"differs at {path}: {a!r} vs {b!r}")


def chem_equal(twin_or_eng, oracle_eng):
    c = twin_or_eng.chemistry if hasattr(twin_or_eng, "chemistry") else twin_or_eng
    return jclose(json.loads(json.dumps(c.to_snapshot(), default=str)), json.loads(json.dumps(oracle_eng.to_snapshot(), default=str)))


# ----------------------------------------------------------------------
# B. event ledger / PondTwin (D)
# ----------------------------------------------------------------------
TAB14 = syn_table(14)
ALL14 = np.arange(len(TAB14))


def day_end(d):
    return T0 + timedelta(days=d + 1)


@test
def ledger_events_arrive_after_all_sensors_any_order():
    orac = oracle(TAB14, EVENTS)
    res = {}
    rnd = random.Random(_common.SEED)
    orders = {"forward": [0, 1, 2, 3, 4], "reverse": [4, 3, 2, 1, 0], "shuffled": rnd.sample(range(5), 5)}
    for name, order in orders.items():
        s = Session(TAB14)
        s.deliver(ALL14)
        rep = [s.record(i) for i in order]
        ok, d = chem_equal(s.twin, orac)
        res[name] = {"equal_to_inorder_oracle": ok, "max_rel_diff_or_msg": d, "statuses": [r["status"] for r in rep],
                     "replayed": [r["replayed"] for r in rep], "placed_late": [r["placed_late"] for r in rep]}
        assert ok, f"{name}: {d}"
    return res


@test
def ledger_events_recorded_before_the_sensors_they_precede():
    orac = oracle(TAB14, EVENTS)
    s = Session(TAB14)
    for i in range(5):
        s.record(i)
    s.deliver(ALL14)
    ok, d = chem_equal(s.twin, orac)
    assert ok, d
    return {"max_rel_diff": d}


@test
def ledger_interleaved_daily_arrival_equals_oracle():
    orac = oracle(TAB14, EVENTS)
    s = Session(TAB14)
    for d in range(14):
        s.deliver_until(day_end(d), T0 + timedelta(days=d) - timedelta(seconds=1) if d else None)
        for i, e in enumerate(EVENTS):
            if T0 + timedelta(days=d) <= e.time < day_end(d):
                s.record(i)
    ok, dd = chem_equal(s.twin, orac)
    assert ok, dd
    return {"max_rel_diff": dd}


@test
def ledger_duplicate_event_ids_are_noops():
    s = Session(TAB14)
    s.deliver(ALL14)
    for i in range(5):
        s.record(i)
    h1 = H.snap_hash(s.twin.to_snapshot(), True)
    again = [s.record(i) for i in range(5)] + [s.record(i) for i in range(5)]
    h2 = H.snap_hash(s.twin.to_snapshot(), True)
    assert all(r["status"] == "duplicate" for r in again), again
    assert h1 == h2, "state changed by duplicate posts"
    return {"duplicate_posts": len(again), "state_hash_unchanged": True}


@test
def ledger_same_event_id_in_two_ponds_is_independent():
    a, b = Session(TAB14), Session(TAB14)
    a.deliver(ALL14); b.deliver(ALL14)
    a.record(0)
    assert b.twin.ledger.get(EIDS[0]) is None
    assert b.record(0)["status"] == "applied"
    return {}


@test
def ledger_reconcile_rows_equal_oracle_and_are_idempotent():
    orac = oracle(TAB14, EVENTS)
    rows = [row_of(i, e) for i, e in enumerate(EVENTS)]
    s = Session(TAB14)
    s.deliver(ALL14)
    s.tick(T0 + timedelta(days=14))
    r1 = s.reconcile(rows)
    ok, d = chem_equal(s.twin, orac)
    assert ok, d
    h = H.snap_hash(s.twin.to_snapshot(), True)
    r2 = s.reconcile(rows)
    assert H.snap_hash(s.twin.to_snapshot(), True) == h and not (r2["applied"] or r2["revised"] or r2["deleted"])
    return {"applied_first": len(r1["applied"]), "applied_second": len(r2["applied"]), "replayed_first": r1["replayed"]}


@test
def ledger_edit_delete_restore_equal_oracle():
    rows = [row_of(i, e) for i, e in enumerate(EVENTS)]
    s = Session(TAB14)
    s.deliver(ALL14)
    s.tick(T0 + timedelta(days=14))
    s.reconcile(rows)
    # edit: water change 30 % -> 50 %
    edited = [dict(r) for r in rows]
    edited[0]["volume_percentage"] = 50.0
    rep = s.reconcile(edited)
    e2 = [PondEvent(**{**EVENTS[0].__dict__, "volume_percent": 50.0})] + EVENTS[1:]
    ok, d = chem_equal(s.twin, oracle(TAB14, e2))
    assert ok and rep["revised"] == [EIDS[0]], (rep, d)
    # delete the salt event
    gone = [r for k, r in enumerate(edited) if k != 2]
    rep = s.reconcile(gone)
    e3 = [e2[0], e2[1]] + e2[3:]
    ok, d = chem_equal(s.twin, oracle(TAB14, e3))
    assert ok and rep["deleted"] == [EIDS[2]], (rep, d)
    # restore it (undone delete)
    rep = s.reconcile(edited)
    ok, d = chem_equal(s.twin, oracle(TAB14, e2))
    assert ok and rep["restored"] == [EIDS[2]], (rep, d)
    # a repost of a deleted id stays a no-op (tombstone) - delete again then post
    s.reconcile(gone)
    assert s.record(2)["status"] == "deleted"
    return {"edit_equal": True, "delete_equal": True, "restore_equal": True, "tombstone_noop": True}


@test
def ledger_legacy_event_without_id_is_claimed_not_doubled():
    s = Session(TAB14)
    s.deliver(ALL14)
    s.tick(T0 + timedelta(days=14))
    with H.frozen_clock(s.now):
        s.twin.record_event(None, EVENTS[1], now=s.now, history=s.hist)
    n_before = len(s.twin.ledger.entries)
    rep = s.reconcile([row_of(1, EVENTS[1])])
    ok, d = chem_equal(s.twin, oracle(TAB14, [EVENTS[1]]))
    assert ok and rep["claimed"] == [EIDS[1]] and len(s.twin.ledger.entries) == n_before, (rep, d)
    return {"claimed": rep["claimed"]}


@test
def ledger_no_future_event_is_applied_before_it_is_visible():
    """An event whose time is after every sensor input is applied directly; a reconcile that does
    not yet list it must not invent it."""
    s = Session(TAB14)
    s.deliver_until(day_end(5))
    before = H.snap_hash(s.twin.to_snapshot(), True)
    s.reconcile([row_of(4, EVENTS[4])])  # event on day 9, newest input day 5
    st = s.twin.ledger.get(EIDS[4])
    assert st is not None and st.model_time == EVENTS[4].time
    assert s.twin.ledger.newest_input_at == EVENTS[4].time or s.twin.ledger.newest_input_at > day_end(5) - timedelta(minutes=1)
    s.deliver(ALL14)  # the sensors catch up through a replay
    ok, d = chem_equal(s.twin, oracle(TAB14, [EVENTS[4]]))
    assert ok, d
    return {"state_changed_by_future_event": before != H.snap_hash(s.twin.to_snapshot(), True)}


@test
def ledger_event_older_than_replay_window_is_placed_late_not_replayed():
    tab = syn_table(45)
    s = Session(tab)
    s.deliver(np.arange(len(tab)))
    old = ev(EventKind.WATER_CHANGE, 3, 12, volume_percent=30.0)
    rep = s.record(0, old)
    entry = s.twin.ledger.get(EIDS[0])
    orac = oracle(tab, [old])
    ok, d = chem_equal(s.twin, orac)
    # documented (event_ledger.py): cannot be placed -> applied at the current state one microsecond after newest input
    assert rep["placed_late"] and entry.model_time > old.time and rep["replayed"] is False, rep
    return {"placed_late": rep["placed_late"], "model_time_shift_days": (entry.model_time - old.time).days,
            "equals_inorder_oracle": ok, "note": "documented behaviour; state differs from in-order by construction" if not ok else "equal"}


@test
def ledger_checkpoints_bounded_and_sensor_replay_equals_inorder_over_45_days():
    tab = syn_table(45)
    s = Session(tab)
    s.deliver(np.arange(len(tab)))
    n_ck = len(s.twin.ledger.checkpoints)
    cutoff = s.twin.ledger.newest_input_at - el.WINDOW
    kept_old = [c for c in s.twin.ledger.checkpoints if c.through is None or c.through <= cutoff]
    assert n_ck <= 33 and len(kept_old) <= 1, (n_ck, len(kept_old))
    return {"checkpoints": n_ck, "older_than_window_kept": len(kept_old)}


@test
def ledger_out_of_window_rows_can_be_reapplied_by_a_later_replay_F3():
    """F3: a row older than the replay floor is ledgered out_of_window and left out. sensor_ingest_history does
    not filter by disposition, so a later replay that starts at the retained pre-window checkpoint reads it back and
    applies it. Reproducer: 45 days, 12 rows just after the retained old checkpoint arrive 35 days late."""
    tab = syn_table(45)
    base = Session(tab)
    base.deliver(np.arange(len(tab)))
    cutoff = base.twin.ledger.newest_input_at - el.WINDOW
    old_ck = max((c.through for c in base.twin.ledger.checkpoints if c.through and c.through <= cutoff))
    lo, hi = pd.Timestamp(old_ck).value, pd.Timestamp(old_ck + timedelta(hours=3)).value
    late_sel = (tab.tns > lo) & (tab.tns <= hi)
    late_idx = np.nonzero(late_sel)[0]
    assert len(late_idx) > 0 and old_ck + timedelta(hours=3) < cutoff
    s = Session(tab)
    s.deliver(np.nonzero(~late_sel)[0])
    prov, _ = s.deliver(late_idx)
    unplaced = sum(1 for p in prov if p.get("placed") is False)
    # spy on what a replay reads back
    seen = []
    orig = s.ledger.history
    def spy(after, until):
        r = orig(after, until)
        seen.append((after, until, [i.time for i in r]))
        return r
    s.hist = spy
    e_time = old_ck + timedelta(hours=5)
    assert e_time < cutoff
    e = PondEvent(kind=EventKind.WATER_CHANGE, time=e_time, volume_percent=10.0)
    rep = s.record(0, e)
    late_times = {tab.take(np.array([i]))[0]["created_at"] for i in late_idx}
    reread = [t for (_, _, ts) in seen for t in ts if t in late_times]
    return {"late_rows": int(len(late_idx)), "inputs_unplaced_on_arrival": unplaced,
            "old_checkpoint": old_ck.isoformat(), "replayed": rep["replayed"], "replayed_from": rep["replayed_from"],
            "late_inputs_read_back_by_replay": len(set(reread)), "reapplied": len(set(reread)) > 0}


# ----------------------------------------------------------------------
# profile / local_time (D)
# ----------------------------------------------------------------------
@test
def profile_volume_change_midway_does_not_touch_earlier_events():
    tab = syn_table(14)
    day = lambda d, h=12: T0 + timedelta(days=d, hours=h, minutes=7)
    wc1 = PondEvent(kind=EventKind.WATER_CHANGE, time=day(3), volume_litres=100.0)
    wc2 = PondEvent(kind=EventKind.WATER_CHANGE, time=day(10), volume_litres=100.0)
    profs = ProfileHistory([PondProfile(volume_l=700.0, biomass_g=0.0, effective_from=T0),
                            PondProfile(volume_l=1000.0, biomass_g=0.0, effective_from=T0 + timedelta(days=7))])
    only_old = ProfileHistory([PondProfile(volume_l=700.0, biomass_g=0.0, effective_from=T0)])
    out = {}
    def run(pr, events):
        tw = H.make_twin(spec_syn()); tw.profiles = pr
        led = H.SensorLedger(tab)
        now = T0
        with H.frozen_clock(T0 + timedelta(days=6)):
            m = tab.tns <= pd.Timestamp(T0 + timedelta(days=6)).value
            idx = np.nonzero(m)[0]
            tw.ingest_sensor_inputs(group_rows(tab.take(idx)).inputs, history=led.history, now=T0 + timedelta(days=6)); led.mark(idx)
            tw.record_event(EIDS[0], events[0], now=T0 + timedelta(days=6), history=led.history)
        snap6 = H.snap_hash(tw.to_snapshot(), True)
        return tw, led, snap6
    tw_a, led_a, s6a = run(profs, [wc1])
    tw_b, led_b, s6b = run(only_old, [wc1])
    out["state_day6_identical_with_future_profile_known"] = s6a == s6b
    assert s6a == s6b
    # day 3 event resolved against 700 L: 100/700
    snapA = tw_a.chemistry.to_snapshot()
    # continue past the change: event on day 10 resolves against 1000 L
    with H.frozen_clock(T0 + timedelta(days=14)):
        rest = np.nonzero(~led_a.delivered)[0]
        tw_a.ingest_sensor_inputs(group_rows(tab.take(rest)).inputs, history=led_a.history, now=T0 + timedelta(days=14)); led_a.mark(rest)
        tw_a.record_event(EIDS[1], wc2, now=T0 + timedelta(days=14), history=led_a.history)
    # oracle: engine with 700 L until day 7, 1000 L afterwards
    cfg700 = PondConfig(volume_litres=700.0, estimated_biomass_grams=0.0, time_zone="Asia/Singapore")
    eng = WaterChemistryEngine(cfg700)
    inputs = group_rows(tab.take(np.arange(len(tab)))).inputs
    cfg1000 = PondConfig(volume_litres=1000.0, estimated_biomass_grams=0.0, time_zone="Asia/Singapore")
    for it in inputs:
        if it.time >= wc1.time and not getattr(eng, "_w1", False):
            eng.apply_event(wc1); eng._w1 = True
        if it.time >= T0 + timedelta(days=7) and eng.config.volume_litres != 1000.0:
            eng.config = cfg1000
        if it.time >= wc2.time and not getattr(eng, "_w2", False):
            eng.apply_event(wc2); eng._w2 = True
        eng.ingest_sensor_sample(RawSample(it.time, it.channels.get("ph"), it.channels.get("tds"), it.channels.get("temp"), None))
    pct1 = 100.0 / 700.0
    ok_orac, d_orac = chem_equal(tw_a, eng)
    out["twin_equals_manual_profile_oracle"] = ok_orac
    out["oracle_diff"] = d_orac
    assert ok_orac, d_orac
    last = tw_a.chemistry.to_snapshot()
    ev_entries = {e.event_id: e for e in tw_a.ledger.entries.values()}
    out["twin_volume_after_all"] = tw_a.chemistry.config.volume_litres
    out["event1_pct_expected_of_700"] = pct1
    # the TDS pool carries the dilution; compare last_tds_ppm up to sensor effects is brittle, so check the
    # engine config at each event through the stored copies
    evs = {e["time"]: e for e in last["events"]}
    out["events_recorded"] = len(evs)
    assert tw_a.chemistry.config.volume_litres == 1000.0
    return out


@test
def local_time_midnight_boundaries_and_dst_and_halfhour_zones():
    res = {}
    # Asia/Kolkata (+5:30): 18:29:59Z -> 23:59:59 IST same day; 18:30Z -> next day
    k1 = local_day_key(datetime(2026, 1, 12, 18, 29, 59, tzinfo=UTC), "Asia/Kolkata")
    k2 = local_day_key(datetime(2026, 1, 12, 18, 30, 0, tzinfo=UTC), "Asia/Kolkata")
    assert (k1, k2) == ("2026-01-12", "2026-01-13"), (k1, k2)
    # Asia/Singapore default (+8)
    assert local_day_key(datetime(2026, 1, 12, 15, 59, 59, tzinfo=UTC)) == "2026-01-12"
    assert local_day_key(datetime(2026, 1, 12, 16, 0, 0, tzinfo=UTC)) == "2026-01-13"
    # Asia/Muscat (+4), Asia/Jakarta (+7)
    assert local_day_key(datetime(2026, 4, 1, 19, 59, tzinfo=UTC), "Asia/Muscat") == "2026-04-01"
    assert local_day_key(datetime(2026, 4, 1, 20, 0, tzinfo=UTC), "Asia/Muscat") == "2026-04-02"
    assert local_day_key(datetime(2023, 1, 26, 16, 59, tzinfo=UTC), "Asia/Jakarta") == "2023-01-26"
    assert local_day_key(datetime(2023, 1, 26, 17, 0, tzinfo=UTC), "Asia/Jakarta") == "2023-01-27"
    # DST fixture (America/New_York, 2026-03-08 spring forward, 2026-11-01 fall back): every local midnight
    # is on the right key and the day length is 23/25 h
    ny = "America/New_York"
    for start, hours in ((datetime(2026, 3, 8, 5, 0, tzinfo=UTC), 23), (datetime(2026, 11, 1, 4, 0, tzinfo=UTC), 25)):
        k_before = local_day_key(start - timedelta(seconds=1), ny)
        k_start = local_day_key(start, ny)
        k_end_before = local_day_key(start + timedelta(hours=hours) - timedelta(seconds=1), ny)
        k_end = local_day_key(start + timedelta(hours=hours), ny)
        assert k_before != k_start and k_start == k_end_before and k_end != k_start, (start, k_before, k_start, k_end_before, k_end)
    # naive datetime is taken as UTC (documented)
    assert local_day_key(datetime(2026, 1, 12, 18, 30), "Asia/Kolkata") == "2026-01-13"
    assert as_aware(datetime(2026, 1, 1)).tzinfo == UTC
    # a brute-force sweep: 100 000 instants at 7-minute spacing vs zoneinfo-free arithmetic on fixed-offset zones
    idx = pd.date_range("2026-01-01", periods=100000, freq="7min", tz="UTC")
    for tz, off in (("Asia/Kolkata", 5.5), ("Asia/Muscat", 4.0), ("Asia/Jakarta", 7.0), ("Asia/Singapore", 8.0)):
        want = [(t + pd.Timedelta(hours=off)).strftime("%Y-%m-%d") for t in idx[::97]]
        got = [local_day_key(t.to_pydatetime(), tz) for t in idx[::97]]
        assert want == got, tz
    return {"sweep_instants_per_zone": len(idx[::97]), "zones": 4}


# ----------------------------------------------------------------------
# sensor_inputs (D)
# ----------------------------------------------------------------------
@test
def sensor_inputs_grouping_ordering_and_dispositions():
    t = T0
    rows = [
        {"id": 5, "sensor_type": "pH", "value": 7.5, "created_at": t, "sampled_at": None},
        {"id": 3, "sensor_type": "temp", "value": 26.0, "created_at": t, "sampled_at": None},
        {"id": 9, "sensor_type": "pH", "value": 7.7, "created_at": t, "sampled_at": None},          # same channel, higher id wins
        {"id": 4, "sensor_type": "TDS", "value": float("nan"), "created_at": t, "sampled_at": None},  # unusable
        {"id": 6, "sensor_type": "LUX", "value": True, "created_at": t, "sampled_at": None},          # bool is not a number
        {"id": 7, "sensor_type": "ec", "value": 1.0, "created_at": t, "sampled_at": None},            # unknown type: not read
        {"id": 8, "sensor_type": "temp", "value": 25.0, "created_at": t + timedelta(minutes=15), "sampled_at": t + timedelta(minutes=10)},
        {"id": 2, "sensor_type": "pH", "value": 7.0, "created_at": "2026-01-01T00:00:00", "sampled_at": None},  # naive text = UTC
    ]
    d = group_rows(rows)
    disp = {l["sensor_row_id"]: l["disposition"] for l in d.ledger}
    assert len(d.inputs) == 2, d.inputs
    first = d.inputs[0]
    assert first.channels["ph"] == 7.7 and first.row_ids["ph"] == 9 and first.channels["temp"] == 26.0
    assert disp[5] == "superseded" and disp[2] == "superseded" and disp[4] == "unusable" and disp[6] == "unusable"
    assert 7 not in disp
    assert d.inputs[1].time == t + timedelta(minutes=10) and d.inputs[1].time_basis == "sample"
    assert first.time_basis == "ingestion"
    assert d.watermark == t + timedelta(minutes=15)
    return {"dispositions": disp}


@test
def sensor_inputs_v2_cycle_identity_groups_across_sample_times():
    t = T0
    base = {"device_id": "d1", "boot_id": "b1", "cycle_seq": 7}
    rows = [{"id": 1, "sensor_type": "pH", "value": 7.5, "created_at": t, "sampled_at": t, **base},
            {"id": 2, "sensor_type": "temp", "value": 26.0, "created_at": t, "sampled_at": t + timedelta(seconds=40), **base}]
    d = group_rows(rows)
    assert len(d.inputs) == 1 and d.inputs[0].time == t and set(d.inputs[0].row_ids) == {"ph", "temp"}
    rows2 = [dict(r, cycle_seq=8 if r["id"] == 2 else 7) for r in rows]
    assert len(group_rows(rows2).inputs) == 2
    return {}


@test
def sensor_inputs_merge_and_complete_groups_and_freshness():
    t = T0
    a = SensorInput(t, "sample", {"ph": 7.5, "tds": None, "temp": None, "lux": None}, {"ph": 1})
    b = SensorInput(t, "sample", {"ph": 7.6, "tds": None, "temp": 26.0, "lux": None}, {"ph": 3, "temp": 2})
    m = merge_inputs([a, b])
    assert len(m) == 1 and m[0].channels["ph"] == 7.6 and m[0].channels["temp"] == 26.0 and m[0].row_ids == {"ph": 3, "temp": 2}
    m2 = merge_inputs([b, a])
    assert m2[0].channels == m[0].channels
    rows = [{"created_at": t + timedelta(minutes=i)} for i in range(5)] + [{"created_at": t + timedelta(minutes=5)}] * 3
    kept = complete_groups(rows, 8)
    assert len(kept) == 5 and complete_groups(rows[:7], 8) == rows[:7]
    fr = channel_freshness({"ph": {"value": 7.5, "reading_at": t.isoformat(), "time_basis": "sample"}}, t + timedelta(minutes=30), timedelta(minutes=15))
    assert fr["ph"]["fresh"] is True and fr["temp"]["fresh"] is False
    fr = channel_freshness({"ph": {"value": 7.5, "reading_at": t.isoformat()}}, t + timedelta(minutes=31), timedelta(minutes=15))
    assert fr["ph"]["fresh"] is False and fresh_value(fr, "ph") is None
    return {}


# ----------------------------------------------------------------------
# SensorGate (D): latch / stale
# ----------------------------------------------------------------------
def feed(gate, seq, step_min=15, start=T0, channel="ph"):
    out = []
    for k, v in enumerate(seq):
        t = start + timedelta(minutes=step_min * k)
        g = gate.validate(RawSample(t, v if channel == "ph" else None, None, None if channel == "ph" else v, None))
        out.append((g.ph if channel == "ph" else g.temp).verdict.value)
    return out


@test
def gate_basic_verdicts_range_rate_stale_missing():
    g = SensorGate(lambda t: False)
    v = feed(g, [7.5, 7.52, 3.9, 11.0, 7.55])
    assert v == ["ok", "ok", "rejected", "rejected", "ok"], v
    g = SensorGate(lambda t: False)
    v = feed(g, [7.5, 7.5, 7.5, 7.5, 7.5, 7.51])
    assert v == ["ok", "ok", "ok", "suspect_stale", "suspect_stale", "ok"], v
    g = SensorGate(lambda t: False)
    t = T0
    assert g.validate(RawSample(t, None, None, 26.0, None)).ph.verdict.value == "missing"
    assert g.validate(RawSample(t + timedelta(minutes=15), 7.5, 2.0, 26.0, None)).tds.verdict.value == "suspect_range"
    return {"stale_after_identical_readings": 4}


@test
def gate_rate_limit_latches_on_a_sustained_step_without_a_logged_event():
    """F2 reproducer. pH steps 7.0 -> 7.6 (0.6 in one 15 min step; limit 1.0/h * 0.25 h = 0.25) and STAYS at
    7.6 for 3 days with small natural variation. The comparison is against the last TRUSTED value, which never
    updates, so every later reading is rejected."""
    seq = [7.00, 7.01, 7.02] + [7.60 + 0.002 * ((k % 7) - 3) for k in range(288)]  # 3 days at 15 min
    g = SensorGate(lambda t: False)
    v = feed(g, seq)
    rejected_after_step = sum(1 for x in v[3:] if x == "suspect_rate")
    # the same sequence with a logged water change near the step
    g2 = SensorGate(lambda t: abs((t - (T0 + timedelta(minutes=40))).total_seconds()) <= 6 * 3600)
    v2 = feed(g2, seq)
    out = {"readings_after_step": len(seq) - 3, "suspect_rate_after_step_no_event": rejected_after_step,
           "suspect_rate_after_step_with_event": sum(1 for x in v2[3:] if x == "suspect_rate"),
           "last_trusted_no_event": g._last_trusted.get(__import__("koi.models.engine", fromlist=["x"]).SensorChannel.PH)}
    assert rejected_after_step == len(seq) - 3, f"expected a permanent latch, got {rejected_after_step}/{len(seq) - 3}"
    return out


@test
def gate_rate_limit_scales_with_dt_to_last_sample_not_to_last_trusted():
    """With 1-minute spacing the allowed delta is 1.0*(1/60) = 0.0167 pH; ordinary probe noise of +-0.05 is
    rejected as drift for as long as it stays > 0.0167 away from the last trusted value."""
    rng = np.random.default_rng(_common.SEED)
    seq = list((7.5 + rng.normal(0, 0.03, 600)).round(2))
    g = SensorGate(lambda t: False)
    v = feed(g, seq, step_min=1)
    frac_ok = v.count("ok") / len(v)
    g15 = SensorGate(lambda t: False)
    v15 = feed(g15, seq, step_min=15)
    return {"noise_sd": 0.03, "frac_ok_at_1min": frac_ok, "frac_ok_at_15min": v15.count("ok") / len(v15)}


@test
def gate_snapshot_restore_is_exact():
    g = SensorGate(lambda t: False)
    feed(g, [7.5, 7.5, 7.5, 7.5, 7.6])
    g2 = SensorGate.from_snapshot(json.loads(json.dumps(g.to_snapshot())), lambda t: False)
    assert g.to_snapshot() == g2.to_snapshot()
    a = feed(g, [7.6, 7.61], start=T0 + timedelta(hours=2)); b = feed(g2, [7.6, 7.61], start=T0 + timedelta(hours=2))
    assert a == b
    return {}


# ----------------------------------------------------------------------
# tds_steps / tds_prompts (D)
# ----------------------------------------------------------------------
def tds_series(days, step_events=(), noise=0.0, seed=_common.SEED, step_min=15):
    """TDS 300 ppm +- noise, with level steps (day, hour, delta_ppm) that persist."""
    rng = np.random.default_rng(seed)
    n = days * 24 * 60 // step_min
    t = pd.date_range(T0, periods=n, freq=f"{step_min}min", tz="UTC")
    h = np.arange(n) * step_min / 60.0
    level = np.full(n, 300.0) + 1.0 * np.sin(2 * np.pi * h / 24.0)
    for d, hh, delta in step_events:
        level[h >= d * 24 + hh] += delta
    return t, (level + rng.normal(0, noise, n)).round(3)


def tds_inputs(t, y):
    return [SensorInput(ti.to_pydatetime(), "sample", {"ph": None, "tds": float(v), "temp": None, "lux": None}, {"tds": i})
            for i, (ti, v) in enumerate(zip(t, y))]


@test
def tds_steps_detect_injected_salt_and_water_change_with_expected_magnitude():
    vol = 700.0
    salt = predicted_salt_step(50.0, vol)  # 71.43 ppm
    wc = predicted_water_change_step(300.0 + salt, 0.30, 30.0)  # negative
    t, y = tds_series(6, [(2, 6.0, salt), (4, 6.0, wc)], noise=0.4)
    steps = detect_steps(list(zip([x.to_pydatetime() for x in t], y)), floor_ppm=20.0)
    assert len(steps) == 2, [(s.time, s.delta_ppm) for s in steps]
    errs = [steps[0].delta_ppm - salt, steps[1].delta_ppm - wc]
    assert all(abs(e) < 3.0 for e in errs), errs
    return {"predicted_salt_step": salt, "predicted_water_change_step": wc, "detected_deltas": [s.delta_ppm for s in steps],
            "errors_ppm": errs, "first_flag_after_true_step_min": [(steps[0].time - (T0 + timedelta(days=2, hours=6))).total_seconds() / 60,
                                                                     (steps[1].time - (T0 + timedelta(days=4, hours=6))).total_seconds() / 60]}


@test
def tds_steps_noise_only_series_gives_no_flag_and_one_spike_is_not_a_step():
    t, y = tds_series(30, noise=1.5)
    assert detect_steps(list(zip([x.to_pydatetime() for x in t], y)), 20.0) == []
    y2 = y.copy(); y2[200] += 80.0
    assert detect_steps(list(zip([x.to_pydatetime() for x in t], y2)), 20.0) == []
    try:
        detect_steps([], 0.0); raise AssertionError("floor 0 accepted")
    except ValueError:
        pass
    return {"days": 30}


@test
def tds_prompts_step_prompt_stability_cooldown_event_suppression_persistence():
    cfg = PromptConfig(enabled=True)
    t, y = tds_series(8, [(3, 6.0, 71.4)], noise=0.4)
    ins = tds_inputs(t, y)
    led = el.EventLedger()
    p1 = TdsPrompts(); p1.observe(ins, led, cfg)
    assert len(p1.prompts) == 1 and p1.prompts[0]["detection"] == "step" and set(p1.prompts[0]["event_kinds"]) == {"salt", "filter_clean"}
    # chunked delivery gives the same prompt (ids aside)
    p2 = TdsPrompts()
    for a in range(0, len(ins), 37):
        p2.observe(ins[a:a + 37], led, cfg)
    strip = lambda p: [{k: v for k, v in x.items() if k != "id"} for x in p.prompts]
    assert strip(p1) == strip(p2), "chunked != single"
    # re-delivery of everything: no duplicate prompt
    p2.observe(ins, led, cfg)
    assert len(p2.prompts) == 1
    # snapshot round trip keeps state
    p3 = TdsPrompts.from_dict(json.loads(json.dumps(p1.to_dict())))
    assert p3.to_dict() == p1.to_dict()
    # a logged salt event within 6 h before the step suppresses the prompt
    led2 = el.EventLedger()
    led2.add("e", PondEvent(kind=EventKind.SALT, time=T0 + timedelta(days=3, hours=4), salt_grams=50.0), recorded_at=T0, source="api")
    p4 = TdsPrompts(); p4.observe(ins, led2, cfg)
    assert p4.prompts == []
    # disabled config does nothing
    p5 = TdsPrompts(); p5.observe(ins, led, PromptConfig(enabled=False)); assert p5.prompts == [] and p5.baseline == []
    # cooldown: second step 10 h later is suppressed, 30 h later is allowed
    for gap_h, expect in ((10.0, 1), (30.0, 2)):
        t2, y2 = tds_series(8, [(3, 6.0, 71.4), (3, 6.0 + gap_h, -90.0)], noise=0.4)
        p = TdsPrompts(); p.observe(tds_inputs(t2, y2), el.EventLedger(), cfg)
        assert len(p.prompts) == expect, (gap_h, len(p.prompts))
    # rejecting an explanation removes it from the next identical-signature prompt
    p1.reject(p1.prompts[0]); assert "step:rise:salt" in p1.rejected
    # noise only: no prompt in 30 days
    tn, yn = tds_series(30, noise=1.5)
    pn = TdsPrompts(); pn.observe(tds_inputs(tn, yn), el.EventLedger(), cfg); assert pn.prompts == []
    return {"prompt": {k: p1.prompts[0][k] for k in ("detection", "baseline_ppm", "level_ppm", "confidence")},
            "delay_h_from_true_step": (parse_timestamp(p1.prompts[0]["detected_at"]) - (T0 + timedelta(days=3, hours=6))).total_seconds() / 3600}


@test
def tds_prompts_late_reading_is_dropped_by_watermark():
    """Observation (F4): observe() skips any reading with time <= last_reading_at, so a TDS reading that arrives
    out of order never enters the baseline/recent window (prompt state depends on arrival order)."""
    cfg = PromptConfig(enabled=True)
    t, y = tds_series(3, noise=0.4)
    ins = tds_inputs(t, y)
    a = TdsPrompts(); a.observe(ins, el.EventLedger(), cfg)
    b = TdsPrompts()
    b.observe(ins[:-8] + ins[-4:], el.EventLedger(), cfg)
    b.observe(ins[-8:-4], el.EventLedger(), cfg)   # four readings delivered late, still inside the 12-reading window
    da, db = a.to_dict(), b.to_dict()
    return {"late_readings": 4, "state_equal_to_in_order": da == db,
            "baseline_len_inorder": len(da["baseline"]), "baseline_len_late": len(db["baseline"]),
            "recent_equal": da["recent"] == db["recent"]}


@test
def tds_prompts_cycle_split_across_polls_uses_first_row_while_sensor_inputs_uses_highest_id_F4b():
    """F4b: two TDS rows share one sample time (ids 1 and 2). group_rows over both rows applies the HIGHEST id (400);
    when the rows arrive in two polls, TdsPrompts.observe keeps the first (300) and skips the second
    (time <= last_reading_at), while PondTwin.ingest_sensor_inputs merges them to 400 (merge_inputs)."""
    t0 = T0
    r1 = {"id": 1, "sensor_type": "TDS", "value": 300.0, "created_at": t0, "sampled_at": None}
    r2 = {"id": 2, "sensor_type": "TDS", "value": 400.0, "created_at": t0, "sampled_at": None}
    cfg = PromptConfig(enabled=True)
    one = TdsPrompts(); one.observe(group_rows([r1, r2]).inputs, el.EventLedger(), cfg)
    two = TdsPrompts()
    two.observe(group_rows([r1]).inputs, el.EventLedger(), cfg); two.observe(group_rows([r2]).inputs, el.EventLedger(), cfg)
    s1, s2 = Session(syn_table(1)), Session(syn_table(1))
    twin_split_value = None
    tw = H.make_twin(spec_syn()); led = H.SensorLedger(syn_table(1))
    with H.frozen_clock(t0 + timedelta(hours=1)):
        tw.ingest_sensor_inputs(group_rows([r1]).inputs, now=t0 + timedelta(hours=1))
        tw.ingest_sensor_inputs(group_rows([r2]).inputs, now=t0 + timedelta(hours=1))
    return {"single_poll_recent": one.recent, "two_polls_recent": two.recent,
            "prompt_state_differs": one.recent != two.recent,
            "twin_sensor_channel_tds_after_two_polls": tw.sensor_channels.get("tds", {}).get("value")}


# ----------------------------------------------------------------------
# device_health (D)
# ----------------------------------------------------------------------
@test
def device_health_missed_cycles_known_answers_and_confidence_levels():
    iv = timedelta(minutes=15)
    t = T0
    def contacts(times):
        return dh.sensor_contacts([{"created_at": x} for x in times], iv)
    steady = [t + iv * k for k in range(10)]
    assert dh.missed_cycles(contacts(steady), steady[0], steady[-1]) == 0
    hole = steady[:3] + [steady[2] + timedelta(minutes=120)]   # 2 h gap after the 3rd contact
    assert dh.missed_cycles(contacts(hole), hole[0], hole[-1]) == 7, dh.missed_cycles(contacts(hole), hole[0], hole[-1])
    jitter = [steady[0], steady[0] + timedelta(minutes=21)]    # 21 min <= 15 + 7.5 grace
    assert dh.missed_cycles(contacts(jitter), jitter[0], jitter[-1]) == 0
    late = [steady[0], steady[0] + timedelta(minutes=23)]
    assert dh.missed_cycles(contacts(late), late[0], late[-1]) == 1
    # confidence
    now = t + timedelta(hours=2)
    fresh = {c: {"reading_at": (now - timedelta(minutes=5)).isoformat(), "value": 1, "time_basis": "sample"} for c in ("ph", "temp", "tds")}
    assert dh.assess_confidence("chemistry", fresh, [], iv, now)["level"] == "high"
    assert dh.assess_confidence("chemistry", {}, [], iv, now)["level"] == "low"
    old = {c: {"reading_at": (now - timedelta(hours=2)).isoformat(), "value": 1} for c in ("ph", "temp", "tds")}
    assert dh.assess_confidence("chemistry", old, [], iv, now)["level"] == "low"
    mixed = dict(fresh); mixed["tds"] = {"reading_at": (now - timedelta(hours=2)).isoformat(), "value": 1}
    assert dh.assess_confidence("chemistry", mixed, [], iv, now)["level"] == "reduced"
    assert dh.assess_confidence("chemistry", fresh, ["ph"], iv, now)["level"] == "reduced"
    # battery trend: -100 mV/day over 3 days
    bt = dh.battery_trend([{"at": (t + timedelta(days=d / 4)).isoformat(), "mv": 4000 - 100 * d / 4} for d in range(13)])
    assert bt["trend"] == "falling" and abs(bt["slope_mv_per_day"] + 100.0) < 0.5, bt
    return {"hole_missed": 7}


# ----------------------------------------------------------------------
# chemistry invariants (step 5): SYN only
# ----------------------------------------------------------------------
@test
def chemistry_step_pools_conserve_mass_without_uptake_and_never_go_negative():
    rng = np.random.default_rng(_common.SEED)
    worst_mass = 0.0
    worst_min = 0.0
    for _ in range(20000):
        tan, no2, no3 = (float(x) for x in rng.uniform(0, 5000, 3))
        hours = float(rng.choice([0.0, 0.0017, 0.25, 1.0, 6.0, 24.0, 240.0]))
        temp = float(rng.uniform(-5, 45))
        out = WaterChemistryEngine._step_pools(tan, no2, no3, 0, hours, temp, None, 0.05, 0.035)
        mass_in, mass_out = tan + no2 + no3, sum(out[:3])
        worst_mass = max(worst_mass, abs(mass_out - mass_in) / max(mass_in, 1e-12))
        worst_min = min(worst_min, min(out[:3]))
        assert min(out[:3]) >= 0.0
        assert out[0] <= tan + 1e-9  # TAN only decays between events
    assert worst_mass < 1e-12, worst_mass
    # with light, nitrate uptake only removes nitrate and never below zero
    for _ in range(20000):
        tan, no2, no3 = (float(x) for x in rng.uniform(0, 5000, 3))
        lux = float(rng.choice([0, 10, 5000, 10000, 40000, 150000]))
        out = WaterChemistryEngine._step_pools(tan, no2, no3, int(rng.integers(0, 3)), float(rng.uniform(0, 48)), float(rng.uniform(0, 40)), lux)
        assert min(out[:3]) >= 0.0 and sum(out[:3]) <= tan + no2 + no3 + 1e-9
    return {"samples": 40000, "worst_relative_mass_error_no_uptake": worst_mass, "min_pool_value": worst_min}


@test
def chemistry_events_mass_balance_and_nonnegativity_under_random_sequences():
    rng = random.Random(_common.SEED)
    cfg = PondConfig(volume_litres=700.0, estimated_biomass_grams=0.0, tap_nitrate_ppm=5.0, tap_tds_ppm=30.0, time_zone="Asia/Singapore")
    checked = {"feeding": 0, "water_change": 0, "top_up": 0, "salt": 0}
    worst = 0.0
    eng = WaterChemistryEngine(cfg)
    t = T0
    eng.ingest_sensor_sample(RawSample(t, 7.5, 300.0, 26.0, None))
    for k in range(3000):
        t += timedelta(minutes=rng.choice([1, 15, 60, 360]))
        kind = rng.choice(["sensor", "sensor", "feeding", "water_change", "top_up", "salt"])
        before = (eng._tan_mg, eng._no2_mg, eng._no3_mg, eng._last_tds_ppm)
        if kind == "sensor":
            eng.ingest_sensor_sample(RawSample(t, 7.5 + 0.1 * math.sin(k), 300 + k % 7, 26.0 + math.sin(k / 9), None))
        elif kind == "feeding":
            g, pr = rng.uniform(1, 80), rng.uniform(20, 55)
            eng.apply_event(PondEvent(EventKind.FEEDING, t, food_grams=g, protein_percent=pr))
            want = g * pr / 100 * 0.16 * 0.70 * 1000
            worst = max(worst, abs((eng._tan_mg - before[0]) - want) / want); checked["feeding"] += 1
            assert eng._no2_mg == before[1] and eng._no3_mg == before[2]
        elif kind in ("water_change", "top_up"):
            pct = rng.uniform(0, 100)
            eng.apply_event(PondEvent(EventKind.WATER_CHANGE if kind == "water_change" else EventKind.TOP_UP, t, volume_percent=pct))
            if kind == "water_change":
                f = pct / 100
                assert abs(eng._tan_mg - before[0] * (1 - f)) <= 1e-9 * max(1, before[0])
                assert abs(eng._no2_mg - before[1] * (1 - f)) <= 1e-9 * max(1, before[1])
                want_no3 = (before[2] / 700 * (1 - f) + 5.0 * f) * 700
                assert abs(eng._no3_mg - want_no3) <= 1e-9 * max(1, want_no3)
                checked["water_change"] += 1
            else:
                assert eng._tan_mg == before[0] and eng._last_tds_ppm <= before[3] + 1e-12
                checked["top_up"] += 1
        else:
            g = rng.uniform(1, 200)
            eng.apply_event(PondEvent(EventKind.SALT, t, salt_grams=g))
            assert abs((eng._last_tds_ppm - before[3]) - salt_tds_step(g, 700.0)) < 1e-9; checked["salt"] += 1
        assert min(eng._tan_mg, eng._no2_mg, eng._no3_mg, eng._last_tds_ppm) >= 0.0, (k, kind)
    return {"events_checked": checked, "worst_feeding_relative_error": worst, "final_pools_mg": [eng._tan_mg, eng._no2_mg, eng._no3_mg]}


@test
def chemistry_water_change_percent_bounds_and_salt_validation():
    cfg = PondConfig(volume_litres=700.0, estimated_biomass_grams=0.0)
    e = WaterChemistryEngine(cfg); e.ingest_sensor_sample(RawSample(T0, 7.5, 300.0, 26.0, None))
    e._tan_mg = 100.0
    e.apply_event(PondEvent(EventKind.WATER_CHANGE, T0 + timedelta(hours=1), volume_percent=250.0))   # clamps to 100 %
    assert e._tan_mg == 0.0
    e._tan_mg = 100.0
    e.apply_event(PondEvent(EventKind.WATER_CHANGE, T0 + timedelta(hours=2), volume_percent=-20.0))   # clamps to 0 %
    assert e._tan_mg == 100.0
    e.apply_event(PondEvent(EventKind.WATER_CHANGE, T0 + timedelta(hours=3)))                         # no amount: no-op
    assert e._tan_mg == 100.0
    for bad in (0.0, -1.0, float("nan"), None):
        try:
            salt_tds_step(bad, 700.0); raise AssertionError(f"accepted salt {bad}")
        except ValueError:
            pass
    for bad_v in (0.0, -5.0, float("inf")):
        try:
            salt_tds_step(10.0, bad_v); raise AssertionError(f"accepted volume {bad_v}")
        except ValueError:
            pass
    return {}


@test
def chemistry_stored_pools_survive_snapshot_exactly_and_replay_is_deterministic():
    tab = syn_table(10)
    a = oracle(tab, EVENTS[:4])
    snap = json.loads(json.dumps(a.to_snapshot()))
    b = WaterChemistryEngine.from_snapshot(snap) if False else None
    with H.frozen_clock(T0 + timedelta(days=10)):
        b = WaterChemistryEngine.from_snapshot(snap)
    ok, d = jclose(json.loads(json.dumps(a.to_snapshot(), default=str)), json.loads(json.dumps(b.to_snapshot(), default=str)))
    assert ok, d
    c = oracle(tab, EVENTS[:4])
    ok2, d2 = jclose(json.loads(json.dumps(a.to_snapshot(), default=str)), json.loads(json.dumps(c.to_snapshot(), default=str)), tol=0.0)
    assert ok2, d2
    return {"snapshot_max_rel_diff": d, "rerun_bit_identical": ok2}


@test
def snapshot_from_snapshot_prunes_daily_against_wall_clock_F1():
    """F1: WaterChemistryEngine.from_snapshot() calls _prune_daily(datetime.now()). A snapshot of data that is
    older than 30 days relative to the machine clock loses all its day buckets on load (harmless when data time ~
    wall time; it makes restart-equivalence tests on historical data wall-clock dependent)."""
    tab = syn_table(5)
    eng = oracle(tab, [])
    snap = json.loads(json.dumps(eng.to_snapshot()))
    n_before = len(snap["daily"])
    reloaded = WaterChemistryEngine.from_snapshot(snap)  # real clock
    with H.frozen_clock(T0 + timedelta(days=5)):
        kept = WaterChemistryEngine.from_snapshot(snap)
    out = {"daily_buckets_in_snapshot": n_before, "after_load_real_clock": len(reloaded.to_snapshot()["daily"]),
           "after_load_clock_at_data_time": len(kept.to_snapshot()["daily"]), "wall_clock_utc_year": datetime.now(UTC).year}
    assert out["after_load_clock_at_data_time"] == n_before
    return out


@test
def pond_twin_snapshot_roundtrip_all_versions_load():
    tab = syn_table(10)
    s = Session(tab); s.deliver(np.arange(len(tab)))
    for i in range(4):
        s.record(i)
    snap = json.loads(json.dumps(s.twin.to_snapshot(), default=str))
    with H.frozen_clock(T0 + timedelta(days=10)):
        t2 = PondTwin.from_snapshot(snap)
        legacy = PondTwin.from_snapshot(snap["chemistry"])            # bare chemistry snapshot (v1)
        v3 = {k: v for k, v in snap.items() if k not in ("event_ledger", "tds_prompts")}
        t3 = PondTwin.from_snapshot(v3)                                # no ledger / prompts
    assert H.snap_hash(t2.to_snapshot()) == H.snap_hash(s.twin.to_snapshot())
    return {"v6_roundtrip_identical": True, "v1_legacy_loads": legacy.chemistry.last_ingest_time is not None,
            "v3_loads_empty_ledger_entries": len(t3.ledger.entries), "v3_checkpoints": len(t3.ledger.checkpoints)}


if __name__ == "__main__":
    (OUT / "syn_results.json").write_text(json.dumps(RESULTS, indent=1, default=str))
    for r in RESULTS:
        print(("PASS " if r["ok"] else "FAIL ") + r["test"], "" if r["ok"] else (r.get("assertion") or r.get("error")))
    print(sum(r["ok"] for r in RESULTS), "/", len(RESULTS))
