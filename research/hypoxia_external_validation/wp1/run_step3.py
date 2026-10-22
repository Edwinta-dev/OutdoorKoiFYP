"""WP1 step 3A: metamorphic delivery tests on real-data subsets.
Reference = ONE batch, rows in order. Variants: chunked (2000/137/daily), restart
mid-run, shuffled rows, shuffled chunk order, duplicated rows (same ids / fresh ids),
delayed rows. Run: python -I wp1/run_step3.py"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import numpy as np
import adapters as A, harness as H, metamorphic as M

OUT = _common.WP1 / "out" / "step3"
OUT.mkdir(parents=True, exist_ok=True)


def subjects():
    for p in A.fwi_ponds():
        yield A.fwi_source(p, "keep"), None, None
    ev = A.oman_tank_events()
    yield A.oman_source("tank", "5min"), None, None
    yield A.oman_source("tank", "5min"), ev, "+events"
    yield A.oman_source("open", "5min"), None, None
    s = A.oman_source("tank", "native"); s.rows = s.rows.sorted_by_time().subset(np.arange(30000)); s.name += ":first30k"
    yield s, None, None
    yield A.thinned(A.idn_source("clean", "jakarta"), 900), None, None
    yield A.thinned(A.idn_source("raw", "jakarta"), 900), None, None
    yield A.thinned(A.idn_source("raw", "utc"), 900), None, None
    s = A.idn_source("clean", "jakarta"); s.rows = s.rows.sorted_by_time().subset(np.arange(120000)); s.name += ":first120k"
    yield s, None, None


def run_subject(spec, events, tag):
    tab = spec.rows.sorted_by_time()
    kw = dict(events=events, stats=False, health=False, snapshot_daily=False)
    ref = H.run_replay(spec, tab, [np.arange(len(tab))], **kw)
    ref_twin = ref.pop("_twin")
    rows = []
    for kind in M.KINDS:
        t2, batches = M.variant(tab, kind)
        t0 = time.perf_counter()
        restart = (len(batches) // 2) if kind == "restart" else None
        r = H.run_replay(spec, t2, batches, restart_at_batch=restart, **kw)
        unplaced = r["_unplaced_ns"]
        base = ref_twin
        if unplaced:  # oracle: in-order run without the sample instants production declares out_of_window (#82)
            keep = np.nonzero(~np.isin(tab.tns, list(unplaced)))[0]
            base = H.run_replay(spec, tab, [keep], **kw)["_twin"]
        cmp = M.compare(base, r["_twin"], spec, tab, validate_extra=(events is None), drop_ns=unplaced)
        if cmp["core_equal"] and not cmp["ckpt_common_diff"] and not cmp["ckpt_only_in_ref"] and cmp["ckpt_extra_validated"] in (None, {}) or (
                cmp["core_equal"] and not cmp["ckpt_common_diff"] and not cmp["ckpt_only_in_ref"] and all(v in (True, None) for v in cmp["ckpt_extra_validated"].values())):
            verdict = "PASS"
        elif cmp["only_row_id_diffs"] and not cmp["ckpt_common_diff"] and not cmp["ckpt_only_in_ref"]:
            verdict = "PASS(row_id identity only)"
        elif cmp["only_tds_prompts_diffs"] and not cmp["ckpt_common_diff"] and not cmp["ckpt_only_in_ref"]:
            verdict = "F4(tds_prompts only)"
        else:
            verdict = "MISMATCH"
        rows.append({"subject": spec.name + (tag or ""), "verdict": verdict, "variant": kind, "batches": len(batches),
                     "rows_delivered": r["rows_in"], "ref_rows": ref["rows_in"], "late_inputs": r["provenance"].get("late", 0),
                     "replayed_inputs": r["provenance"].get("replayed", 0), "unplaced": r["provenance"].get("unplaced", 0),
                     "oracle_excludes_unplaced_instants": len(unplaced),
                     "exceptions": sum(r["exceptions"].values()), "dispositions": r["dispositions"],
                     "sec": round(time.perf_counter() - t0, 2), **cmp})
    return rows


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    allrows = []
    for spec, ev, tag in subjects():
        if only and only not in spec.name:
            continue
        rows = run_subject(spec, ev, tag)
        allrows += rows
        bad = [f'{r["variant"]}:{r["verdict"]}' for r in rows if r["verdict"] != "PASS"]
        print(f"{spec.name}{tag or ''}: state-hash mismatches in {bad}", flush=True)
    (OUT / f"step3_results{'_' + only.replace(':','_') if only else ''}.json").write_text(json.dumps(allrows, indent=1, default=str))
