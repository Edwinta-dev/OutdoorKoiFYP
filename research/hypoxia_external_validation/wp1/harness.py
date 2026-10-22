"""WP1 steps 2-3: instrumented poll-style replay of production code (read-only import).

The replay mirrors koi/worker/poller.py's per-cycle order:
    scan rows (limit, complete_groups) -> group_rows -> reconcile_events ->
    PondTwin.ingest_sensor_inputs(history=...) -> mark rows ledgered ->
    TdsPrompts.observe
plus side components run on the same inputs: a standalone SensorGate (per-channel
verdict counts), tds_steps.detect_steps, device_health, local_time.
Nothing here edits Backend; no storage/network is used (the sensor ledger and
event rows are in-memory arrays/lists).
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import _common  # noqa: F401
from koi.models import device_health as dh
from koi.models.engine import PondConfig, RawSample, SensorChannel, SensorGate
from koi.models.local_time import local_day_key
from koi.models.pond_twin import PondTwin
from koi.models.profile import PondProfile, ProfileHistory
from koi.models.sensor_inputs import (APPLIED, CHANNELS, OUT_OF_WINDOW, SUPERSEDED, UNUSABLE, complete_groups,
                                      group_rows)
from koi.models.tds_prompts import PromptConfig
from koi.models.tds_steps import detect_steps

UTC = timezone.utc
# Needed only to construct PondConfig/PondProfile (volume must be > 0): NOT a
# measurement. Gate, ingestion, prompts and health never read it; chemistry
# outputs on sources with an unknown volume are excluded from every score.
PLACEHOLDER_VOLUME_L = 1000.0
PLACEHOLDER_BIOMASS_G = 0.0
BATCH_ROWS = 2000  # IngestConfig.batch_rows default


class frozen_clock:
    """Replay-harness aid, not a Backend edit: while active, `datetime.now()` inside
    koi.models.engine and koi.models.pond_twin returns `at`. Needed because
    WaterChemistryEngine.from_snapshot prunes daily buckets against the WALL CLOCK
    (finding F1); replaying 2023-2026 data with the real clock would otherwise
    discard history on every restore."""

    def __init__(self, at):
        self.at = at

    def __enter__(self):
        import koi.models.engine as E
        import koi.models.pond_twin as P
        at = self.at

        class _D(datetime):
            @classmethod
            def now(cls, tz=None):
                return at if tz is None or at.tzinfo else at.replace(tzinfo=tz)
        self._saved = (E.datetime, P.datetime)
        E.datetime = _D
        P.datetime = _D

    def __exit__(self, *a):
        import koi.models.engine as E
        import koi.models.pond_twin as P
        E.datetime, P.datetime = self._saved


def dt_of_ns(ns: int) -> datetime:
    return pd.Timestamp(int(ns), unit="ns", tz="UTC").to_pydatetime()


def make_twin(spec, volume_l_override=None) -> PondTwin:
    vol = volume_l_override or spec.volume_l or PLACEHOLDER_VOLUME_L
    cfg = PondConfig(volume_litres=vol, estimated_biomass_grams=PLACEHOLDER_BIOMASS_G, time_zone=spec.time_zone)
    twin = PondTwin.create(cfg)
    if spec.volume_l is not None:  # only documented profiles are attached
        twin.profiles = ProfileHistory([PondProfile(volume_l=spec.volume_l, biomass_g=PLACEHOLDER_BIOMASS_G)])
    return twin


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ----------------------------------------------------------------------
# Delivery plans: lists of index arrays into a RowTable
# ----------------------------------------------------------------------
def prod_batches(table, limit: int = BATCH_ROWS) -> list:
    """The poller's scans over a time-ordered table: `limit` rows, minus the rows
    of the last insert time when the scan hit the limit (complete_groups). The
    prefix rule is the numpy form of complete_groups (tested against it)."""
    n, i, out = len(table), 0, []
    while i < n:
        idx = np.arange(i, min(i + limit, n))
        if len(idx) >= limit:
            t = table.tns[idx]
            k = int(np.searchsorted(t, t[-1], side="left"))
            keep = idx[:k] if k > 0 else idx
        else:
            keep = idx
        out.append(keep)
        i += len(keep)
    return out


def fixed_batches(n: int, size: int) -> list:
    return [np.arange(a, min(a + size, n)) for a in range(0, n, size)]


# ----------------------------------------------------------------------
# State fingerprints
# ----------------------------------------------------------------------
def _scrub(obj, drop_recorded=False):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "saved_at":
                continue
            if drop_recorded and k in ("recorded_at",):
                continue
            out[k] = _scrub(v, drop_recorded)
        if "prompts" in out and isinstance(out["prompts"], list):
            out["prompts"] = [{**p, "id": "<uuid4>"} for p in out["prompts"]]
        return out
    if isinstance(obj, list):
        return [_scrub(v, drop_recorded) for v in obj]
    return obj


def canon(snapshot: dict, drop_recorded=False) -> str:
    s = json.loads(json.dumps(snapshot, default=str))
    return json.dumps(_scrub(s, drop_recorded), sort_keys=True)


def snap_hash(snapshot: dict, drop_recorded=False) -> str:
    return hashlib.sha256(canon(snapshot, drop_recorded).encode()).hexdigest()[:16]


def diff_paths(a, b, path="", out=None, limit=40):
    """Paths where two canonical JSON values differ."""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if type(a) != type(b):
        out.append((path, repr(a)[:60], repr(b)[:60]))
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((f"{path}/{k}", "<absent>" if k not in a else "<present>", "<absent>" if k not in b else "<present>"))
            else:
                diff_paths(a[k], b[k], f"{path}/{k}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append((path + "[len]", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            diff_paths(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append((path, repr(a)[:60], repr(b)[:60]))
    return out


def effective_times(twin: PondTwin, ledger_store=None) -> dict:
    """The effective (model) timestamps a state carries."""
    snap = twin.to_snapshot()
    return {
        "last_input_at": snap["sensor_inputs"]["last_input_at"],
        "chemistry_last_ingest": snap["chemistry"]["last_ingest_time"],
        "newest_input_at": snap["event_ledger"]["newest_input_at"],
        "channels": {c: v["reading_at"] for c, v in snap["sensor_inputs"]["channels"].items()},
        "event_models": {e["event_id"]: e["model_time"] for e in snap["event_ledger"]["entries"]},
        "checkpoint_through": [c["through"] for c in snap["event_ledger"]["checkpoints"]],
    }


def event_ids(twin: PondTwin) -> dict:
    snap = twin.to_snapshot()
    return {e["event_id"]: e["status"] for e in snap["event_ledger"]["entries"]}


# ----------------------------------------------------------------------
# Sensor ledger (in memory) -- the rows PondTwin may read back for replays
# ----------------------------------------------------------------------
class SensorLedger:
    """Mimics sensor_ingest_history: ledgered rows with effective time in
    (after, until]. Every row of a delivered batch is ledgered (applied,
    superseded, unusable or out_of_window), as in storage.memory."""

    def __init__(self, table):
        self.table = table
        self.delivered = np.zeros(len(table), dtype=bool)

    def mark(self, idx):
        self.delivered[np.asarray(idx)] = True

    def history(self, after, until):
        t = self.table.tns
        m = self.delivered & (t <= pd.Timestamp(until).value)
        if after is not None:
            m &= t > pd.Timestamp(after).value
        return group_rows(self.table.take(np.nonzero(m)[0])).inputs


# ----------------------------------------------------------------------
# One instrumented replay
# ----------------------------------------------------------------------
class Timer:
    def __init__(self):
        self.s = defaultdict(float)

    def __call__(self, name):
        outer = self

        class _C:
            def __enter__(self_):
                self_.t = time.perf_counter()

            def __exit__(self_, *a):
                outer.s[name] += time.perf_counter() - self_.t
        return _C()


def run_replay(spec, table, batches, *, events=None, prompts=True, history_on=True, snapshot_daily=True,
               restart_at_batch=None, health=True, stats=True, volume_l_override=None, keep_twin=True,
               max_exceptions=5, freeze_clock=True, on_batch=None):
    """Poll-style replay of `table` delivered as `batches` (index arrays).
    Returns a dict of counters plus the final twin."""
    twin = make_twin(spec, volume_l_override)
    ledger = SensorLedger(table)
    hist = ledger.history if history_on else None
    tm = Timer()
    exc = Counter()
    exc_samples: list = []
    disp = Counter()
    rows_in = 0
    unknown_rows = 0
    n_inputs = 0
    prov = Counter()
    verdict = {c: Counter() for c in CHANNELS}
    present = Counter()
    visible_events: list = []  # interventions logged by `now`: the standalone gate's volume-event predicate

    def vol_event_near(t):
        return any(e["event_type"] in ("WATER_CHANGE", "WATER_TOPUP", "SALT")
                   and abs((t - e["event_timestamp"]).total_seconds()) <= 6 * 3600 for e in visible_events)

    gate = SensorGate(vol_event_near) if stats else None
    VCODE = {"ok": 0, "suspect_stale": 1, "suspect_range": 2, "suspect_rate": 3, "rejected": 4, "missing": 5}
    vlog = {"t": [], **{c: [] for c in CHANNELS}}
    tds_series: list = []
    prompt_cfg = PromptConfig(enabled=True) if prompts else PromptConfig(enabled=False)
    snaps = []
    snap_roundtrip_ok = 0
    snap_roundtrip_bad = []
    conf = {d: Counter() for d in dh.DOMAIN_CHANNELS}
    all_received: list = []
    now = None
    last_day = None
    ev_applied_report: list = []
    events = events or []
    batch_times = []
    out_of_order_inputs = 0
    unplaced_ids: set = set()
    unplaced_ns: set = set()
    max_input_time = None
    cadence = timedelta(seconds=spec.cadence_s)
    n_batches = 0

    def guard(name, fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001 - we count every production exception
            exc[name] += 1
            if len(exc_samples) < max_exceptions:
                exc_samples.append({"component": name, "type": type(e).__name__, "msg": str(e)[:200],
                                    "tb": traceback.format_exc()[-600:]})
            return None

    for bi, idx in enumerate(batches):
        n_batches += 1
        rows = table.take(idx)
        rows_in += len(rows)
        bmax = max(r["created_at"] for r in rows) if rows else now
        now = bmax if now is None else max(now, bmax)
        fc = frozen_clock(now) if freeze_clock else _null()  # production passes wall-clock `now`; so does the replay
        fc.__enter__()
        with tm("sensor_inputs.group_rows"):
            disc = guard("sensor_inputs.group_rows", group_rows, rows)
        if disc is None:
            fc.__exit__(None, None, None)
            continue
        unknown_rows += len(rows) - len(disc.ledger)
        n_inputs += len(disc.inputs)
        if health:
            all_received.extend(disc.received)
        # visible interventions (logged by now): never from the future
        if events:
            visible = [e for e in events if e["created_at"] <= now]
            visible_events[:] = visible
            with tm("pond_twin.reconcile_events"):
                rep = guard("pond_twin.reconcile_events", twin.reconcile_events, visible, now=now, history=hist)
            if rep:
                ev_applied_report.append({k: rep[k] for k in ("applied", "claimed", "revised", "deleted", "restored",
                                                              "not_replayable", "skipped", "replayed")})
        # standalone gate on the same inputs (only meaningful in time order)
        if stats:
            with tm("engine.SensorGate(standalone)"):
                for item in disc.inputs:
                    if max_input_time is not None and item.time < max_input_time:
                        out_of_order_inputs += 1
                    max_input_time = item.time if max_input_time is None else max(max_input_time, item.time)
                    g = guard("engine.SensorGate", gate.validate, RawSample(
                        item.time, item.channels.get("ph"), item.channels.get("tds"),
                        item.channels.get("temp"), item.channels.get("lux")))
                    if g is None:
                        continue
                    vlog["t"].append(pd.Timestamp(item.time).value)
                    for c in CHANNELS:
                        v = g.channels[SensorChannel(c)].verdict.value
                        vlog[c].append(VCODE[v])
                        verdict[c][v] += 1
                        if item.channels.get(c) is not None:
                            present[c] += 1
                    if item.channels.get("tds") is not None and g.tds.is_trusted:
                        tds_series.append((item.time, g.tds.value))
        with tm("pond_twin.ingest_sensor_inputs"):
            res = guard("pond_twin.ingest_sensor_inputs", twin.ingest_sensor_inputs, disc.inputs, history=hist, now=now)
        if res is not None:
            provenance, _warn = res
            unplaced = {rid for p in provenance if p.get("placed") is False for rid in p["row_ids"].values()}
            unplaced_ids |= unplaced
            unplaced_ns |= {pd.Timestamp(p["time"]).value for p in provenance if p.get("placed") is False}
            for p in provenance:
                prov["late"] += bool(p.get("late"))
                prov["replayed"] += bool(p.get("replayed"))
                prov["unplaced"] += p.get("placed") is False
                prov["inputs"] += 1
            for entry in disc.ledger:
                if entry["sensor_row_id"] in unplaced and entry["disposition"] == APPLIED:
                    entry["disposition"] = OUT_OF_WINDOW
        for entry in disc.ledger:
            disp[entry["disposition"]] += 1
        ledger.mark(idx)
        with tm("tds_prompts.observe"):
            guard("tds_prompts.observe", twin.tds_prompts.observe, disc.inputs, twin.ledger, prompt_cfg)
        if health and res is not None:
            with tm("device_health.assess_confidence"):
                gs = {"chemistry": {"gate": twin.chemistry._gate.to_snapshot()}}
                stale = dh.snapshot_stale_flags(gs)
                for dom in dh.DOMAIN_CHANNELS:
                    c = guard("device_health.assess_confidence", dh.assess_confidence, dom,
                              twin.sensor_channels, stale, cadence, now)
                    if c:
                        conf[dom][c["level"]] += 1
        # daily snapshot + restart-equivalence
        day = local_day_key(now, spec.time_zone)
        if snapshot_daily and day != last_day and res is not None:
            with tm("pond_twin.snapshot"):
                snap = guard("pond_twin.to_snapshot", twin.to_snapshot)
                if snap is not None:
                    size = len(json.dumps(snap, default=str))
                    with (frozen_clock(now) if freeze_clock else _null()):
                        again = guard("pond_twin.from_snapshot", PondTwin.from_snapshot,
                                      json.loads(json.dumps(snap, default=str)))
                    ok = again is not None and snap_hash(again.to_snapshot()) == snap_hash(snap)
                    snap_roundtrip_ok += bool(ok)
                    if not ok and len(snap_roundtrip_bad) < 3:
                        snap_roundtrip_bad.append({"day": day, "diff": diff_paths(
                            json.loads(canon(snap)), json.loads(canon(again.to_snapshot())))[:8] if again else "load failed"})
                    snaps.append({"day": day, "bytes": size})
            last_day = day
        if on_batch is not None:
            on_batch(bi, twin, now)
        fc.__exit__(None, None, None)
        if restart_at_batch is not None and bi == restart_at_batch:
            with tm("pond_twin.restart"):
                snapshot = json.loads(json.dumps(twin.to_snapshot(), default=str))
                prof = twin.profiles
                with (frozen_clock(now) if freeze_clock else _null()):
                    twin = PondTwin.from_snapshot(snapshot)
                twin.profiles = prof
                # the registry rebuilds the standalone gate state from the same snapshot
                if gate is not None:
                    gate = SensorGate.from_snapshot(gate.to_snapshot(), vol_event_near)

    result = {
        "name": spec.name, "batches": n_batches, "rows_in": rows_in, "rows_unknown_type": unknown_rows,
        "inputs": n_inputs, "dispositions": dict(disp), "provenance": dict(prov),
        "exceptions": dict(exc), "exception_samples": exc_samples,
        "runtime_s": {k: round(v, 3) for k, v in tm.s.items()},
        "snapshots": {"count": len(snaps), "max_bytes": max((s["bytes"] for s in snaps), default=0),
                      "roundtrip_identical": snap_roundtrip_ok, "roundtrip_mismatch": snap_roundtrip_bad},
        "events_reports": ev_applied_report[-3:], "events_reports_n": len(ev_applied_report),
        "out_of_order_inputs_seen_by_standalone_gate": out_of_order_inputs,
        "final_now": now.isoformat() if now else None,
    }
    if stats:
        n = max(n_inputs, 1)
        result["gate"] = {c: {"present": present[c], "verdicts": dict(verdict[c])} for c in CHANNELS}
        result["usable"] = {c: {"present_fraction": present[c] / n,
                                "trusted_fraction_of_inputs": verdict[c].get("ok", 0) / n,
                                "trusted_fraction_of_present": (verdict[c].get("ok", 0) / present[c]) if present[c] else None}
                            for c in CHANNELS}
        result["gate_matches_twin_gate"] = (gate.to_snapshot() == twin.chemistry._gate.to_snapshot()
                                           if restart_at_batch is None else None)
    if health:
        result["confidence_levels"] = {d: dict(c) for d, c in conf.items()}
    result["_verdicts"] = {k: np.asarray(v, dtype=np.int64 if k == "t" else np.int8) for k, v in vlog.items()}
    result["_unplaced_ids"] = unplaced_ids
    result["_unplaced_ns"] = unplaced_ns
    result["_tds_series"] = tds_series
    result["_received"] = all_received
    result["_twin"] = twin if keep_twin else None
    return result
