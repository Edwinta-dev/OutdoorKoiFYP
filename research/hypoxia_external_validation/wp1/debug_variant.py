"""Inspect one metamorphic mismatch. Usage: python -I wp1/debug_variant.py <idn_clean|idn_raw> <variant> [variant...]
(IDN-T thinned to 15 min). Prints the state-diff paths against the in-order single-pass reference."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import numpy as np
import adapters as A, harness as H, metamorphic as M

which = sys.argv[1].split("_")[1]
spec = A.thinned(A.idn_source(which, "jakarta"), 900)
tab = spec.rows.sorted_by_time()
kw = dict(stats=False, health=False, snapshot_daily=False)
ref = H.run_replay(spec, tab, [np.arange(len(tab))], **kw)["_twin"]
for kind in sys.argv[2:]:
    t2, b = M.variant(tab, kind)
    r = H.run_replay(spec, t2, b, **kw)
    unpl = r["_unplaced_ns"]
    base = ref
    if unpl:
        keep = np.nonzero(~np.isin(tab.tns, list(unpl)))[0]
        base = H.run_replay(spec, tab, [keep], **kw)["_twin"]
    c = M.compare(base, r["_twin"], spec, tab, validate_extra=True, drop_ns=unpl)
    print(kind, "unplaced", len(unpl), {k: c[k] for k in ("core_equal", "diff_categories", "ckpt_common_diff", "ckpt_only_in_ref",
                                                          "ckpt_only_in_variant", "ckpt_extra_validated", "n_diff_paths_core")})
    for p in c["diff_paths"][:6]:
        print("   ", p)
    if c["ckpt_common_diff"]:
        rc = {k.through.isoformat(): k for k in base.ledger.checkpoints if k.through}
        vc = {k.through.isoformat(): k for k in r["_twin"].ledger.checkpoints if k.through}
        import json
        for k in c["ckpt_common_diff"][:2]:
            d = H.diff_paths(json.loads(H.canon(rc[k].chemistry)), json.loads(H.canon(vc[k].chemistry)))
            print("   checkpoint", k, d[:5])
