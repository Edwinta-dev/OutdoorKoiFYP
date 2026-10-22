"""WP1 step 5: chemistry on EXTERNAL replays: only invariants (non-negativity; no nitrogen enters without a feeding
quantity). No feeding is invented, so pools must stay exactly 0 and no chemistry value is scored.
Mass-balance and event arithmetic are verified on SYN fixtures in syn_tests.py (chemistry_* tests)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import numpy as np
import adapters as A, harness as H

OUT = _common.WP1 / "out" / "step5"
OUT.mkdir(parents=True, exist_ok=True)
rows = []


def check(spec, events=None, tag=""):
    tab = spec.rows.sorted_by_time()
    r = H.run_replay(spec, tab, H.prod_batches(tab), events=events, stats=False, health=False, snapshot_daily=False)
    tw = r["_twin"]
    c = tw.chemistry
    vals = {"tan_mg": c._tan_mg, "no2_mg": c._no2_mg, "no3_mg": c._no3_mg, "last_tds_ppm": c._last_tds_ppm}
    rows.append({"run": spec.name + tag, "inputs": r["inputs"], **vals, "all_nonneg": min(vals.values()) >= 0,
                 "nitrogen_pools_exactly_zero": c._tan_mg == 0 and c._no2_mg == 0 and c._no3_mg == 0,
                 "exceptions": sum(r["exceptions"].values())})


for p in A.fwi_ponds():
    check(A.fwi_source(p))
check(A.oman_source("tank", "5min"), A.oman_tank_events(), "+events(10: 6 feeding without quantity, 4 water changes)")
check(A.oman_source("open", "5min"))
check(A.thinned(A.idn_source("clean", "jakarta"), 900))
check(A.thinned(A.idn_source("raw", "jakarta"), 900))
(OUT / "external_pool_invariants.json").write_text(json.dumps(rows, indent=1, default=str))
print(json.dumps({"runs": len(rows), "all_nonneg": all(r["all_nonneg"] for r in rows),
                  "pools_zero": all(r["nitrogen_pools_exactly_zero"] for r in rows), "exceptions": sum(r["exceptions"] for r in rows)}))
for r in rows[-4:]:
    print(r)
