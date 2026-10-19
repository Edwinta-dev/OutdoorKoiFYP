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
    (sampled_at, payload v2, migration 0027) and created_at otherwise.
    A row without sampled_at (older firmware, or a node whose clock was
    not set) has time_basis "ingestion": the time is when the database
    stored it, not when the probe sampled, and every time reported from
    here says so.

Inputs are ordered by (effective_sample_time, id). Complete v2 identities
group by device, boot and cycle; legacy rows group by effective time.
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
# A late reading older than the replay window (event_ledger.WINDOW): no
# checkpoint is left to place it at its sample time, so the model leaves
# it out rather than apply it at a time it does not describe (#82).
OUT_OF_WINDOW = "out_of_window"


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


def _group_key(row: dict) -> tuple:
    device_id, boot_id, cycle_seq = row.get("device_id"), row.get("boot_id"), row.get("cycle_seq")
    if device_id is not None and boot_id is not None and cycle_seq is not None:
        return ("cycle", str(device_id), str(boot_id), int(cycle_seq))
    return ("time", effective_sample_time(row)[0])


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
    device_id: Optional[str] = None
    boot_id: Optional[str] = None
    cycle_seq: Optional[int] = None

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
    newest created_at among them (the watermark). received holds each
    distinct created_at read, oldest first: one per upload of the node,
    recorded as its contacts (koi/models/device_health.py)."""
    inputs: list[SensorInput] = field(default_factory=list)
    ledger: list[dict] = field(default_factory=list)
    watermark: Optional[datetime] = None
    received: list[datetime] = field(default_factory=list)

    def commit(self) -> Optional[dict]:
        """The p_ingest argument of save_pond_snapshot_with_ingest, or
        None when nothing was read."""
        if not self.ledger:
            return None
        return {"rows": self.ledger, "watermark": self.watermark.isoformat() if self.watermark else None}


def group_rows(rows: list[dict]) -> Discovery:
    """Rows of one pond ({id, sensor_type, value, created_at, sampled_at,
    flags}; the last two may be absent or null) -> the inputs, oldest
    first. Rows of an unknown sensor_type are left out and not recorded."""
    known = [r for r in rows if r.get("sensor_type") in _CHANNEL_OF]
    discovery = Discovery()
    for r in known:
        created = parse_timestamp(r["created_at"])
        if discovery.watermark is None or created > discovery.watermark:
            discovery.watermark = created
    discovery.received = sorted({parse_timestamp(r["created_at"]) for r in known})

    groups: dict[tuple, list[dict]] = {}
    for r in known:
        groups.setdefault(_group_key(r), []).append(r)

    ordered_groups = sorted(groups.items(), key=lambda item: (
        min(effective_sample_time(r)[0] for r in item[1]),
        min(int(r["id"]) for r in item[1])))
    for key, members in ordered_groups:
        members.sort(key=lambda r: int(r["id"]))
        first = min(members, key=lambda r: (effective_sample_time(r)[0], int(r["id"])))
        time, basis = effective_sample_time(first)
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
        cycle_key = key[0] == "cycle"
        discovery.inputs.append(SensorInput(time, basis, channels, row_ids,
                                            str(first["device_id"]) if cycle_key else None,
                                            str(first["boot_id"]) if cycle_key else None,
                                            int(first["cycle_seq"]) if cycle_key else None))
        for r in members:
            discovery.ledger.append({
                "sensor_row_id": int(r["id"]),
                "sensor_type": r["sensor_type"],
                "effective_sample_time": time.isoformat(),
                "time_basis": effective_sample_time(r)[1],
                "disposition": dispositions[int(r["id"])],
            })
    return discovery


def input_key(item: SensorInput) -> tuple:
    """The grouping key of an input, as _group_key gives it for its rows."""
    if item.device_id is not None and item.boot_id is not None and item.cycle_seq is not None:
        return ("cycle", item.device_id, item.boot_id, item.cycle_seq)
    return ("time", item.time)


def merge_inputs(inputs: list[SensorInput]) -> list[SensorInput]:
    """Inputs with the same key as one input, oldest first: a cycle whose
    rows were read across two polls (a scan's limit, or a store-and-forward
    retry) is put back together, as group_rows would have grouped it had
    all its rows been read at once. Per channel the highest row id wins;
    the time is the earliest member's."""
    merged: dict[tuple, SensorInput] = {}
    for item in inputs:
        key = input_key(item)
        prior = merged.get(key)
        if prior is None:
            merged[key] = item
            continue
        channels, row_ids = dict(prior.channels), dict(prior.row_ids)
        for channel, row_id in item.row_ids.items():
            if channel not in row_ids or row_id > row_ids[channel]:
                channels[channel], row_ids[channel] = item.channels.get(channel), row_id
        first = prior if prior.time <= item.time else item
        merged[key] = SensorInput(first.time, first.time_basis, channels, row_ids,
                                  prior.device_id, prior.boot_id, prior.cycle_seq)
    return sorted(merged.values(), key=lambda i: (i.time, min(i.row_ids.values(), default=0)))


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
