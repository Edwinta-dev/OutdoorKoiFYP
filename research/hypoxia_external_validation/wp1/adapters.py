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

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
UTC = timezone.utc

FWI_ZERO_POLICIES = ("keep", "absent")


def thinned(spec: "SourceSpec", seconds: int) -> "SourceSpec":
    """Same source, delivered at a coarser cadence (real rows only). Named assumption
    for cadence-mismatch sensitivity; cadence_s is set to `seconds`."""
    import copy
    s2 = copy.copy(spec)
    s2.rows = spec.rows.thin(seconds)
    s2.name = spec.name + f":thin{seconds // 60}min"
    s2.cadence_s = seconds
    s2.meta = {**spec.meta, "thinned_to_s": seconds, "rows_after_thin": len(s2.rows)}
    return s2


@dataclass
class SourceSpec:
    name: str
    rows: "RowTable"
    time_zone: str                 # IANA zone used to interpret the source's local clock
    tz_status: str                 # documented | ASSUMED | UNKNOWN-assumed
    cadence_s: int                 # expected seconds between uploads (device_health interval)
    volume_l: Optional[float]      # None = unknown (do not invent)
    volume_source: str
    meta: dict = field(default_factory=dict)


TYPES = ("pH", "temp", "TDS")  # sensor_type spellings emitted (production spellings)


class RowTable:
    """Columnar store of SensorData-shaped rows (memory: the full IDN-T raw file is
    ~1.5 M rows, too many dicts for this machine). Rows materialise as dicts only
    when sliced/iterated: {id, sensor_type, value, created_at, sampled_at, flags,
    device_id, boot_id, cycle_seq}."""

    def __init__(self, ids, tcode, vals, tns):
        self.ids = np.asarray(ids, dtype=np.int64)
        self.tcode = np.asarray(tcode, dtype=np.int8)
        self.vals = np.asarray(vals, dtype=np.float64)
        self.tns = np.asarray(tns, dtype=np.int64)  # UTC epoch nanoseconds

    def __len__(self):
        return len(self.ids)

    def take(self, idx) -> list:
        idx = np.asarray(idx, dtype=np.int64)
        if len(idx) == 0:
            return []
        times = list(pd.to_datetime(self.tns[idx], unit="ns", utc=True).to_pydatetime())
        ids, tc, vals = self.ids[idx].tolist(), self.tcode[idx].tolist(), self.vals[idx].tolist()
        return [{"id": ids[j], "sensor_type": TYPES[tc[j]], "value": vals[j], "created_at": times[j],
                 "sampled_at": times[j], "flags": None, "device_id": None, "boot_id": None, "cycle_seq": None}
                for j in range(len(ids))]

    def __getitem__(self, k):
        if isinstance(k, slice):
            return self.take(np.arange(len(self))[k])
        return self.take([k % len(self)])[0]

    def __iter__(self):
        for a in range(0, len(self), 50000):
            yield from self.take(np.arange(a, min(a + 50000, len(self))))

    def subset(self, idx) -> "RowTable":
        idx = np.asarray(idx)
        return RowTable(self.ids[idx], self.tcode[idx], self.vals[idx], self.tns[idx])

    def sorted_by_time(self) -> "RowTable":
        """Scan order of the poller: (created_at, id)."""
        return self.subset(np.lexsort((self.ids, self.tns)))

    def thin(self, seconds: int) -> "RowTable":
        """Keep, per `seconds`-bin of the clock, only the rows of the bin's LAST
        distinct sample instant (existing rows, nothing averaged). Gives the
        production-like 15-minute cadence from minute/second-rate sources."""
        inst = np.unique(self.tns)
        b = inst // (seconds * 10**9)
        last = np.r_[b[1:] != b[:-1], True]
        return self.subset(np.nonzero(np.isin(self.tns, inst[last]))[0])

    def concat(self, other: "RowTable") -> "RowTable":
        return RowTable(*(np.concatenate([a, b]) for a, b in
                          ((self.ids, other.ids), (self.tcode, other.tcode), (self.vals, other.vals), (self.tns, other.tns))))


def _utc_index(local_naive, tz: str) -> pd.DatetimeIndex:
    """Naive local timestamps -> tz-aware UTC DatetimeIndex."""
    return pd.DatetimeIndex(local_naive).tz_localize(tz).tz_convert("UTC")


def build_rows(times_utc: pd.DatetimeIndex, base_ids, channels: dict) -> RowTable:
    """channels: sensor_type -> float Series aligned to times_utc; NaN -> no row.
    The channel of source row i gets id base_ids[i]*4 + TYPES.index(type), so ids are
    unique and ordered by source row. Result is in id order."""
    tns = times_utc.as_unit("ns").asi8
    base = np.asarray(base_ids, dtype=np.int64)
    parts = []
    for stype, series in channels.items():
        k = TYPES.index(stype)
        vals = series.to_numpy(dtype=float)
        keep = ~np.isnan(vals)
        parts.append((base[keep] * 4 + k, np.full(int(keep.sum()), k), vals[keep], tns[keep]))
    ids, tc, vals, t = (np.concatenate([p[j] for p in parts]) for j in range(4))
    order = np.argsort(ids, kind="stable")
    return RowTable(ids[order], tc[order], vals[order], t[order])


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
    times = _utc_index(d["t_local"], "Asia/Kolkata")
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
    times = _utc_index(d["t_local"], OMAN_TZ)
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
    times = _utc_index(d["t_local"], tz)
    rows = build_rows(times, d["id"].to_numpy(),
                      {"pH": d["water_pH"].astype(float), "TDS": d["TDS"].astype(float),
                       "temp": d["water_temp"].astype(float)})
    return SourceSpec(
        name=f"idn_t:{which}:tz-{tz_assumption}", rows=rows, time_zone=tz,
        tz_status="UNKNOWN in dataset; ASSUMED " + tz, cadence_s=900,
        volume_l=IDN_VOLUME_L, volume_source="matrix section 2/4: ~700 L documented (methods paper)",
        meta={"which": which, "tz_assumption": tz_assumption, "source_rows": len(d),
              "channels": ["pH", "TDS", "temp"], "DO_fed": False})
