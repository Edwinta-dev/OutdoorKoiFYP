"""WP1 step 1: external datasets -> production SensorData-shaped rows.

Output row shape (what registry/sensor_inputs.group_rows reads, the columns
of sensor_ingest_pending):
    {id, sensor_type, value, created_at, sampled_at, flags,
     device_id, boot_id, cycle_seq}
- sensor_type spellings are the production ones: "pH", "temp", "TDS".
  "LUX" is never emitted (no source has lux) and DO is never emitted
  (DO is a label only, AGENT_RULES / matrix section 5 rule 1).
- A channel that is NaN in the source, or dropped by an explicit named
  policy, has NO row: absent, not zero.
- created_at and sampled_at are aware UTC datetimes. External data has no
  database insert time, so created_at := sampled_at (zero-latency delivery;
  a named assumption). Hence time_basis == "sample".
- device_id/boot_id/cycle_seq are None (no source carries them), so rows
  group by effective time, the legacy path of sensor_inputs._group_key.
- id is unique and increases with the source file order.

Only CSV reads happen here (pandas); no code from the data is executed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
UTC = timezone.utc

FWI_ZERO_POLICIES = ("keep", "absent")


@dataclass
class SourceSpec:
    name: str
    rows: list
    time_zone: str                 # IANA zone used to interpret the source's local clock
    tz_status: str                 # documented | ASSUMED | UNKNOWN-assumed
    cadence_s: int                 # expected seconds between uploads (device_health interval)
    volume_l: Optional[float]      # None = unknown (do not invent)
    volume_source: str
    meta: dict = field(default_factory=dict)


def _row(i: int, sensor_type: str, value: float, t) -> dict:
    return {"id": int(i), "sensor_type": sensor_type, "value": float(value), "created_at": t, "sampled_at": t,
            "flags": None, "device_id": None, "boot_id": None, "cycle_seq": None}


def _utc_pydt(local_naive: pd.Series, tz: str):
    """Naive local timestamps -> aware UTC python datetimes."""
    aware = pd.DatetimeIndex(local_naive).tz_localize(tz).tz_convert("UTC")
    return list(aware.to_pydatetime())


def build_rows(times_utc: list, base_ids, channels: dict[str, pd.Series]) -> list[dict]:
    """channels: sensor_type -> float Series aligned to times_utc; NaN -> no row.
    The k-th channel of source row i gets id base_ids[i]*4 + k (k in 0..3), so
    ids are unique and ordered by source row."""
    rows: list[dict] = []
    for k, (stype, series) in enumerate(channels.items()):
        vals = series.to_numpy(dtype=float)
        for i in range(len(times_utc)):
            v = vals[i]
            if v != v:  # NaN
                continue
            rows.append(_row(int(base_ids[i]) * 4 + k, stype, v, times_utc[i]))
    rows.sort(key=lambda r: r["id"])
    return rows


# ----------------------------------------------------------------------
# FWI
# ----------------------------------------------------------------------
def fwi_ponds() -> list[str]:
    return sorted(p.stem for p in (SRC / "fwi" / "csv").glob("ara2_*.csv"))


def load_fwi_frame(pond: str) -> pd.DataFrame:
    d = pd.read_csv(SRC / "fwi" / "csv" / f"{pond}.csv")
    d.columns = ["ts", "do", "ph", "temp", "qc_dt", "qc_do", "qc_ph"]
    d["t_local"] = pd.to_datetime(d["ts"])
    d["src_index"] = range(len(d))
    return d


def fwi_source(pond: str, zero_policy: str = "keep", frame: Optional[pd.DataFrame] = None) -> SourceSpec:
    """One FWI pond. IST (Asia/Kolkata, documented). DO is read by load_fwi_frame
    only so QC columns can be joined later; it never enters `rows`.
    zero_policy: keep   = rows as published (0.0 pH/temp go to the gate);
                 absent = the README's documented reset-zero artefacts become absent channels."""
    if zero_policy not in FWI_ZERO_POLICIES:
        raise ValueError(zero_policy)
    d = frame if frame is not None else load_fwi_frame(pond)
    ph, temp = d["ph"].astype(float).copy(), d["temp"].astype(float).copy()
    zeros = {"ph": int((ph == 0).sum()), "temp": int((temp == 0).sum())}
    if zero_policy == "absent":
        ph[ph == 0] = float("nan")
        temp[temp == 0] = float("nan")
    times = _utc_pydt(d["t_local"], "Asia/Kolkata")
    rows = build_rows(times, d["src_index"].to_numpy(), {"pH": ph, "temp": temp})
    return SourceSpec(
        name=f"fwi:{pond}:zeros-{zero_policy}", rows=rows, time_zone="Asia/Kolkata", tz_status="documented (IST)",
        cadence_s=900, volume_l=None, volume_source="unknown (FWI does not publish pond volume)",
        meta={"pond": pond, "source_rows": len(d), "zero_values_in_source": zeros, "zero_policy": zero_policy,
              "channels": ["pH", "temp"], "DO_fed": False})


