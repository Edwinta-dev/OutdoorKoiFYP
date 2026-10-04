"""
sensor_inputs.py

Turns discovered SensorData rows into the model's sensor inputs, in
event-time order, and judges how fresh each channel is. Pure: no I/O.
The poller (koi/worker/poller.py) discovers the rows through storage
(sensor_ingest_pending, migration 0014) and hands them here.

Two clocks are kept apart:

  * Ingestion progress: which rows have been read. That is the ledger
    and the watermark in storage, keyed on SensorData.created_at, the
    database insert time. Nothing here moves it.
  * Model event time: when a reading describes the pond. Its
    effective_sample_time is the node's sample time when the row has one
    (sampled_at, issue #17) and created_at otherwise. Until #17 every
    reading has time_basis "ingestion": the time is when the database
    stored it, not when the probe sampled, and every time reported from
    here says so.

Inputs are ordered by (effective_sample_time, id). The node uploads one
cycle's readings in a single insert, so they share created_at; the rows
at one effective_sample_time form one input, holding the channels that
were sent and None for the ones that were not. If a channel appears
twice at the same time, the row with the higher id is used and the other
is recorded as superseded. Grouping rows into device cycles by device id
and cycle number, and placing readings that arrive late by their sample
time, belong to issue #82: it changes effective_sample_time and
_group_key below, and nothing else here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

TIME_BASIS_INGESTION = "ingestion"
TIME_BASIS_SAMPLE = "sample"

# Model channel -> SensorData.sensor_type spellings (the same as the
# dashboard's, koi/api/dashboard.py). Rows of any other type are not read.
CHANNEL_TYPES: dict[str, tuple[str, ...]] = {
    "ph": ("pH",),
    "tds": ("TDS",),
    "temp": ("temp",),
    "lux": ("LUX", "lux"),
}
CHANNELS = tuple(CHANNEL_TYPES)
SENSOR_TYPES = tuple(t for types in CHANNEL_TYPES.values() for t in types)
_CHANNEL_OF = {t: channel for channel, types in CHANNEL_TYPES.items() for t in types}



@dataclass(frozen=True)
class IngestConfig:
    """The poller's ingestion settings (Settings.sensor_ingest): the
    node's expected minutes between uploads, how many minutes each scan
    overlaps the newest ingested insert time, and the most rows a pond
    ingests per cycle."""
    cadence_minutes: int = 15
    overlap_minutes: int = 60
    batch_rows: int = 2000

    @property
    def cadence(self) -> timedelta:
        return timedelta(minutes=self.cadence_minutes)


# Ledger dispositions (sensor_ingest_ledger.disposition).
APPLIED = "applied"
SUPERSEDED = "superseded"
UNUSABLE = "unusable"


def parse_timestamp(value: object) -> datetime:
    """A timestamp column value (ISO string or datetime) as an aware
    datetime; one without a zone is taken as UTC."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def effective_sample_time(row: dict) -> tuple[datetime, str]:
    """(time, time_basis) of one SensorData row: its sample time when it
    carries one, else its insert time, labelled as ingestion time."""
    sampled = row.get("sampled_at")
    if sampled is not None:
        return parse_timestamp(sampled), TIME_BASIS_SAMPLE
    return parse_timestamp(row["created_at"]), TIME_BASIS_INGESTION


def _group_key(row: dict) -> datetime:
    return effective_sample_time(row)[0]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


@dataclass(frozen=True)
class SensorInput:
    """One upload of the node: every channel it sent at one time.
    channels holds a value or None (not sent, or not a number) for each
    of CHANNELS; row_ids are the SensorData rows used."""
    time: datetime
    time_basis: str
    channels: dict[str, Optional[float]]
    row_ids: dict[str, int]

    @property
    def missing(self) -> list[str]:
        return [c for c in CHANNELS if self.channels.get(c) is None]

    def to_dict(self) -> dict:
        return {"time": self.time.isoformat(), "time_basis": self.time_basis,
                "channels": dict(self.channels), "row_ids": dict(self.row_ids)}


