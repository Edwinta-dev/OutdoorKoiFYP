import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import numpy as np, pandas as pd
import adapters as A, harness as H, metamorphic as M
spec = A.thinned(A.idn_source("clean", "jakarta"), 900)
tab = spec.rows.sorted_by_time()
t2, b = M.variant(tab, "shuffle_chunks")
print([(pd.Timestamp(tab.tns[x].min(), tz="UTC").isoformat()[:16], pd.Timestamp(tab.tns[x].max(), tz="UTC").isoformat()[:16]) for x in b])
def info(tw):
    out = {}
    for c in tw.ledger.checkpoints:
        if c.through and "2023-02-19" <= c.through.isoformat()[:10] <= "2023-02-21":
            d = c.chemistry["daily"].get("2023-02-20")
            out[c.through.isoformat()[:16]] = (len(d["trusted_tds"]) if d else None, d["day"][:16] if d else None)
    return out
for i in range(1, len(b) + 1):
    r = H.run_replay(spec, t2, b[:i], stats=False, health=False, snapshot_daily=False)
    tw = r["_twin"]
    d = tw.chemistry._daily.get("2023-02-20")
    print(i, "live Feb20 bucket:", (len(d.trusted_tds), d.day.isoformat()[:16]) if d else None, info(tw))