# ----------------------------------------------------------------------
# Oman
# ----------------------------------------------------------------------
OMAN_FILES = {"open": SRC / "oman" / "data" / "raw" / "offline" / "raw_readings.csv",
              "tank": SRC / "oman" / "data" / "raw" / "live" / "raw_readings_new.csv"}
OMAN_TZ = "Asia/Muscat"  # fixed GMT+4, documented in data_dictionary.md


def load_oman_frame(which: str) -> pd.DataFrame:
    d = pd.read_csv(OMAN_FILES[which])
    d["t_local"] = pd.to_datetime(d["timestamp"], format="ISO8601")
    d["src_index"] = range(len(d))
    return d


def oman_source(which: str, grid: str = "native", frame: Optional[pd.DataFrame] = None) -> SourceSpec:
    """which: open (open pond, volume unknown) | tank (180 L live tilapia tank, documented).
    grid: native = every 5-7 s row; 5min = the last real row of each 5-minute bin
    (selection of an existing sample, no averaging, no new value)."""
    d = frame if frame is not None else load_oman_frame(which)
    n_src = len(d)
    if grid == "5min":
        d = d.sort_values(["t_local", "src_index"], kind="stable")
        d = d.groupby(d["t_local"].dt.floor("5min"), sort=True).tail(1).sort_values("src_index")
    elif grid != "native":
        raise ValueError(grid)
    times = _utc_pydt(d["t_local"], OMAN_TZ)
    rows = build_rows(times, d["src_index"].to_numpy(),
                      {"pH": d["ph"].astype(float), "temp": d["temp_c"].astype(float)})
    tank = which == "tank"
    return SourceSpec(
        name=f"oman:{which}:{grid}", rows=rows, time_zone=OMAN_TZ, tz_status="documented (GMT+4)",
        cadence_s=6 if grid == "native" else 300,
        volume_l=180.0 if tank else None,
        volume_source="data_dictionary: controlled 180 L tank" if tank else "unknown (open pond)",
        meta={"which": which, "grid": grid, "source_rows": n_src, "rows_after_grid": len(d),
              "channels": ["pH", "temp"], "DO_fed": False})


