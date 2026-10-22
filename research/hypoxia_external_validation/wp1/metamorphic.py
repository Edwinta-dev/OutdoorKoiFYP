"""WP1 step 3 helpers: delivery variants of one RowTable and state comparison."""
import json
import numpy as np
import _common
import harness as H

SEED = _common.SEED


def variant(table, kind, seed=SEED):
    """table must be time-sorted. Returns (table2, batches2). Reference = ("single")."""
    n = len(table)
    rng = np.random.default_rng(seed)
    allidx = np.arange(n)
    if kind == "single":
        return table, [allidx]
    if kind == "chunk2000":
        return table, H.prod_batches(table)
    if kind == "chunk137":
        return table, H.fixed_batches(n, 137)
    if kind == "daily":
        day = table.tns // (86400 * 10**9)
        cuts = np.nonzero(np.r_[True, day[1:] != day[:-1]])[0]
        return table, [allidx[a:b] for a, b in zip(cuts, list(cuts[1:]) + [n])]
    if kind == "restart":  # same delivery as chunk2000; the harness restarts mid-way
        return table, H.prod_batches(table)
    if kind == "shuffle_within":
        return table, [rng.permutation(n)]
    if kind == "shuffle_chunks":
        b = H.prod_batches(table)
        order = rng.permutation(len(b))
        return table, [b[i] for i in order]
    if kind == "dup_same_ids":  # every 5th chunk is re-delivered (same row ids) after the next chunk
        b = H.prod_batches(table)
        out = []
        for i, idx in enumerate(b):
            out.append(idx)
            if i >= 1 and (i - 1) % 5 == 0:
                out.append(b[i - 1])
        return table, out
    if kind == "dup_new_ids":  # store-and-forward retry: same samples, fresh row ids, with the next chunk
        b = H.prod_batches(table)
        sel_ids = list(range(0, len(b), 5))
        copies, pos, parts = {}, n, []
        for i in sel_ids:
            copies[i] = np.arange(pos, pos + len(b[i]))
            pos += len(b[i])
            parts.append(b[i])
        dup = table.subset(np.concatenate(parts))
        dup.ids = dup.ids + 10**12
        t2 = table.concat(dup)
        out = [np.concatenate([idx, copies.get(i - 1, np.array([], dtype=np.int64))]) for i, idx in enumerate(b)]
        if (len(b) - 1) in copies:
            out.append(copies[len(b) - 1])
        return t2, out
    if kind.startswith("delayed") and kind != "delayed_beyond_window":  # every 10th sample instant arrives k chunks late
        k = int(kind[len("delayed"):] or 3)
        inst, inv = np.unique(table.tns, return_inverse=True)
        late = inv % 10 == 0
        b = H.prod_batches(table)
        pend = {}
        out = []
        for i, idx in enumerate(b):
            keep = idx[~late[idx]]
            for j in idx[late[idx]]:
                pend.setdefault(min(i + k, len(b) - 1), []).append(j)
            extra = np.array(pend.pop(i, []), dtype=np.int64)
            out.append(np.concatenate([keep, extra]))
        return table, out
    if kind == "delayed_beyond_window":  # first 3% of instants delivered after everything else
        inst, inv = np.unique(table.tns, return_inverse=True)
        cut = max(int(len(inst) * 0.03), 1)
        late = inv < cut
        b = H.prod_batches(table)
        out = [idx[~late[idx]] for idx in b if len(idx[~late[idx]])]
        out.append(allidx[late])
        return table, out
    raise ValueError(kind)


KINDS = ["chunk2000", "chunk137", "daily", "restart", "shuffle_within", "shuffle_chunks",
         "dup_same_ids", "dup_new_ids", "delayed1", "delayed3", "delayed_beyond_window"]


def category(path):
    p = path.split("/")
    if len(p) < 2:
        return "root"
    if p[1] == "event_ledger":
        return "event_ledger/" + (p[2].split("[")[0] if len(p) > 2 else "")
    if p[1] == "chemistry" and len(p) > 2:
        return "chemistry/" + p[2].split("[")[0]
    return p[1].split("[")[0]


