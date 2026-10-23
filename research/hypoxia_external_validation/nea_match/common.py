"""Shared loaders. Archived data is read-only and never executed. Production code is imported unchanged."""
import sys, math
import numpy as np, pandas as pd
from pathlib import Path

BACKEND = r"C:\Users\AgenticDev\OutdoorKoiFYP\Backend"
NEA = Path(r"C:\Users\AgenticDev\OutdoorKoiFYP\NEA_Data_Analysis")
SCRAPE = Path(r"C:\Users\AGENTI~1\AppData\Local\Temp\claude\C--Users-AgenticDev-OutdoorKoiFYP-MobileUI-mobile-app\de35b01b-dbe6-47be-b171-edaa5666b681\scratchpad\nea_zip\Full FYP Dir\NEAForecastScraper\NEAForecastScraper\nea_forecast_data")
OUT = Path(__file__).resolve().parent
TEST_START = pd.Timestamp("2025-01-01"); TEST_END = pd.Timestamp("2026-08-19")

sys.path.insert(0, BACKEND)
from koi.models import ladder, forecast_utils           # production, unchanged
from koi.weather import history as weather_history      # production as-of selector
PARAMS = ladder.PARAMS

# ---- notebook cell 4 definitions (copied verbatim in meaning; used only for the OUTCOME and for the tier column) ----
TIERS = ["Fair", "Light", "Thundery", "Moderate", "Severe"]
TIER_RANK = {t: i for i, t in enumerate(TIERS)}
def to_tier(x):
    if pd.isna(x): return np.nan
    s = str(x).strip()
    if s in TIERS: return s
    t = s.lower()
    if "no rain" in t: return "Fair"
    if "heavy" in t or "severe" in t: return "Severe"
    if "moderate" in t: return "Moderate"
    if "thunder" in t: return "Thundery"
    if any(k in t for k in ("light", "shower", "drizzle", "passing", "rain")): return "Light"
    if any(k in t for k in ("fair", "cloud", "haz", "sunny", "wind", "mist")): return "Fair"
    return np.nan

def wilson(k, n, z=1.96):
    if n == 0: return (np.nan, np.nan)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)

def local(s):  # naive Asia/Singapore, as the archive stamps are +08:00
    return pd.to_datetime(s.str[:19])

def load_rain_h():
    w = pd.read_csv(NEA / "hourly_rainfall_wide.csv")
    t = pd.to_datetime(w["date"]) + pd.to_timedelta(w["hour"].astype(int), unit="h")
    return pd.Series(w["mean_mm"].values, index=t).sort_index()

def day_rain(rain_h):
    g = rain_h.groupby(rain_h.index.normalize())
    d = g.sum(min_count=1); d[g.count() < 20] = np.nan
    return d

def load_nowcast():
    c = pd.read_csv(NEA / "two_hr_forecast_woodlands.csv")
    for col in ["issued_at", "valid_from", "valid_to"]: c[col] = local(c[col])
    c["tier"] = c["forecast_text"].map(to_tier)
    c = (c.dropna(subset=["tier", "valid_from", "valid_to"]).drop_duplicates(["issued_at"], keep="last")
          .sort_values("issued_at").reset_index(drop=True))
    h0 = c.valid_from.dt.floor("h"); h1 = c.valid_to.dt.floor("h")
    n_hours = ((h1 - h0).dt.total_seconds() // 3600).astype(int) + 1
    return c, h0, n_hours

def window_rain(c, h0, n_hours, rain_h):
    mm = pd.Series(0.0, index=c.index); present = pd.Series(0, index=c.index)
    for k in range(int(n_hours.max())):
        hk = h0 + pd.to_timedelta(k, unit="h"); m = k < n_hours
        v = rain_h.reindex(hk[m]); mm.loc[m] += v.fillna(0).values; present.loc[m] += v.notna().astype(int).values
    mm = mm.where(present > 0)
    return mm, (mm >= 0.2).astype(float).where(mm.notna())
