"""Builds the per-day inputs the ladder needs, from the archive, respecting issue times.
NOTHING here is production code: production never computes these inputs (see REPORT.md, section 1).
The construction follows the notebook definitions (cells 8, 12) so only the decision time differs."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

REGIONS = ["north", "south", "east", "west", "central"]

def load_periods():
    p = pd.read_csv(SCRAPE / "twenty_four_hr_periods.csv")
    p["iss"] = local(p.issued_at); p["ps"] = local(p.period_start)
    p["date"] = p.ps.dt.normalize(); p["block"] = p.ps.dt.hour // 6
    p["region"] = p.region.astype(str).str.strip().str.lower()
    p["tier"] = p.forecast_text.map(to_tier); p["rank"] = p.tier.map(TIER_RANK)
    p["order"] = np.arange(len(p))
    return p.dropna(subset=["date", "tier"])

def pooled_rank_notebook(p):
    """notebook cell 8/12 as written: dedupe (date,block,region) keep='last' IN FILE ORDER, no issue-time filter."""
    q = p.drop_duplicates(["date", "block", "region"], keep="last")
    piv = q.pivot(index=["date", "block"], columns="region", values="rank")[REGIONS]
    return piv.groupby(level="date").mean().mean(axis=1)

def pooled_rank_asof(p, hours_after_midnight):
    """Latest issuance (by issued_at) at or before D + hours, per (block, region); same pooling as the notebook."""
    out = {}
    for hh in [hours_after_midnight]:
        T = p.date + pd.Timedelta(hours=hh)
        q = p[p.iss <= T].sort_values(["iss", "order"]).drop_duplicates(["date", "block", "region"], keep="last")
        piv = q.pivot(index=["date", "block"], columns="region", values="rank").reindex(columns=REGIONS)
        return piv.groupby(level="date").mean().mean(axis=1), q.groupby("date").size()

def load_outlook():
    o = pd.read_csv(SCRAPE / "four_day_outlook.csv")
    o["iss"] = local(o.issued_at); o["qd"] = pd.to_datetime(o.query_date); o["target"] = pd.to_datetime(o.forecast_for_date)
    o["lead"] = (o.target - o.qd).dt.days; o = o[o.lead.between(1, 4)].copy()
    o["mid"] = (pd.to_numeric(o.temp_low, errors="coerce") + pd.to_numeric(o.temp_high, errors="coerce")) / 2
    o["high"] = pd.to_numeric(o.temp_high, errors="coerce"); o["order"] = np.arange(len(o))
    return o

def outlook_notebook(o):
    q = o.drop_duplicates(["qd", "lead"], keep="last")
    return q.groupby("qd").mid.mean().rename("mid_nb")  # mean over the available leads, as skipna mean of out_t1..4

def outlook_asof(o, hour=23.99):
    """Per day D, the latest outlook issuance with issued_at <= D+hour; mean of its lead 1..4 midpoints (and highs)."""
    T = o.qd + pd.Timedelta(hours=hour)
    q = o[o.iss <= T].sort_values(["iss", "order"])
    last = q.groupby("qd").iss.transform("max"); q = q[q.iss == last]
    q = q.drop_duplicates(["qd", "lead"], keep="last")
    g = q.groupby("qd")
    return pd.DataFrame({"mid": g.mid.mean(), "high": g.high.mean(), "n_leads": g.size(), "iss": g.iss.max()})

def load_climate():
    c = pd.read_csv(NEA / "daily_climate.csv"); c["date"] = pd.to_datetime(c.date)
    c = c.set_index("date")
    c["regime"] = np.where(c.source.astype(str).str.contains("cluster"), "old", "new")
    return c[["t_max", "regime", "source"]]