def _core(snapshot):
    s = json.loads(H.canon(snapshot, True))
    s["event_ledger"].pop("checkpoints", None)
    return s


def prefix_checkpoint(spec, tab, through, drop_ns=()):
    """Chemistry checkpoint an IN-ORDER run would hold at `through` (no events)."""
    m = tab.tns <= int(through.value if hasattr(through, "value") else through)
    idx = np.nonzero(m & ~np.isin(tab.tns, list(drop_ns)))[0] if len(drop_ns) else np.nonzero(m)[0]
    if len(idx) == 0:
        return None
    r = H.run_replay(spec, tab, [idx], stats=False, health=False, snapshot_daily=False)
    tw = r["_twin"]
    return json.loads(H.canon(tw._chemistry_checkpoint(tw.last_input_at)))


def _trim(chem, cutoff):
    """Checkpoint chemistry without day buckets at/over the retention edge (calendar date <= cutoff): a restore under
    now = newest input prunes them by DAILY_RETENTION_DAYS, an in-order run prunes them later; both are by design."""
    c = json.loads(H.canon(chem))
    c["daily"] = {k: v for k, v in c["daily"].items() if v.get("calendar_day") is None or v["calendar_day"] > cutoff}
    return json.dumps(c, sort_keys=True)


def compare(ref_twin, var_twin, spec=None, tab=None, validate_extra=False, drop_ns=()):
    import pandas as pd
    a, b = ref_twin.to_snapshot(), var_twin.to_snapshot()
    core_a, core_b = _core(a), _core(b)
    d_core = H.diff_paths(core_a, core_b, limit=200)
    et_a, et_b = H.effective_times(ref_twin), H.effective_times(var_twin)
    et_a.pop("checkpoint_through"), et_b.pop("checkpoint_through")
    rc = {c.through.isoformat(): c for c in ref_twin.ledger.checkpoints if c.through}
    vc = {c.through.isoformat(): c for c in var_twin.ledger.checkpoints if c.through}
    only_ref, only_var = sorted(set(rc) - set(vc)), sorted(set(vc) - set(rc))
    from datetime import timedelta
    from koi.models.local_time import local_date
    newest = var_twin.ledger.newest_input_at
    cutoff = (local_date(newest, var_twin.chemistry.config.time_zone) - timedelta(days=var_twin.chemistry.DAILY_RETENTION_DAYS)).isoformat() if newest else ""
    ck_diff = [k for k in sorted(set(rc) & set(vc))
               if _trim(rc[k].chemistry, cutoff) != _trim(vc[k].chemistry, cutoff)]
    extra_ok = None
    if validate_extra and only_var and tab is not None:
        extra_ok = {}
        for k in only_var:
            want = prefix_checkpoint(spec, tab, pd.Timestamp(k), drop_ns)
            extra_ok[k] = None if want is None else bool(_trim(vc[k].chemistry, cutoff) == _trim(want, cutoff))
    return {
        "hash_full_equal": H.snap_hash(a) == H.snap_hash(b),
        "hash_state_equal": H.snap_hash(a, True) == H.snap_hash(b, True),
        "core_equal": not d_core,
        "event_ids_equal": H.event_ids(ref_twin) == H.event_ids(var_twin),
        "effective_times_equal": et_a == et_b,
        "ckpt_common_diff": ck_diff, "ckpt_only_in_ref": only_ref, "ckpt_only_in_variant": only_var,
        "ckpt_extra_validated": extra_ok,
        "diff_categories": sorted({category(p[0]) for p in d_core}),
        "only_row_id_diffs": bool(d_core) and all(p[0].endswith("/row_id") for p in d_core),
        "only_tds_prompts_diffs": bool(d_core) and all(p[0].startswith("/tds_prompts") for p in d_core),
        "diff_paths": [list(map(str, x)) for x in d_core[:6]],
        "n_diff_paths_core": len(d_core),
    }
