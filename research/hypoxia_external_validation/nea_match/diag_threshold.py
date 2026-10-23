"""Diagnostic only (not a result): why ladder_params.json says 31.86 where the notebook printed 31.80."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
c = pd.read_csv(NEA / "daily_climate.csv"); c["date"] = pd.to_datetime(c.date)
tr = c[c.date <= "2024-12-31"].t_max; z = (tr.quantile(.7) - tr.mean()) / tr.std()
for end in ("2026-08-20", "2026-09-28"):
    new = c[(c.date <= end) & ~c.source.astype(str).str.contains("cluster")].t_max
    print("new-regime rows through", end, len(new), "z-matched threshold", round(new.mean() + z * new.std(), 3))
print("old hot threshold (train q0.70)", round(tr.quantile(.7), 3), "| days in test with new-regime t_max in [31.80,31.86):",
      int(((c.date > "2024-12-31") & ~c.source.astype(str).str.contains("cluster") & (c.t_max >= 31.8) & (c.t_max < 31.86)).sum()))
print("last climate date", c.date.max().date())
