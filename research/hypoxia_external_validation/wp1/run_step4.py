"""WP1 step 4 (B-tier): agreement tables, explicitly NOT ground truth.
 4a SensorGate verdicts vs FWI per-channel QC flags (+ README-documented zero artefacts)
 4b device_health gap detection vs FWI QC_Flag_DateTime time_gap
 4c IDN-T raw vs cleaned file (cleaning filter is not an anomaly label) and tz sensitivity
 4d rate-latch / stale-run statistics from the step-2 verdict logs
 4e Oman tank events: no future events in the ledger; gate behaviour with/without logged water changes
Run after run_step2.py. Output wp1/out/step4/*.json|csv"""
import json, sys
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import numpy as np, pandas as pd
import adapters as A, harness as H
from koi.models import device_health as dh

S2 = _common.WP1 / "out" / "step2"
OUT = _common.WP1 / "out" / "step4"
OUT.mkdir(parents=True, exist_ok=True)
CODE = {0: "ok", 1: "suspect_stale", 2: "suspect_range", 3: "suspect_rate", 4: "rejected", 5: "missing"}
BAD = {2, 3, 4}  # the gate says the value is implausible (range/rate/rejected); stale = "possibly cached"


def load_v(name):
    z = np.load(S2 / "verdicts" / (name.replace(":", "_") + ".npz"))
    return {k: z[k] for k in z.files}


def fwi_gate_vs_qc():
    rows_ph, rows_zero, per_pond = [], [], []
    for pond in A.fwi_ponds():
        d = A.load_fwi_frame(pond)
        d["ns"] = pd.DatetimeIndex(d["t_local"]).tz_localize("Asia/Kolkata").tz_convert("UTC").as_unit("ns").asi8
        v = load_v(f"fwi:{pond}:zeros-keep")
        vt = pd.DataFrame({"ns": v["t"], "gate_ph": v["ph"], "gate_temp": v["temp"]})
        d["qc_ph"] = d["qc_ph"].fillna("")
        g = d.groupby("ns").agg(qc_ph=("qc_ph", lambda s: "; ".join(sorted(set(x for x in s if x)))), ph=("ph", "last"),
                                temp=("temp", "last")).reset_index()
        m = g.merge(vt, on="ns", how="inner")
        assert len(m) == len(vt) == len(g), (pond, len(m), len(vt), len(g))
        m["pond"] = pond
        m["qc"] = np.where(m["qc_ph"] == "", "none",
                           np.where(m["qc_ph"].str.contains("jump") & m["qc_ph"].str.contains("range"), "both",
                                    np.where(m["qc_ph"].str.contains("jump"), "pH_jump_>1", "pH_out_of_range")))
        rows_ph.append(m[["pond", "qc", "gate_ph", "ph"]])
        z = m[(m["ph"] == 0) | (m["temp"] == 0)].copy()
        z["art"] = np.where((z["ph"] == 0) & (z["temp"] == 0), "pH=0&temp=0", np.where(z["ph"] == 0, "pH=0", "temp=0"))
        rows_zero.append(z[["pond", "art", "gate_ph", "gate_temp", "ph", "temp", "qc"]])
        flagged = m["qc"] != "none"
        gbad = m["gate_ph"].isin(BAD)
        per_pond.append({"pond": pond, "n": len(m), "qc_flagged": int(flagged.sum()), "gate_bad": int(gbad.sum()),
                         "both": int((flagged & gbad).sum()), "qc_only": int((flagged & ~gbad).sum()),
                         "gate_only": int((~flagged & gbad).sum()), "gate_stale": int((m["gate_ph"] == 1).sum())})
    ph = pd.concat(rows_ph, ignore_index=True)
    ph["gate"] = ph["gate_ph"].map(CODE)
    tab = pd.crosstab(ph["gate"], ph["qc"], margins=True)
    tab.to_csv(OUT / "fwi_gate_ph_vs_qc_crosstab.csv")
    pp = pd.DataFrame(per_pond)
    pp.to_csv(OUT / "fwi_gate_ph_vs_qc_per_pond.csv", index=False)
    zz = pd.concat(rows_zero, ignore_index=True)
    zz["gate_ph"] = zz["gate_ph"].map(CODE)
    zz["gate_temp"] = zz["gate_temp"].map(CODE)
    zt = zz.groupby("art").agg(n=("pond", "size")).join(pd.crosstab(zz["art"], zz["gate_ph"]).add_prefix("ph_")).join(
        pd.crosstab(zz["art"], zz["gate_temp"]).add_prefix("temp_"))
    zt.to_csv(OUT / "fwi_documented_zero_artifacts_vs_gate.csv")
    zq = pd.crosstab(zz["art"], zz["qc"])
    zq.to_csv(OUT / "fwi_documented_zero_artifacts_vs_qc.csv")
    tot = pp[["n", "qc_flagged", "gate_bad", "both", "qc_only", "gate_only", "gate_stale"]].sum()
    summ = {"inputs": int(tot["n"]), "qc_flagged_pH": int(tot["qc_flagged"]), "gate_bad_pH": int(tot["gate_bad"]),
            "both": int(tot["both"]), "qc_only": int(tot["qc_only"]), "gate_only": int(tot["gate_only"]),
            "gate_stale_pH": int(tot["gate_stale"]),
            "P_gate_bad_given_qc": float(tot["both"] / tot["qc_flagged"]),
            "P_qc_given_gate_bad": float(tot["both"] / tot["gate_bad"]),
            "pH_zero_rows": int((ph["ph"] == 0).sum()),
            "pH_in_[4,6)_rows": int(((ph["ph"] >= 4) & (ph["ph"] < 6)).sum())}
    return summ, tab, zt, zq


