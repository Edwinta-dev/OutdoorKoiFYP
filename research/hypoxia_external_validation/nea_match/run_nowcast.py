"""Nowcast rule: production ladder.nowcast over the archived Woodlands 2-hour issuances, selected with the
production as-of selector (koi.weather.history.select_forecast_as_of), scored with the notebook outcome definitions."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
from datetime import datetime, timedelta, timezone
import json, bisect

SG = timezone(timedelta(hours=8))
def aware(ts): return ts.to_pydatetime().replace(tzinfo=SG)

rain_h = load_rain_h()
c, h0, nh = load_nowcast()
c["window_mm"], c["window_rain"] = window_rain(c, h0, nh, rain_h)
c["nb_flag"] = c.tier.map({"Fair": 0, "Light": 1, "Thundery": 1, "Moderate": 1, "Severe": 1}).astype(bool)

rows = [{"product": "2hr", "slot_id": "Woodlands", "issued_at": aware(r.issued_at).isoformat(), "source_updated_at": None,
         "valid_from": aware(r.valid_from).isoformat(), "valid_to": aware(r.valid_to).isoformat(),
         "payload": {"forecast": r.forecast_text}, "fetched_at": aware(r.issued_at).isoformat(), "id": int(i)}
        for i, r in c.iterrows()]
iss = c.issued_at.values

prod_fire = np.zeros(len(c), bool); prod_fire_sel = np.zeros(len(c), bool); sel_idx = np.full(len(c), -1)
for i in range(len(c)):
    lo = np.searchsorted(iss, iss[i] - np.timedelta64(6, "h")); hi = np.searchsorted(iss, iss[i], side="right")
    s = aware(c.issued_at[i])
    prod_fire[i] = bool(ladder.nowcast(rain_call=c.forecast_text[i])["actions"])   # the issuance's own wording
    got = weather_history.select_forecast_as_of(rows[lo:hi], s, valid_at=s)   # production selector, as the route calls it
    if got is None: continue
    sel_idx[i] = got["id"]
    data = got["payload"]; value = data.get("forecast")                        # exactly as routes._lead_time_actions unpacks it
    text = value.get("text") if isinstance(value, dict) else value
    res = ladder.nowcast(rain_call=text if isinstance(text, str) else None)
    prod_fire_sel[i] = bool(res["actions"])
c["prod_fire"] = prod_fire; c["prod_fire_selected_row"] = prod_fire_sel; c["sel_idx"] = sel_idx
c[c.issued_at >= TEST_START].to_csv(OUT / "nowcast_issuances_test.csv", index=False)   # test rows only (full file is 26 MB)

def score(d, flag, name):
    ok = d.window_rain.notna(); m = d.loc[ok, flag]; y = d.loc[ok, "window_rain"]
    n = int(m.sum()); k = int(y[m].sum()); lo, hi = wilson(k, n)
    return dict(rule=name, issuances=int(ok.sum()), n_fired=n, pct_flagged=100 * m.mean(), precision=k / n, ci_lo=lo, ci_hi=hi,
                base=y.mean(), lift=(k / n) / y.mean())

res = []
scopes = {"train (<=2024-12-31)": c.issued_at < TEST_START,
          "TEST 2025-01-01..2026-08-19 (task spec)": (c.issued_at >= TEST_START) & (c.issued_at < TEST_END + pd.Timedelta(days=1)),
          "TEST 2025-01-01..file end 2026-09-28 (chapter scope)": c.issued_at >= TEST_START}
for sname, sel in scopes.items():
    d = c[sel]
    for flag, nm in (("nb_flag", "notebook non-Fair"), ("prod_fire", "production ladder.nowcast")):
        res.append({"scope": sname, **score(d, flag, nm)})
res = pd.DataFrame(res); res.to_csv(OUT / "nowcast_precision.csv", index=False)
print(res.round(4).to_string())
print("issuances where production selector at issue time did not return the issuance itself:", int((c.sel_idx != c.index).sum()),
      "| flag disagreements notebook vs production (own wording):", int((c.nb_flag != c.prod_fire).sum()),
      "| prod flag from selected row vs own wording differ:", int((c.prod_fire != c.prod_fire_selected_row).sum()))

# ---- onsets (notebook cell 31 definition) ----
s = rain_h.sort_index()
prev2_dry = (s.shift(1).fillna(0) < 0.2) & (s.shift(2).fillna(0) < 0.2)
onsets = s[(s >= 0.2) & prev2_dry].index
def lead_stats(flag_col, onsets_sel, label, scope):
    fl = c[c[flag_col]][["issued_at", "valid_from", "valid_to"]].sort_values("issued_at")
    leads_any, leads_strict = [], []
    for t in onsets_sel:
        cand = fl[(fl.valid_from <= t) & (fl.valid_to > t)]
        leads_any.append((t - cand.issued_at.min()).total_seconds() / 60 if len(cand) else np.nan)
        cs = cand[cand.issued_at < t]                      # usable strictly before the onset hour (production: available_at < t)
        leads_strict.append((t - cs.issued_at.min()).total_seconds() / 60 if len(cs) else np.nan)
    a, b = pd.Series(leads_any), pd.Series(leads_strict)
    pct_ge0 = 100 * (a >= 0).mean()
    n = len(a)
    q = lambda x, p_: float(x.dropna().quantile(p_)) if x.notna().any() else np.nan
    return dict(scope=scope, classifier=label, onsets=n, flagged_any=int(a.notna().sum()), pct_any=100 * a.notna().mean(),
                pct_lead_ge0=pct_ge0, median_any=q(a, .5), p25_any=q(a, .25), p75_any=q(a, .75),
                flagged_strict_before_onset_hour=int(b.notna().sum()), pct_strict=100 * b.notna().mean(),
                median_strict=q(b, .5), p25_strict=q(b, .25), p75_strict=q(b, .75))
orow = []
for scope, o_sel in (("all years (chapter: 1,722 onsets)", onsets),
                     ("TEST onsets 2025-01-01..2026-08-19", onsets[(onsets >= TEST_START) & (onsets < TEST_END + pd.Timedelta(days=1))])):
    for flag, label in (("nb_flag", "notebook non-Fair"), ("prod_fire", "production ladder.nowcast")):
        orow.append(lead_stats(flag, o_sel, label, scope))
orow = pd.DataFrame(orow); orow.to_csv(OUT / "nowcast_onsets.csv", index=False)
print(orow.round(2).to_string())
