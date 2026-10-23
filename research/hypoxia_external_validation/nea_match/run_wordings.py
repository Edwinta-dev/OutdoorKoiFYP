"""Task 3: classify every distinct archived wording with production code and compare with the notebook tier order."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

srcs = {"nowcast_2hr": pd.read_csv(NEA / "two_hr_forecast_woodlands.csv").forecast_text,
        "periods_24h": pd.read_csv(SCRAPE / "twenty_four_hr_periods.csv").forecast_text,
        "general_24h": pd.read_csv(SCRAPE / "twenty_four_hr_general.csv").forecast_text,
        "outlook_4day": pd.read_csv(SCRAPE / "four_day_outlook.csv").forecast_text}
counts = pd.DataFrame({k: v.value_counts() for k, v in srcs.items()}).fillna(0).astype(int)
counts["total"] = counts.sum(axis=1)
INTENSITY = {"unknown": 0, "moderate": 1, "heavy": 2}
rows = []
for w in counts.index:
    tier = to_tier(w); inc, inten = forecast_utils.rain_context_from_text(w)
    fires = bool(ladder.nowcast(rain_call=w)["actions"])
    rows.append(dict(wording=w, **{k: int(counts.loc[w, k]) for k in srcs}, total=int(counts.loc[w, "total"]), notebook_tier=tier, notebook_rank=TIER_RANK[tier],
                     notebook_nonFair=tier != "Fair", prod_rain_incoming=inc, prod_intensity=inten,
                     prod_intensity_rank=INTENSITY[inten], ladder_nowcast_fires=fires))
t = pd.DataFrame(rows).sort_values(["notebook_rank", "total"], ascending=[True, False])
t["nonFair_agrees"] = t.notebook_nonFair == t.ladder_nowcast_fires
t["incoming_agrees_nonFair"] = t.notebook_nonFair == t.prod_rain_incoming
t.to_csv(OUT / "wording_classification.csv", index=False)
pd.set_option("display.width", 260, "display.max_colwidth", 45)
print(t[["wording", "nowcast_2hr", "periods_24h", "outlook_4day", "notebook_tier", "notebook_rank", "prod_rain_incoming",
         "prod_intensity", "ladder_nowcast_fires", "nonFair_agrees"]].to_string(index=False))

# order check: pairs whose notebook tier order is strict but production intensity order disagrees or ties
pairs = []
tiers = t.groupby("notebook_tier").agg(prod_intensities=("prod_intensity", lambda s: sorted(set(s))), n=("total", "sum")).reindex(TIERS)
print(tiers)
tiers.to_csv(OUT / "tier_vs_production_intensity.csv")
ins = t[~t.nonFair_agrees]; print("Non-Fair disagreements:\n", ins[["wording", "notebook_tier", "ladder_nowcast_fires"]])