def gaps_vs_qc():
    """Pair level: pair i = (row i-1, row i). Truth-like reference = the FWI QC time_gap flag. The flag turns out to be
    written on BOTH rows next to a gap, so a pair is 'QC gap' when both its rows carry the flag and the pair's gap is > 20 min
    (checked against timestamps). device_health: missed_cycles() of the production code on the two contacts."""
    pooled, rowinfo = [], []
    iv = timedelta(minutes=15)
    for pond in A.fwi_ponds():
        d = A.load_fwi_frame(pond)
        d["ns"] = pd.DatetimeIndex(d["t_local"]).tz_localize("Asia/Kolkata").tz_convert("UTC").as_unit("ns").asi8
        d["gapflag"] = d["qc_dt"].fillna("").str.contains("time_gap")
        g = d.groupby("ns").agg(gapflag=("gapflag", "any")).reset_index().sort_values("ns", ignore_index=True)
        ts = pd.to_datetime(g["ns"].to_numpy(), unit="ns", utc=True).to_pydatetime()
        contacts = dh.sensor_contacts([{"created_at": t} for t in ts], iv)
        n = len(ts)
        gap = np.r_[np.nan, np.diff(g["ns"].to_numpy()) / 1e9]
        missed = np.r_[np.nan, [dh.missed_cycles(contacts[i - 1:i + 1], ts[i - 1], ts[i]) for i in range(1, n)]]
        f = g["gapflag"].to_numpy()
        pair_qc_both = np.r_[False, f[1:] & f[:-1]]
        post = np.r_[False, gap[1:] > 1200]        # row i follows a >20 min gap
        pre = np.r_[gap[1:] > 1200, False]         # row i precedes a >20 min gap
        rowinfo.append(pd.DataFrame({"pond": pond, "flag": f, "post_gap": post, "pre_gap": pre}))
        pooled.append(pd.DataFrame({"pond": pond, "gap_s": gap, "dh_missed": missed, "pair_qc_both": pair_qc_both}).iloc[1:])
    df = pd.concat(pooled, ignore_index=True)
    ri = pd.concat(rowinfo, ignore_index=True)
    res = {"rows": int(len(ri)), "pairs": int(len(df))}
    res["qc_flag_placement"] = {
        "flagged_rows": int(ri.flag.sum()), "flagged_post_gap_only": int((ri.flag & ri.post_gap & ~ri.pre_gap).sum()),
        "flagged_pre_gap_only": int((ri.flag & ri.pre_gap & ~ri.post_gap).sum()),
        "flagged_both_sides": int((ri.flag & ri.pre_gap & ri.post_gap).sum()),
        "flagged_not_adjacent_to_gt20min_gap": int((ri.flag & ~ri.pre_gap & ~ri.post_gap).sum()),
        "unflagged_adjacent_to_gt20min_gap": int((~ri.flag & (ri.pre_gap | ri.post_gap)).sum())}
    gt20 = df.gap_s > 1200
    res["pairs_gap_gt20min"] = int(gt20.sum())
    res["qc_pair_vs_timestamps"] = {"pair_both_flagged_and_gap_gt20": int((df.pair_qc_both & gt20).sum()),
                                    "pair_both_flagged_but_gap_le20": int((df.pair_qc_both & ~gt20).sum()),
                                    "gap_gt20_but_not_both_flagged": int((~df.pair_qc_both & gt20).sum())}
    dhd = df.dh_missed > 0
    res["device_health_vs_qc_gap_pairs"] = {"both": int((dhd & gt20).sum()), "device_health_only": int((dhd & ~gt20).sum()),
                                            "gap_gt20_not_detected": int((~dhd & gt20).sum()), "neither": int((~dhd & ~gt20).sum())}
    res["gap_s_percentiles"] = {str(p): float(np.nanpercentile(df.gap_s, p)) for p in (50, 90, 99, 99.9)}
    res["device_health_missed_cycles_total"] = int(df.dh_missed.sum())
    expected = np.floor((df.gap_s[gt20] - 450) / 900 - 0).clip(lower=0)  # closed form: due times k*900 <= gap-450, k>=1
    res["missed_cycles_closed_form_matches"] = bool((np.floor((df.gap_s[gt20] - 450) / 900) == df.dh_missed[gt20]).mean() > 0.999)
    res["pairs_with_gap_between_15_and_22.5min(threshold zone)"] = int(((df.gap_s > 960) & (df.gap_s <= 1350)).sum())
    res["gap_hist_s"] = {"<=900+60": int((df.gap_s <= 960).sum()), "960-1200": int(((df.gap_s > 960) & (df.gap_s <= 1200)).sum()),
                         "1200-1350": int(((df.gap_s > 1200) & (df.gap_s <= 1350)).sum()), ">1350": int((df.gap_s > 1350).sum())}
    return res


