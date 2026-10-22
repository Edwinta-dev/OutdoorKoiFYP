"""WP1 step 2: single-pass (poll-style) instrumented replay of every source.
Run: python -I wp1/run_step2.py   (writes wp1/out/step2/*.json and step2_summary.csv)"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import numpy as np, pandas as pd
import adapters as A, harness as H
from koi.models import device_health as dh
from koi.models.local_time import local_day_key
from koi.models.tds_steps import detect_steps
from datetime import timedelta

OUT = _common.WP1 / "out" / "step2"
OUT.mkdir(parents=True, exist_ok=True)


def health_summary(received, interval_s):
    rec = sorted(set(received))
    interval = timedelta(seconds=interval_s)
    contacts = dh.sensor_contacts([{"created_at": t} for t in rec], interval)
    missed_pair = np.zeros(max(len(rec) - 1, 0), dtype=int)
    for i in range(len(rec) - 1):
        missed_pair[i] = dh.missed_cycles(contacts[i:i + 2], rec[i], rec[i + 1])
    whole = dh.missed_cycles(contacts, rec[0], rec[-1]) if len(rec) > 1 else 0
    gaps = np.array([(rec[i + 1] - rec[i]).total_seconds() for i in range(len(rec) - 1)])
    return {"interval_s": interval_s, "contacts": len(rec), "pairs_with_missed": int((missed_pair > 0).sum()),
            "missed_total_pairwise": int(missed_pair.sum()), "missed_total_whole_window": int(whole),
            "pairwise_equals_whole": bool(missed_pair.sum() == whole),
            "gap_s_median": float(np.median(gaps)) if len(gaps) else None,
            "gap_s_p99": float(np.percentile(gaps, 99)) if len(gaps) else None,
            "gap_s_max": float(gaps.max()) if len(gaps) else None}


def local_time_check(table, tz):
    t = pd.DatetimeIndex(np.unique(table.tns), tz="UTC") if False else pd.DatetimeIndex(pd.to_datetime(np.unique(table.tns), unit="ns", utc=True))
    indep = t.tz_convert(tz).strftime("%Y-%m-%d")
    prod = [local_day_key(x.to_pydatetime(), tz) for x in t]
    mism = int(sum(a != b for a, b in zip(indep, prod)))
    return {"unique_instants": len(t), "local_day_key_mismatches_vs_pandas": mism,
            "local_days": int(len(set(prod))), "monotone": bool(all(prod[i] <= prod[i + 1] for i in range(len(prod) - 1)))}


def tds_summary(series):
    out = {"trusted_tds_readings": len(series)}
    if len(series) < 13:
        return out
    for floor in (10.0, 20.0, 40.0):
        out[f"steps_floor{int(floor)}"] = len(detect_steps(series, floor))
    thin, last = [], None
    for t, v in series:  # first reading in each 15-min bin: restores the 3 h baseline the detector assumes
        b = int(t.timestamp() // 900)
        if b != last:
            thin.append((t, v)); last = b
    out["thinned_15min_readings"] = len(thin)
    for floor in (10.0, 20.0, 40.0):
        out[f"steps_thinned15_floor{int(floor)}"] = len(detect_steps(thin, floor)) if len(thin) > 13 else None
    return out


def one(spec, events=None, tag=""):
    t0 = time.perf_counter()
    tab = spec.rows.sorted_by_time()
    batches = H.prod_batches(tab)
    r = H.run_replay(spec, tab, batches, events=events)
    twin = r.pop("_twin"); series = r.pop("_tds_series"); rec = r.pop("_received"); vl = r.pop("_verdicts")
    r["tag"] = tag
    (OUT / "verdicts").mkdir(exist_ok=True)
    np.savez_compressed(OUT / "verdicts" / (spec.name.replace(":", "_") + tag + ".npz"), **vl)
    r["source"] = {"time_zone": spec.time_zone, "tz_status": spec.tz_status, "volume_l": spec.volume_l,
                   "volume_source": spec.volume_source, "cadence_s_assumed": spec.cadence_s,
                   **{k: v for k, v in spec.meta.items()}}
    r["local_time"] = local_time_check(tab, spec.time_zone)
    r["device_health"] = health_summary(rec, spec.cadence_s)
    if "TDS" in spec.meta["channels"]:
        r["tds_steps"] = tds_summary(series)
        pr = twin.tds_prompts
        r["tds_prompts"] = {"prompts": len(pr.prompts), "by_detection": pd.Series([p["detection"] for p in pr.prompts]).value_counts().to_dict() if pr.prompts else {},
                            "ids_unique": len({p["id"] for p in pr.prompts}) == len(pr.prompts)}
    snap = twin.to_snapshot()
    r["final_state"] = {"hash": H.snap_hash(snap), "hash_state": H.snap_hash(snap, True), "bytes": len(json.dumps(snap, default=str)),
                        "channels": {c: v["reading_at"] for c, v in snap["sensor_inputs"]["channels"].items()},
                        "checkpoints": len(snap["event_ledger"]["checkpoints"]), "gate_run_length": snap["chemistry"]["gate"]["run_length"]}
    r["wall_s"] = round(time.perf_counter() - t0, 2)
    (OUT / (spec.name.replace(":", "_") + tag + ".json")).write_text(json.dumps(r, indent=1, default=str))
    print(f"{spec.name}{tag}: rows={r['rows_in']} inputs={r['inputs']} wall={r['wall_s']}s exc={r['exceptions']}", flush=True)
    return r


def summary_row(r, tag=""):
    row = {"run": r["name"] + r.get("tag", ""), "rows_in": r["rows_in"], "inputs": r["inputs"], "wall_s": r["wall_s"],
           "exceptions": sum(r["exceptions"].values()), **{f"disp_{k}": v for k, v in r["dispositions"].items()},
           "late": r["provenance"].get("late", 0), "unplaced": r["provenance"].get("unplaced", 0),
           "snap_rt_ok": r["snapshots"]["roundtrip_identical"], "snap_n": r["snapshots"]["count"],
           "snap_max_kB": round(r["snapshots"]["max_bytes"] / 1024, 1),
           "gate==twin_gate": r.get("gate_matches_twin_gate"),
           "contacts": r["device_health"]["contacts"], "hc_pairs_missed": r["device_health"]["pairs_with_missed"],
           "dayKey_mismatch": r["local_time"]["local_day_key_mismatches_vs_pandas"]}
    for c in ("ph", "temp", "tds"):
        u = r["usable"][c]
        row[f"{c}_present"] = round(u["present_fraction"], 4)
        row[f"{c}_trusted_of_present"] = None if u["trusted_fraction_of_present"] is None else round(u["trusted_fraction_of_present"], 4)
        for v, n in r["gate"][c]["verdicts"].items():
            if v != "missing":
                row[f"{c}_{v}"] = n
    for k, v in r["runtime_s"].items():
        row["t_" + k.split(".")[-1]] = v
    return row


if __name__ == "__main__":
    rows = []
    which = sys.argv[1:] or ["fwi", "fwi0", "oman", "idn"]
    if "fwi" in which:
        for p in A.fwi_ponds():
            rows.append(summary_row(one(A.fwi_source(p, "keep"))))
    if "fwi0" in which:
        for p in A.fwi_ponds():
            rows.append(summary_row(one(A.fwi_source(p, "absent"))))
    if "oman" in which:
        for w in ("open", "tank"):
            for g in ("native", "5min"):
                rows.append(summary_row(one(A.oman_source(w, g))))
        ev = A.oman_tank_events()
        rows.append(summary_row(one(A.oman_source("tank", "5min"), events=ev, tag="+events")))
        rows.append(summary_row(one(A.oman_source("tank", "native"), events=ev, tag="+events")))
    if "thin" in which:
        for w in ("clean", "raw"):
            rows.append(summary_row(one(A.thinned(A.idn_source(w, "jakarta"), 900))))
        for w in ("open", "tank"):
            rows.append(summary_row(one(A.thinned(A.oman_source(w, "native"), 900))))
            rows.append(summary_row(one(A.thinned(A.oman_source(w, "native"), 300))))
    if "idn" in which:
        for w in ("clean", "raw"):
            for tz in ("jakarta", "utc"):
                rows.append(summary_row(one(A.idn_source(w, tz))))
    pd.DataFrame(rows).to_csv(OUT / ("step2_summary_" + "_".join(which) + ".csv"), index=False)
