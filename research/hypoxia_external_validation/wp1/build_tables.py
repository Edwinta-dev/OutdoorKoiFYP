"""Builds wp1/out/tables.md (every table in REPORT.md comes from here, from the saved run outputs)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import numpy as np, pandas as pd

OUT = _common.WP1 / "out"
S2, S3, S4, SYN, S5 = OUT / "step2", OUT / "step3", OUT / "step4", OUT / "syn", OUT / "step5"
md = []


def table(df, title, floatfmt=3):
    md.append(f"\n### {title}\n")
    md.append(df.to_markdown(index=False, floatfmt=f".{floatfmt}f") if hasattr(df, "to_markdown") else df.to_string(index=False))
    md.append("")


def md_table(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                cells.append("" if np.isnan(v) else (f"{v:.3f}" if abs(v) < 1000 else f"{v:,.0f}"))
            elif isinstance(v, (int, np.integer)):
                cells.append(f"{v:,}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def put(title, df):
    md.append(f"\n#### {title}\n")
    md.append(md_table(df))
    md.append("")


def load(name):
    return json.load(open(S2 / name))


def verdict_sum(js, ch):
    d = {}
    for j in js:
        for k, v in j["gate"][ch]["verdicts"].items():
            d[k] = d.get(k, 0) + v
    return d


def summarise(label, js):
    n_in = sum(j["inputs"] for j in js)
    row = {"run": label, "runs": len(js), "rows in": sum(j["rows_in"] for j in js), "inputs": n_in,
           "superseded rows": sum(j["dispositions"].get("superseded", 0) for j in js),
           "unusable rows": sum(j["dispositions"].get("unusable", 0) for j in js),
           "exceptions": sum(sum(j["exceptions"].values()) for j in js)}
    for ch in ("ph", "temp", "tds"):
        v = verdict_sum(js, ch)
        present = sum(j["gate"][ch]["present"] for j in js)
        row[f"{ch} present"] = present / n_in
        row[f"{ch} ok|present"] = (v.get("ok", 0) / present) if present else float("nan")
        row[f"{ch} stale"] = v.get("suspect_stale", 0)
        row[f"{ch} rate"] = v.get("suspect_rate", 0)
        row[f"{ch} range+rej"] = v.get("suspect_range", 0) + v.get("rejected", 0)
    row["snap roundtrip ok"] = f'{sum(j["snapshots"]["roundtrip_identical"] for j in js)}/{sum(j["snapshots"]["count"] for j in js)}'
    row["dayKey mismatches"] = sum(j["local_time"]["local_day_key_mismatches_vs_pandas"] for j in js)
    row["gate==twin gate"] = f'{sum(1 for j in js if j.get("gate_matches_twin_gate") is True)}/{sum(1 for j in js if j.get("gate_matches_twin_gate") is not None)}'
    row["wall s"] = sum(j["wall_s"] for j in js)
    return row


def main():
    fw_k = [json.load(open(p)) for p in sorted(S2.glob("fwi_*_zeros-keep.json"))]
    fw_a = [json.load(open(p)) for p in sorted(S2.glob("fwi_*_zeros-absent.json"))]
    runs = [summarise("FWI, 17 ponds, as published (zeros kept)", fw_k), summarise("FWI, 17 ponds, zeros -> absent", fw_a)]
    for f, lab in (("oman_open_native", "Oman open pond, native 5-7 s"), ("oman_open_5min", "Oman open pond, 5-min grid"),
                   ("oman_open_native_thin15min", "Oman open pond, 15-min"), ("oman_tank_native", "Oman 180 L tank, native 5-7 s"),
                   ("oman_tank_native+events", "Oman tank native + 10 logged events"),
                   ("oman_tank_5min", "Oman tank, 5-min grid"), ("oman_tank_5min+events", "Oman tank 5-min + 10 logged events"),
                   ("oman_tank_native_thin15min", "Oman tank, 15-min"),
                   ("idn_t_raw_tz-jakarta", "IDN-T raw, Asia/Jakarta (assumed)"), ("idn_t_raw_tz-utc", "IDN-T raw, UTC (sensitivity)"),
                   ("idn_t_clean_tz-jakarta", "IDN-T cleaned, Asia/Jakarta"), ("idn_t_clean_tz-utc", "IDN-T cleaned, UTC"),
                   ("idn_t_raw_tz-jakarta_thin15min", "IDN-T raw, thinned to 15-min"),
                   ("idn_t_clean_tz-jakarta_thin15min", "IDN-T cleaned, thinned to 15-min")):
        p = S2 / f"{f}.json"
        if p.exists():
            runs.append(summarise(lab, [json.load(open(p))]))
    t1 = pd.DataFrame(runs)
    md.append("## Step 2: per-source replay (single pass through the poll loop; batch 2000 rows, complete_groups)\n")
    put("S2-A rows, dispositions, exceptions, snapshot round-trips", t1[["run", "runs", "rows in", "inputs", "superseded rows", "unusable rows", "exceptions", "snap roundtrip ok", "dayKey mismatches", "gate==twin gate", "wall s"]])
    put("S2-B usable-channel fraction and SensorGate verdicts (inputs; 'ok|present' = trusted share of inputs that carried the channel)",
        t1[["run", "ph present", "ph ok|present", "ph stale", "ph rate", "ph range+rej", "temp present", "temp ok|present", "temp stale", "temp rate", "tds present", "tds ok|present", "tds stale", "tds rate"]])
    # runtime per component
    comp = {}
    for label, glob in (("FWI (17 ponds)", "fwi_*_zeros-keep.json"), ("Oman tank native", "oman_tank_native.json"), ("IDN-T raw (Jakarta)", "idn_t_raw_tz-jakarta.json"), ("IDN-T cleaned (Jakarta)", "idn_t_clean_tz-jakarta.json")):
        js = [json.load(open(p)) for p in sorted(S2.glob(glob))]
        tot = {}
        for j in js:
            for k, v in j["runtime_s"].items():
                tot[k] = tot.get(k, 0) + v
        n_in = sum(j["inputs"] for j in js)
        comp[label] = {**{k: v for k, v in tot.items()}, "inputs": n_in}
    rt = pd.DataFrame(comp).T.reset_index().rename(columns={"index": "source"})
    put("S2-C runtime per component (seconds, total over the run; 'inputs' = sensor inputs processed)", rt)
    # device health and local time
    dh_rows = []
    for f, lab in (("oman_tank_native", "Oman tank native (interval 6 s)"), ("oman_tank_5min", "Oman tank 5-min (300 s)"), ("oman_open_native", "Oman open native (6 s)"),
                   ("idn_t_raw_tz-jakarta", "IDN-T raw (assumed 900 s)"), ("idn_t_clean_tz-jakarta", "IDN-T cleaned (assumed 900 s)")):
        j = load(f + ".json")
        d = j["device_health"]
        dh_rows.append({"run": lab, "contacts": d["contacts"], "pairs with >=1 missed cycle": d["pairs_with_missed"], "missed cycles": d["missed_total_pairwise"],
                        "pairwise sum == whole window": d["pairwise_equals_whole"], "median gap s": d["gap_s_median"], "p99 gap s": d["gap_s_p99"], "max gap h": d["gap_s_max"] / 3600,
                        "confidence chemistry (chunks)": json.dumps(j.get("confidence_levels", {}).get("chemistry"))})
    for p in sorted(S2.glob("fwi_*_zeros-keep.json"))[:0]:
        pass
    fw_dh = {"run": "FWI, 17 ponds (900 s)", "contacts": sum(j["device_health"]["contacts"] for j in fw_k), "pairs with >=1 missed cycle": sum(j["device_health"]["pairs_with_missed"] for j in fw_k),
             "missed cycles": sum(j["device_health"]["missed_total_pairwise"] for j in fw_k), "pairwise sum == whole window": all(j["device_health"]["pairwise_equals_whole"] for j in fw_k),
             "median gap s": float(np.median([j["device_health"]["gap_s_median"] for j in fw_k])), "p99 gap s": float(np.median([j["device_health"]["gap_s_p99"] for j in fw_k])),
             "max gap h": max(j["device_health"]["gap_s_max"] for j in fw_k) / 3600, "confidence chemistry (chunks)": ""}
    put("S2-D device_health on the replays (production missed_cycles over every consecutive contact pair)", pd.DataFrame([fw_dh] + dh_rows))
    lt = []
    for j in fw_k + [load(f) for f in ("oman_open_native.json", "oman_tank_native.json", "idn_t_raw_tz-jakarta.json", "idn_t_raw_tz-utc.json", "idn_t_clean_tz-jakarta.json")]:
        lt.append({"run": j["name"], "tz": j["source"]["time_zone"], "tz status": j["source"]["tz_status"], "unique instants": j["local_time"]["unique_instants"],
                   "local days": j["local_time"]["local_days"], "mismatches vs pandas": j["local_time"]["local_day_key_mismatches_vs_pandas"], "monotone": j["local_time"]["monotone"]})
    lt = pd.DataFrame(lt)
    agg = lt.groupby("tz").agg(runs=("run", "size"), instants=("unique instants", "sum"), mismatches=("mismatches vs pandas", "sum")).reset_index()
    put("S2-E local_time.local_day_key vs independent pandas conversion on every distinct sample instant", agg)
    # TDS
    tr = []
    for f in ("idn_t_clean_tz-jakarta", "idn_t_raw_tz-jakarta", "idn_t_clean_tz-jakarta_thin15min", "idn_t_raw_tz-jakarta_thin15min"):
        j = load(f + ".json")
        s = j["tds_steps"]
        tr.append({"run": j["name"], "gate-trusted TDS readings": s["trusted_tds_readings"], "steps floor 10": s["steps_floor10"], "floor 20": s["steps_floor20"], "floor 40": s["steps_floor40"],
                   "15-min readings": s["thinned_15min_readings"], "steps@15min floor 10": s["steps_thinned15_floor10"], "floor 20 ": s["steps_thinned15_floor20"], "floor 40 ": s["steps_thinned15_floor40"],
                   "prompts (enabled)": j["tds_prompts"]["prompts"], "by kind": json.dumps(j["tds_prompts"]["by_detection"])})
    put("S2-F tds_steps / tds_prompts on IDN-T (no logged action exists; every step is 'unlabelled', not an intervention)", pd.DataFrame(tr))

    # ---- step 3
    if (S3 / "step3_results.json").exists():
        r3 = pd.DataFrame(json.load(open(S3 / "step3_results.json")))
        md.append("\n## Step 3: metamorphic delivery tests\n")
        r3["family"] = r3["subject"].str.replace(r"ara2_[0-9a-f]+", "ara2_*", regex=True)
        r3["family"] = r3["family"].str.replace(":zeros-keep", "")
        piv = r3.groupby(["family", "variant"])["verdict"].agg(lambda s: ", ".join(f"{k} x{v}" for k, v in s.value_counts().items())).unstack("variant")
        piv = piv.reset_index()
        put("S3-A verdict per subject family x delivery variant", piv)
        cnt = r3.groupby("variant")["verdict"].value_counts().unstack(fill_value=0).reset_index()
        put("S3-B all subjects: verdict counts per variant", cnt)
        tot = r3.assign(sub=r3.subject).groupby("variant").agg(subjects=("sub", "nunique"), late=("late_inputs", "sum"), replayed=("replayed_inputs", "sum"), unplaced=("unplaced", "sum"),
                                                              exceptions=("exceptions", "sum"), event_ids_equal=("event_ids_equal", "all"), eff_times_equal=("effective_times_equal", "all")).reset_index()
        put("S3-C exposure per variant (summed over subjects)", tot)
        ex = r3[r3.ckpt_only_in_variant.map(len) > 0]
        md.append(f"\nChecked extra checkpoints (variant-only): {int(ex.ckpt_only_in_variant.map(len).sum())}; "
                  f"all validated equal to the in-order prefix state: "
                  f"{all(all(v in (True, None) for v in (d or {}).values()) for d in ex.ckpt_extra_validated)}; "
                  f"checkpoints present in the reference but missing in a variant: {int(r3.ckpt_only_in_ref.map(len).sum())}; "
                  f"common checkpoints with different content: {int(r3.ckpt_common_diff.map(len).sum())}.\n")
        mm = r3[~r3.verdict.isin(["PASS", "PASS(row_id identity only)", "F4(tds_prompts only)"])]
        md.append(f"\nMISMATCH rows (anything other than PASS / row_id identity / tds_prompts only): {len(mm)}\n")
        if len(mm):
            put("S3-D mismatches", mm[["subject", "variant", "diff_categories", "ckpt_common_diff", "ckpt_only_in_ref"]].astype(str))
        f4 = r3[r3.verdict == "F4(tds_prompts only)"][["subject", "variant", "late_inputs", "replayed_inputs", "n_diff_paths_core"]]
        put("S3-E rows whose only difference is tds_prompts state", f4)

    # ---- syn
    sy = json.load(open(SYN / "syn_results.json"))
    md.append("\n## SYN fixtures (D-tier)\n")
    put("SYN tests", pd.DataFrame([{"test": r["test"], "result": "PASS" if r["ok"] else "FAIL"} for r in sy]))

    # ---- step 4
    r4 = json.load(open(S4 / "step4_results.json"))
    md.append("\n## Step 4 (B-tier agreement; not ground truth)\n")
    ct = pd.read_csv(S4 / "fwi_gate_ph_vs_qc_crosstab.csv")
    put("S4-A FWI pH: SensorGate verdict (rows) x FWI QC_Flag_pH (cols), 72,492 distinct sample instants over 17 ponds", ct)
    put("S4-B FWI documented zero artefacts (README) x gate verdict", pd.read_csv(S4 / "fwi_documented_zero_artifacts_vs_gate.csv"))
    put("S4-C same rows x FWI QC flag", pd.read_csv(S4 / "fwi_documented_zero_artifacts_vs_qc.csv"))
    put("S4-D gate vs QC per pond (pH)", pd.read_csv(S4 / "fwi_gate_ph_vs_qc_per_pond.csv"))
    g = r4["gaps"]
    md.append("\n#### S4-E device_health gap detection vs FWI QC time_gap\n")
    md.append("```json\n" + json.dumps({k: g[k] for k in g if k != "gap_hist_s"} | {"gap_hist_s": g["gap_hist_s"]}, indent=1) + "\n```\n")
    ir = r4["idn"]
    md.append("\n#### S4-F IDN-T raw vs cleaned file\n")
    md.append("```json\n" + json.dumps({k: v for k, v in ir.items() if k != "component_sensitivity"}, indent=1) + "\n```\n")
    cs = pd.DataFrame(ir["component_sensitivity"]).T.reset_index().rename(columns={"index": "run"})
    cs["tds_steps"] = cs["tds_steps"].map(lambda d: f'{d["steps_floor20"]}@1min/{d["steps_thinned15_floor20"]}@15min' if isinstance(d, dict) else "")
    cs["confidence_chemistry"] = cs["confidence_chemistry"].map(json.dumps)
    put("S4-G IDN-T component sensitivity (raw vs cleaned, Jakarta vs UTC, full vs 15-min)", cs)
    lat = pd.read_csv(S4 / "gate_latch_stale_runs.csv")
    pick = lat[(lat.type == "rate") & (lat.channel == "ph")].sort_values("longest_h", ascending=False).head(14)
    put("S4-H longest SensorGate suspect_rate runs, pH (a run that never releases is the F2 latch)", pick[["run", "runs", "longest_h", "longest_inputs", "frac_inputs", "frac_inputs_in_runs_ge24h", "runs_ge24h"]])
    st = lat[(lat.type == "stale") & (lat.channel.isin(["temp", "ph"]))]
    sts = st.assign(group=st.run.str.replace(r"ara2_[0-9a-f]+", "ara2_*", regex=True)).groupby(["group", "channel"]).agg(
        runs=("runs", "sum"), mean_frac_inputs_stale=("frac_inputs", "mean"), longest_h=("longest_h", "max")).reset_index()
    put("S4-I SensorGate suspect_stale share of inputs (mean over runs in group)", sts[sts.group.str.contains("zeros-keep|oman|idn")])
    md.append("\n#### S4-J Oman events\n```json\n" + json.dumps(r4["oman_events"], indent=1) + "\n```\n")

    # ---- step 5
    ex5 = json.load(open(S5 / "external_pool_invariants.json"))
    md.append("\n## Step 5: chemistry invariants on external replays\n")
    put("S5-A external replays: pools and non-negativity (no feeding quantity exists, so pools must be 0)",
        pd.DataFrame([{"run": r["run"], "inputs": r["inputs"], "tan_mg": r["tan_mg"], "no2_mg": r["no2_mg"], "no3_mg": r["no3_mg"], "last_tds_ppm": r["last_tds_ppm"], "all_nonneg": r["all_nonneg"], "pools_zero": r["nitrogen_pools_exactly_zero"]} for r in ex5]))
    (OUT / "tables.md").write_text("\n".join(md), encoding="utf-8")
    print("tables.md written", len("\n".join(md)))


main()