def stale_vs_do():
    """B-tier consistency check for the gate's suspect_stale verdict. DO is NOT fed to any model; it is used only as an
    independent label: a monitor that is really frozen/replaying a cache would repeat DO as well. For every maximal run of
    suspect_stale inputs (pH or temperature) of the FWI replays: did DO move inside the run?"""
    rows = []
    for pond in A.fwi_ponds():
        d = A.load_fwi_frame(pond)
        d["ns"] = pd.DatetimeIndex(d["t_local"]).tz_localize("Asia/Kolkata").tz_convert("UTC").as_unit("ns").asi8
        g = d.groupby("ns").agg(do=("do", "last")).reset_index()
        v = load_v(f"fwi:{pond}:zeros-keep")
        assert (g["ns"].to_numpy() == v["t"]).all()
        do = g["do"].to_numpy()
        for ch in ("ph", "temp"):
            m = v[ch] == 1
            if not m.any():
                continue
            edge = np.diff(np.r_[0, m.astype(int), 0])
            st, en = np.nonzero(edge == 1)[0], np.nonzero(edge == -1)[0] - 1
            for s_, e_ in zip(st, en):
                lo = max(s_ - 3, 0)  # the run starts after 3 identical readings already seen
                seg = do[lo:e_ + 1]
                rows.append({"pond": pond, "channel": ch, "inputs": int(e_ - s_ + 1), "do_range": float(np.nanmax(seg) - np.nanmin(seg)),
                             "do_all_zero": bool((seg == 0).all())})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "fwi_stale_episodes_vs_do.csv", index=False)
    res = {}
    for ch in ("ph", "temp"):
        x = df[df.channel == ch]
        res[ch] = {"episodes": int(len(x)), "inputs_in_episodes": int(x.inputs.sum()),
                   "episodes_with_DO_change_ge_0.05": int((x.do_range >= 0.05).sum()),
                   "episodes_with_DO_flat(<0.005)": int((x.do_range < 0.005).sum()),
                   "share_with_DO_change": float((x.do_range >= 0.05).mean())}
    return res