def oman_tank_events() -> list[dict]:
    """The tank-run events of event_logs_new.csv that map onto a production
    event_type, as pondInterventions-shaped rows. Only documented quantities:
    water-change percentage is in the event name; FEEDING has NO quantity
    (food_grams None, retained as missing). Events outside the tank window and
    non-mappable types (Other, Actuator, Fish Weight, Maintenance) are returned
    as `skipped` counts by oman_tank_events_report. event_timestamp is start_time
    in GMT+4 -> aware UTC; created_at (when the operator logged it) is
    timestamp_logged, so an event is not visible before it was logged."""
    from koi.models.event_ledger import derived_event_id
    e = pd.read_csv(SRC / "oman" / "data" / "events" / "event_logs_new.csv")
    tank = load_oman_frame("tank")
    t0 = pd.Timestamp(tank["t_local"].min())
    t1 = pd.Timestamp(tank["t_local"].max())
    out = []
    for _, r in e.iterrows():
        start = pd.Timestamp(r["start_time"])
        if not (t0 <= start <= t1):
            continue
        name = str(r["event"])
        etype, pct = None, None
        if name.startswith("Water Change:"):
            etype, pct = "WATER_CHANGE", float(name.split(":")[1].strip().rstrip("%"))
        elif name == "Feeding":
            etype = "FEEDING"
        if etype is None:
            continue
        at = start.tz_localize(OMAN_TZ).tz_convert("UTC").to_pydatetime()
        logged = pd.Timestamp(r["timestamp_logged"]).tz_localize(OMAN_TZ).tz_convert("UTC").to_pydatetime()
        out.append({"id": int(r["id"]), "event_id": derived_event_id(int(r["id"])), "event_type": etype,
                    "event_timestamp": at, "created_at": logged, "volume_percentage": pct, "volume_litres": None,
                    "algae_method": None, "food_grams": None, "protein_percentage": None, "salt_grams": None,
                    "notes": str(r["note"]) if pd.notna(r["note"]) else None})
    return out


def oman_tank_events_report() -> dict:
    e = pd.read_csv(SRC / "oman" / "data" / "events" / "event_logs_new.csv")
    tank = load_oman_frame("tank")
    t0, t1 = pd.Timestamp(tank["t_local"].min()), pd.Timestamp(tank["t_local"].max())
    in_win = e[(pd.to_datetime(e["start_time"]) >= t0) & (pd.to_datetime(e["start_time"]) <= t1)]
    kinds = in_win["event"].str.replace(r":.*", "", regex=True).value_counts().to_dict()
    mapped = oman_tank_events()
    return {"events_total": len(e), "events_in_tank_window": len(in_win), "kinds_in_window": kinds,
            "mapped_to_production_types": len(mapped),
            "mapped_by_type": pd.Series([m["event_type"] for m in mapped]).value_counts().to_dict()}


# ----------------------------------------------------------------------
# IDN-T
# ----------------------------------------------------------------------
IDN_FILES = {"raw": SRC / "idn_t" / "pond_iot_2023_raw.csv", "clean": SRC / "idn_t" / "pond_iot_2023.csv"}
IDN_TZ_ASSUMPTIONS = {"jakarta": "Asia/Jakarta", "utc": "UTC"}
IDN_VOLUME_L = 700.0


def load_idn_frame(which: str) -> pd.DataFrame:
    d = pd.read_csv(IDN_FILES[which], encoding="utf-8-sig")
    d["t_local"] = pd.to_datetime(d["created_date"], format="%m/%d/%Y %H:%M")
    return d


def idn_source(which: str = "raw", tz_assumption: str = "jakarta", frame: Optional[pd.DataFrame] = None) -> SourceSpec:
    """IDN-T (Mendeley yd36bx6f8f v2). Timezone is UNKNOWN in the dataset: the
    primary run ASSUMES Asia/Jakarta (WIB, UTC+7); the sensitivity run assumes
    the stamps are already UTC. Source `id` is kept (id*4+k keeps order).
    Minute resolution: several source rows share one minute, so rows of the same
    channel collide in one sensor_inputs group (highest id wins, rest superseded)."""
    d = frame if frame is not None else load_idn_frame(which)
    tz = IDN_TZ_ASSUMPTIONS[tz_assumption]
    times = _utc_pydt(d["t_local"], tz)
    rows = build_rows(times, d["id"].to_numpy(),
                      {"pH": d["water_pH"].astype(float), "TDS": d["TDS"].astype(float),
                       "temp": d["water_temp"].astype(float)})
    return SourceSpec(
        name=f"idn_t:{which}:tz-{tz_assumption}", rows=rows, time_zone=tz,
        tz_status="UNKNOWN in dataset; ASSUMED " + tz, cadence_s=900,
        volume_l=IDN_VOLUME_L, volume_source="matrix section 2/4: ~700 L documented (methods paper)",
        meta={"which": which, "tz_assumption": tz_assumption, "source_rows": len(d),
              "channels": ["pH", "TDS", "temp"], "DO_fed": False})