@dataclass
class Discovery:
    """The inputs found in one scan, in event-time order, and what to
    record for every row read: ledger rows (sensor_ingest_ledger) and the
    newest created_at among them (the watermark)."""
    inputs: list[SensorInput] = field(default_factory=list)
    ledger: list[dict] = field(default_factory=list)
    watermark: Optional[datetime] = None

    def commit(self) -> Optional[dict]:
        """The p_ingest argument of save_pond_snapshot_with_ingest, or
        None when nothing was read."""
        if not self.ledger:
            return None
        return {"rows": self.ledger, "watermark": self.watermark.isoformat() if self.watermark else None}


def group_rows(rows: list[dict]) -> Discovery:
    """Rows of one pond ({id, sensor_type, value, created_at}, plus
    sampled_at once #17 adds it) -> the inputs, oldest first. Rows of an
    unknown sensor_type are left out and not recorded."""
    known = [r for r in rows if r.get("sensor_type") in _CHANNEL_OF]
    known.sort(key=lambda r: (_group_key(r), int(r["id"])))
    discovery = Discovery()
    for r in known:
        created = parse_timestamp(r["created_at"])
        if discovery.watermark is None or created > discovery.watermark:
            discovery.watermark = created

    groups: dict[datetime, list[dict]] = {}
    for r in known:
        groups.setdefault(_group_key(r), []).append(r)

    for time, members in groups.items():
        basis = effective_sample_time(members[0])[1]
        channels: dict[str, Optional[float]] = {c: None for c in CHANNELS}
        row_ids: dict[str, int] = {}
        dispositions: dict[int, str] = {}
        # Highest id last, so it wins a channel that appears twice.
        for r in members:
            channel = _CHANNEL_OF[r["sensor_type"]]
            value = _number(r.get("value"))
            if value is None:
                dispositions[int(r["id"])] = UNUSABLE
                continue
            if channel in row_ids:
                dispositions[row_ids[channel]] = SUPERSEDED
            channels[channel] = value
            row_ids[channel] = int(r["id"])
            dispositions[int(r["id"])] = APPLIED
        discovery.inputs.append(SensorInput(time, basis, channels, row_ids))
        for r in members:
            discovery.ledger.append({
                "sensor_row_id": int(r["id"]),
                "sensor_type": r["sensor_type"],
                "effective_sample_time": time.isoformat(),
                "time_basis": effective_sample_time(r)[1],
                "disposition": dispositions[int(r["id"])],
            })
    return discovery


def complete_groups(rows: list[dict], limit: int) -> list[dict]:
    """Rows from a scan that stopped at limit: drops the rows of the last
    insert time, which may continue past the limit, so an upload is never
    split across two cycles. A scan below the limit, or one that is a
    single insert time, is kept whole."""
    if len(rows) < limit or not rows:
        return rows
    last = parse_timestamp(rows[-1]["created_at"])
    kept = [r for r in rows if parse_timestamp(r["created_at"]) < last]
    return kept or rows


# ----------------------------------------------------------------------
# Freshness
# ----------------------------------------------------------------------
def fresh_for(cadence: timedelta) -> timedelta:
    """How long a channel's newest reading counts as current: two of the
    node's expected upload intervals, so one missed upload is tolerated."""
    return 2 * cadence


def channel_freshness(channels: dict[str, dict], now: datetime, cadence: timedelta) -> dict[str, dict]:
    """Each channel's newest reading (PondTwin.sensor_channels) with its
    age at now and whether it is fresh. Judged from the reading's own
    time against the expected cadence, never from how many polls have
    seen it. A channel with no reading is {fresh: False, reading_at:
    None}."""
    limit = fresh_for(cadence)
    out: dict[str, dict[str, Any]] = {}
    for channel in CHANNELS:
        state = channels.get(channel)
        if not state or state.get("reading_at") is None:
            out[channel] = {"value": None, "reading_at": None, "time_basis": None, "age_minutes": None,
                            "fresh": False}
            continue
        age = now - parse_timestamp(state["reading_at"])
        out[channel] = {"value": state.get("value"), "reading_at": state["reading_at"],
                        "time_basis": state.get("time_basis", TIME_BASIS_INGESTION),
                        "age_minutes": round(age.total_seconds() / 60.0, 1),
                        "fresh": age <= limit}
    return out


def fresh_value(freshness: dict[str, dict], channel: str) -> Optional[float]:
    """The channel's newest value if it is fresh, else None."""
    state = freshness.get(channel) or {}
    return state.get("value") if state.get("fresh") else None