def idn_raw_vs_clean():
    raw = A.load_idn_frame("raw")
    clean = A.load_idn_frame("clean")
    res = {"raw_rows": len(raw), "clean_rows": len(clean), "clean_ids_subset_of_raw": bool(clean["id"].isin(raw["id"]).all())}
    keep_ids = set(clean["id"])
    raw["minute_ns"] = pd.DatetimeIndex(raw["t_local"]).tz_localize("Asia/Jakarta").tz_convert("UTC").as_unit("ns").asi8
    win = raw.sort_values("id").groupby("minute_ns").tail(1).sort_values("minute_ns")  # row that wins the group (highest id)
    win["kept"] = win["id"].isin(keep_ids).to_numpy()
    v = load_v("idn_t:raw:tz-jakarta")
    assert len(v["t"]) == len(win) and (v["t"] == win["minute_ns"].to_numpy()).all()
    kept = win["kept"].to_numpy()
    tabs = {}
    for ch in ("ph", "tds", "temp"):
        s = pd.Series(v[ch]).map(CODE).to_numpy()
        tabs[ch] = pd.crosstab(pd.Series(s, name="gate"), pd.Series(np.where(kept, "kept_in_clean", "removed_by_clean"), name="clean"),
                               margins=True)
        tabs[ch].to_csv(OUT / f"idn_raw_gate_{ch}_vs_cleaned_membership.csv")
        bad = pd.Series(v[ch]).isin(list(BAD | {1})).to_numpy()
        res[f"{ch}_P(gate_not_ok|removed)"] = float(bad[~kept].mean()) if (~kept).any() else None
        res[f"{ch}_P(gate_not_ok|kept)"] = float(bad[kept].mean())
    vt = load_v("idn_t:raw:tz-jakarta:thin15min")
    wi = win.set_index("minute_ns").loc[vt["t"]]
    kt = wi["kept"].to_numpy()
    for ch in ("ph", "tds", "temp"):
        bad = pd.Series(vt[ch]).isin(list(BAD | {1})).to_numpy()
        res[f"thin15_{ch}_P(gate_not_ok|removed)"] = float(bad[~kt].mean()) if (~kt).any() else None
        res[f"thin15_{ch}_P(gate_not_ok|kept)"] = float(bad[kt].mean())
        ct = pd.crosstab(pd.Series(pd.Series(vt[ch]).map(CODE).to_numpy(), name="gate"),
                         pd.Series(np.where(kt, "kept_in_clean", "removed_by_clean"), name="clean"), margins=True)
        ct.to_csv(OUT / f"idn_raw_thin15_gate_{ch}_vs_cleaned_membership.csv")
    res["thin15_minutes"] = int(len(vt["t"])); res["thin15_removed"] = int((~kt).sum())
    res["winning_rows_removed_by_cleaning"] = int((~kept).sum())
    res["minutes"] = len(win)
    res["clean_ranges"] = {"pH": [float(clean.water_pH.min()), float(clean.water_pH.max())],
                           "TDS": [float(clean.TDS.min()), float(clean.TDS.max())],
                           "temp": [float(clean.water_temp.min()), float(clean.water_temp.max())]}
    res["raw_ranges"] = {"pH": [float(raw.water_pH.min()), float(raw.water_pH.max())],
                         "TDS": [float(raw.TDS.min()), float(raw.TDS.max())],
                         "temp": [float(raw.water_temp.min()), float(raw.water_temp.max())]}
    sens = {}
    for name in ("idn_t_raw_tz-jakarta", "idn_t_clean_tz-jakarta", "idn_t_raw_tz-utc", "idn_t_clean_tz-utc",
                 "idn_t_raw_tz-jakarta_thin15min", "idn_t_clean_tz-jakarta_thin15min"):
        j = json.load(open(S2 / f"{name}.json"))
        sens[name] = {"inputs": j["inputs"], "rows": j["rows_in"], "superseded_rows": j["dispositions"].get("superseded", 0),
                      "ph_trusted_of_present": j["usable"]["ph"]["trusted_fraction_of_present"],
                      "tds_trusted_of_present": j["usable"]["tds"]["trusted_fraction_of_present"],
                      "temp_trusted_of_present": j["usable"]["temp"]["trusted_fraction_of_present"],
                      "tds_steps": j.get("tds_steps"), "prompts": j.get("tds_prompts", {}).get("prompts"),
                      "local_days": j["local_time"]["local_days"], "contacts": j["device_health"]["contacts"],
                      "missed_pairs": j["device_health"]["pairs_with_missed"],
                      "gap_max_h": j["device_health"]["gap_s_max"] / 3600,
                      "confidence_chemistry": j.get("confidence_levels", {}).get("chemistry")}
    res["component_sensitivity"] = sens
    a, b = load_v("idn_t:raw:tz-jakarta"), load_v("idn_t:raw:tz-utc")
    shifts = np.unique(b["t"] - a["t"])
    res["tz_sensitivity"] = {"verdicts_identical_jakarta_vs_utc": bool(all((a[k] == b[k]).all() for k in ("ph", "tds", "temp", "lux"))),
                             "utc_minus_jakarta_time_shift_h": [float(x) / 3.6e12 for x in shifts],
                             "local_days_jakarta": sens["idn_t_raw_tz-jakarta"]["local_days"],
                             "local_days_utc": sens["idn_t_raw_tz-utc"]["local_days"]}
    return res, tabs


