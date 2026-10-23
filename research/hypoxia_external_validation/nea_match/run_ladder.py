"""WINDOW and PREEMPT.
(A) production exactly as koi/api/routes.py calls it (every unavailable input passed as None).
(B) production ladder.py comparators + ladder_params.json fed with inputs built from the archive per the notebook
    definitions at honest decision times. (B) is NOT production end to end: production has no code that builds these inputs."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inputs import *
from datetime import datetime, timedelta, timezone
import json
SG = timezone(timedelta(hours=8))
def aw(ts): return ts.to_pydatetime().replace(tzinfo=SG)

OLD_NB, NEW_NB = 32.60, 31.80          # notebook cell 12 stored output (HOT threshold old / new regime)
days_all = pd.date_range("2020-02-01", "2026-08-20")           # notebook `daily` index ends at the last periods date
test_days = pd.date_range(TEST_START, TEST_END)
train_days = pd.date_range("2020-02-01", "2024-12-31")

# ---------------- observed series ----------------
rain_h = load_rain_h(); dr = day_rain(rain_h).reindex(days_all)
cl = load_climate(); tmax = cl.t_max.reindex(days_all); regime = cl.regime.reindex(days_all)
def hot_series(old, new):
    thr = pd.Series(np.where(regime == "new", new, old), index=days_all)
    return pd.Series(np.where(tmax.notna(), (tmax >= thr).astype(float), np.nan), index=days_all)
def hot_next3(hot): return pd.concat([hot.shift(-k) for k in (1, 2, 3)], axis=1).max(axis=1, skipna=False)
y_window = pd.Series(np.where(dr.notna(), (dr < 1).astype(float), np.nan), index=days_all)
y_pre_nb = hot_next3(hot_series(OLD_NB, NEW_NB))
y_pre_json = hot_next3(hot_series(PARAMS["temperature_threshold_c"]["old"], PARAMS["temperature_threshold_c"]["new"]))

# ---------------- forecast inputs ----------------
p = load_periods(); o = load_outlook()
nb_rank = pooled_rank_notebook(p).reindex(days_all); nb_out = outlook_notebook(o).reindex(days_all)

per_rows = {}
pe = p.drop_duplicates(["iss", "ps", "region"]).copy()
pe["pe"] = local(pe.period_end)
for r in pe.itertuples():
    per_rows.setdefault((r.region, r.ps), []).append({"product": "24hr", "slot_id": "REG_" + r.region.upper(),
        "issued_at": aw(r.iss).isoformat(), "source_updated_at": None, "valid_from": aw(r.ps).isoformat(),
        "valid_to": aw(r.pe).isoformat(), "payload": {"text": r.forecast_text},
        "fetched_at": aw(r.iss).isoformat(), "id": r.Index})

def pooled_rank_prod_selector(D, hour):
    """production select_forecast_as_of per (region, block); rank from the notebook wording->tier map; pooled as the notebook."""
    as_of = aw(D) + timedelta(hours=hour); per_region = []
    for reg in REGIONS:
        ranks = []
        for b in range(4):
            ps = D + pd.Timedelta(hours=6 * b)
            got = weather_history.select_forecast_as_of(per_rows.get((reg, ps), []), as_of, valid_at=aw(ps) + timedelta(minutes=1))
            if got is not None:
                t = to_tier(got["payload"]["text"])
                if pd.notna(t): ranks.append(TIER_RANK[t])
        if ranks: per_region.append(np.mean(ranks))
    return float(np.mean(per_region)) if per_region else np.nan

oe = o.drop_duplicates(["iss", "target"])
out_rows = {}
for r in oe.itertuples():
    out_rows.setdefault(r.target, []).append({"product": "4day", "slot_id": r.target.date().isoformat(),
        "issued_at": aw(r.iss).isoformat(), "source_updated_at": None, "valid_from": aw(r.target).isoformat(),
        "valid_to": aw(r.target + pd.Timedelta(days=1)).isoformat(), "fetched_at": aw(r.iss).isoformat(), "id": r.Index,
        "payload": {"temperature": {"low": r.temp_low, "high": r.temp_high}, "forecast": {"text": r.forecast_text}}})

def outlook_prod_selector(D, hour=23.99):
    """lead 1..4 entries visible at D+hour via the production selector; midpoint via the production forecast_utils helper."""
    as_of = aw(D) + timedelta(hours=hour); entries = []
    for L in range(1, 5):
        tgt = D + pd.Timedelta(days=L)
        got = weather_history.select_forecast_as_of(out_rows.get(tgt, []), as_of, valid_at=aw(tgt) + timedelta(hours=12))
        if got is not None: entries.append({"data": got["payload"]})
    if not entries: return (np.nan, np.nan)
    env = forecast_utils.daily_environment_from_outlook(entries)
    mids = [e["air_temp_c"] for e in env if e["air_temp_c"] is not None]
    highs = [e["data"]["temperature"]["high"] for e in entries]
    return (float(np.mean(mids)) if mids else np.nan, float(np.mean(highs)))

rank_asof = {h: pd.Series({D: pooled_rank_prod_selector(D, h) for D in days_all}, dtype=float) for h in (0, 6, 12)}
oa = {D: outlook_prod_selector(D) for D in days_all}
out_mid_asof = pd.Series({D: v[0] for D, v in oa.items()}, dtype=float)
out_high_asof = pd.Series({D: v[1] for D, v in oa.items()}, dtype=float)
pd.DataFrame({"pooled_rank_notebook": nb_rank, **{f"pooled_rank_asof_{h:02d}h": rank_asof[h] for h in rank_asof},
              "outlook_mid_notebook": nb_out, "outlook_mid_asof_2359": out_mid_asof, "outlook_high_asof_2359": out_high_asof,
              "t_max": tmax, "regime": regime, "rain_mm": dr, "y_window": y_window, "y_hot_next3_nbthr": y_pre_nb,
              "y_hot_next3_jsonthr": y_pre_json}).to_csv(OUT / "ladder_inputs_daily.csv")

# ---------------- production comparators ----------------
BAND = PARAMS["pooled_dry_rank_max"]; OHT = PARAMS["outlook_hot_threshold_c"]
def nn(x): return None if pd.isna(x) else float(x)
def f_window(rank):
    return bool(ladder.window(pooled_rank=nn(rank), dry_band_max=BAND)["actions"])
def f_preempt(tm, reg, outl):
    return bool(ladder.preempt(observed_tmax_c=nn(tm), regime=None if pd.isna(reg) else reg,
                               outlook_high_c=nn(outl), outlook_hot_threshold_c=OHT)["actions"])
# the two halves of PREEMPT in isolation: force the other half true (99 C, regime 'old')
def f_outlook_only(outl): return f_preempt(99.0, "old", outl)
def f_hot_only(tm, reg): return f_preempt(tm, reg, 99.0)

def evaluate(fire, y, days, name, variant):
    fire = fire.reindex(days); y = y.reindex(days); ok = y.notna()
    m = fire[ok].astype(bool); yy = y[ok]
    n = int(m.sum()); k = int(yy[m].sum()); lo, hi = wilson(k, n); yrs = yy.index.year.nunique()
    return dict(rule=name, variant=variant, days_scored=int(ok.sum()), n_fired=n, hits=k, precision=k / n if n else np.nan,
                ci_lo=lo, ci_hi=hi, base=yy.mean(), lift=(k / n) / yy.mean() if n else np.nan,
                fires_per_yr_chapter_method=n / yrs,
                fires_per_calendar_year_annualised=n / ((days[-1] - days[0]).days + 1) * 365.25)

res = []
for split, days in (("TEST", test_days), ("train", train_days)):
    for var, rank in (("inputs as in notebook (earliest issuance, no time filter)", nb_rank),
                      ("as-of 00:00 D (production selector)", rank_asof[0]), ("as-of 06:00 D (production selector)", rank_asof[6]),
                      ("as-of 12:00 D (production selector)", rank_asof[12])):
        fire = pd.Series({D: f_window(rank[D]) for D in days_all})
        res.append({"split": split, **evaluate(fire, y_window, days, "WINDOW (dry day <1 mm)", var)})
    for var, outl, ysrc in (("outlook = mean lead1-4 midpoints, notebook earliest issuance; outcome nb thr", nb_out, y_pre_nb),
                            ("outlook = mean midpoints as-of 23:59 D (production selector); outcome nb thr", out_mid_asof, y_pre_nb),
                            ("as-of 23:59 midpoints; outcome with production JSON thr (31.86)", out_mid_asof, y_pre_json),
                            ("outlook = mean lead1-4 temp HIGH as-of 23:59 D (name outlook_high_c read literally)", out_high_asof, y_pre_nb)):
        fire = pd.Series({D: f_preempt(tmax[D], regime[D], outl[D]) for D in days_all})
        res.append({"split": split, **evaluate(fire, ysrc, days, "PREEMPT (hot today + hot outlook)", var)})
        if var.startswith(("outlook = mean lead1-4 midpoints", "outlook = mean midpoints")):
            fo = pd.Series({D: f_outlook_only(outl[D]) for D in days_all})
            res.append({"split": split, **evaluate(fo, ysrc, days, "hot outlook alone", var)})
            fh = pd.Series({D: f_hot_only(tmax[D], regime[D]) for D in days_all})
            res.append({"split": split, **evaluate(fh, ysrc, days, "hot today alone", var)})
res = pd.DataFrame(res); res.to_csv(OUT / "ladder_precision.csv", index=False)
pd.set_option("display.width", 250, "display.max_colwidth", 90)
print(res.drop(columns=["hits"]).round(3).to_string())

# ---------------- A: production as called (routes._lead_time_actions arguments) ----------------
stat = {"REACT": {}, "PREEMPT": {}, "WINDOW": {}, "NOWCAST": {}}; fired = {k: 0 for k in stat}
nc_rows = [{"product": "2hr", "slot_id": "Woodlands", "issued_at": aw(r.issued_at).isoformat(), "source_updated_at": None,
            "valid_from": aw(r.valid_from).isoformat(), "valid_to": aw(r.valid_to).isoformat(), "payload": {"forecast": r.forecast_text},
            "fetched_at": aw(r.issued_at).isoformat(), "id": i}
           for i, r in load_nowcast()[0].iterrows() if r.issued_at >= TEST_START - pd.Timedelta(days=2)]
for D in test_days:
    now = aw(D) + timedelta(hours=6)
    row = weather_history.select_forecast_as_of(nc_rows, now, valid_at=now)
    val = ((row or {}).get("payload") or {}).get("forecast")
    out = ladder.actions(decision_time=now, rain=None, nowcast_text=val if isinstance(val, str) else None,
                         observed_tmax_c=None, temperature_regime=None, outlook_high_c=None, pooled_rank=None, dry_band_max=None)
    for r in out["rules"]:
        k = r["rule"]; stat[k][r["status"]] = stat[k].get(r["status"], 0) + 1; fired[k] += len(r["actions"])
asc = pd.DataFrame([{"rule": k, "decision_days": len(test_days), "status_counts": json.dumps(stat[k]), "actions_fired": fired[k]} for k in stat])
asc.to_csv(OUT / "production_as_called.csv", index=False); print(asc.to_string())