def latch_stats():
    rows = []
    for f in sorted((S2 / "verdicts").glob("*.npz")):
        z = np.load(f)
        t = z["t"] / 1e9
        for ch in ("ph", "temp", "tds"):
            c = z[ch]
            if (c == 5).all():
                continue
            for code, label in ((3, "rate"), (1, "stale")):
                m = c == code
                if not m.any():
                    rows.append({"run": f.stem, "channel": ch, "type": label, "runs": 0, "longest_h": 0.0, "longest_inputs": 0,
                                 "frac_inputs": 0.0, "frac_inputs_in_runs_ge24h": 0.0, "runs_ge24h": 0})
                    continue
                edge = np.diff(np.r_[0, m.astype(int), 0])
                st, en = np.nonzero(edge == 1)[0], np.nonzero(edge == -1)[0] - 1
                dur = np.array([(t[e] - t[s]) / 3600 for s, e in zip(st, en)])
                ln = en - st + 1
                rows.append({"run": f.stem, "channel": ch, "type": label, "runs": int(len(st)), "longest_h": float(dur.max()),
                             "longest_inputs": int(ln.max()), "frac_inputs": float(m.mean()),
                             "frac_inputs_in_runs_ge24h": float(ln[dur >= 24].sum() / len(c)), "runs_ge24h": int((dur >= 24).sum())})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "gate_latch_stale_runs.csv", index=False)
    return df


def oman_events_checks():
    ev = A.oman_tank_events()
    created = {e["event_id"]: e["created_at"] for e in ev}
    spec = A.oman_source("tank", "5min")
    tab = spec.rows.sorted_by_time()
    leaks, seen_max = [], [0]

    def cb(bi, twin, now):
        ids = twin.to_snapshot()["event_ledger"]["entries"]
        seen_max[0] = max(seen_max[0], len(ids))
        for e in ids:
            if created[e["event_id"]] > now:
                leaks.append((bi, e["event_id"]))
    r = H.run_replay(spec, tab, H.fixed_batches(len(tab), 240), events=ev, stats=False, health=False,
                     snapshot_daily=False, on_batch=cb)
    tw = r["_twin"]
    entries = tw.to_snapshot()["event_ledger"]["entries"]
    res = {"events_mapped": len(ev), "ledger_entries_final": len(entries), "future_events_in_ledger_at_any_batch": len(leaks),
           "event_times_equal_model_times": all(e["model_time"] == e["event"]["time"] for e in entries),
           "types": pd.Series([e["event"]["kind"] for e in entries]).value_counts().to_dict(),
           "food_grams_all_None": all(e["event"]["food_grams"] is None for e in entries if e["event"]["kind"] == "feeding"),
           "exceptions": r["exceptions"], "events_report": A.oman_tank_events_report()}
    a = json.load(open(S2 / "oman_tank_5min.json"))
    b = json.load(open(S2 / "oman_tank_5min+events.json"))
    res["tank5min_ph_trusted_of_present"] = {"no_events": a["usable"]["ph"]["trusted_fraction_of_present"],
                                             "with_events": b["usable"]["ph"]["trusted_fraction_of_present"]}
    res["tank5min_temp_trusted_of_present"] = {"no_events": a["usable"]["temp"]["trusted_fraction_of_present"],
                                               "with_events": b["usable"]["temp"]["trusted_fraction_of_present"]}
    return res


if __name__ == "__main__":
    out = {}
    pd.set_option("display.width", 250, "display.max_rows", 200, "display.max_columns", 40)
    summ, tab, zt, zq = fwi_gate_vs_qc()
    out["fwi_gate_vs_qc"] = summ
    print(tab.to_string()); print(zt.to_string()); print(zq.to_string()); print(json.dumps(summ, indent=1))
    out["gaps"] = gaps_vs_qc(); print(json.dumps(out["gaps"], indent=1))
    out["stale_vs_do"] = stale_vs_do(); print(json.dumps(out["stale_vs_do"], indent=1))
    res, tabs = idn_raw_vs_clean()
    out["idn"] = res
    print(json.dumps({k: v for k, v in res.items() if k != "component_sensitivity"}, indent=1))
    lt = latch_stats()
    out["oman_events"] = oman_events_checks()
    print(json.dumps(out["oman_events"], indent=1))
    (OUT / "step4_results.json").write_text(json.dumps(out, indent=1, default=str))
    print(lt[lt.type == "rate"].sort_values("longest_h", ascending=False).head(30).to_string())
