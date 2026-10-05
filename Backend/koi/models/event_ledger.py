"""
event_ledger.py

The twin's record of every logged intervention it has applied, and the
daily checkpoints that let it apply one in the past (issue #19). Pure: no
I/O. PondTwin (pond_twin.py) owns one EventLedger and keeps it in its
snapshot; the poller and the event endpoints go through PondTwin.

--------------------------------------------------------------------
IDENTITY
--------------------------------------------------------------------
Each event has a stable UUID, event_id: the app generates it, writes it
to pondInterventions.event_id and posts the same value to the twin
(migration 0015). The integer pondInterventions.id is the row's key and
is kept apart: an entry records it as row_id once the row has been seen.

  * An event_id already in the ledger is never applied again. A repeated
    post is a no-op that returns the current assessment.
  * A post without an event_id (an app build from before #19) is
    recorded under a "legacy:<n>" key. The first pondInterventions row
    of the same kind and amounts within LEGACY_MATCH of its time, that
    is not in the ledger, takes it over instead of being applied again.
    The tolerance covers the app writing the row's time as phone-local
    wall time while posting the same instant in UTC.
  * Ledgers are per pond. The same event_id posted to two ponds is two
    unrelated entries; rows are only ever read for the pond itself.

--------------------------------------------------------------------
ORDER AND REPLAY
--------------------------------------------------------------------
Every input to the chemistry engine has a place in model time: a sensor
input at its time, an event at its event time. At equal times sensor
inputs come first, then events in the order they were recorded (seq).
newest_input_at is the latest place applied so far.

An event at or after newest_input_at is applied directly. One before it
rewinds the chemistry engine to the newest checkpoint strictly before
the event, then applies, in order, every sensor input after the
checkpoint (read back from the sensor ingest ledger, migration 0015's
sensor_ingest_history) and every live event after it. The result is the
state the engine would have had if the inputs had arrived in order.

A checkpoint is taken whenever two consecutive inputs fall on different
local days: the chemistry state as of the earlier one ("through"). They
are kept for WINDOW (30 days) back from newest_input_at, plus the newest
one older than that, so the whole window can be replayed. A new twin
starts with a genesis checkpoint (through None: before everything). A
twin loaded from a snapshot older than the ledger starts with one at its
newest input, so it can replay anything after that.

An event older than every checkpoint (older than the window, or older
than the ledger itself) cannot be put in its place. It is applied at the
current state, one microsecond after newest_input_at, and that is its
place from then on (placed_late). Only the chemistry engine is
rewound. Evaporation and algae advance on weather and camera frames,
which are not recorded input by input, so they receive each new event
once, at their current state, as before.

--------------------------------------------------------------------
EDITS, DELETIONS AND TOMBSTONES (used by the event history screen, #57)
--------------------------------------------------------------------
pondInterventions is the record of what was logged. Each poll cycle
reconciles it with the ledger:

  * a row whose event_id is not in the ledger is applied (with replay
    when it is in the past);
  * a row whose amounts, kind or time differ from what was last seen
    for its event_id is an edit: the entry takes the row's values and
    the engine replays from the earlier of the old and new times;
  * an entry whose row was seen before and is now missing is a
    deletion: the entry becomes a tombstone (status deleted) and the
    engine replays from its time without it. A later post of the same
    event_id stays a no-op, so a retried request cannot bring it back.
    If the row reappears (an undone delete), the entry is applied again.
  * An edit or deletion that cannot be replayed (no checkpoint before
    it) is not applied and is reported; the entry keeps its old values.

Evaporation and algae keep the effect of an edited or deleted event:
neither can be rewound (see above). Rows created before the ledger
existed (ledger.since) are treated as already applied.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from koi.models.engine import EventKind, PondEvent
from koi.models.sensor_inputs import parse_timestamp

LEDGER_VERSION = 1

# How far back events can be logged and replayed: the checkpoint window.
WINDOW = timedelta(days=30)
# Entries are kept this much longer than the window, so a retried post
# near the window's edge still finds its entry.
ENTRY_GRACE = timedelta(days=1)
# How far a legacy post's time may be from its row's (see IDENTITY).
LEGACY_MATCH = timedelta(hours=14)
# Where an event that cannot be placed in time goes after newest_input_at.
LATE_STEP = timedelta(microseconds=1)

APPLIED = "applied"
DELETED = "deleted"
LEGACY_PREFIX = "legacy:"

# pondInterventions.event_type -> the engine's event kind.
ROW_KINDS = {"FEEDING": EventKind.FEEDING, "WATER_CHANGE": EventKind.WATER_CHANGE,
             "WATER_TOPUP": EventKind.TOP_UP, "ALGAE_SCRUB": EventKind.ALGAL_SCRUB}


def normalise_event_id(value: object) -> str:
    """A UUID in its canonical lower-case form; ValueError otherwise."""
    return str(uuid.UUID(str(value)))


def derived_event_id(row_id: int) -> str:
    """The UUID migration 0015 gives a row that has none
    (intervention_event_id: md5 of 'pondInterventions:' || id)."""
    return str(uuid.UUID(hex=hashlib.md5(f"pondInterventions:{int(row_id)}".encode()).hexdigest()))


def _number(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def event_from_row(row: dict) -> Optional[PondEvent]:
    """The engine event of a pondInterventions row, None for an unknown
    event_type."""
    kind = ROW_KINDS.get(str(row.get("event_type") or ""))
    if kind is None:
        return None
    return PondEvent(kind=kind, time=parse_timestamp(row["event_timestamp"]),
                     volume_percent=_number(row.get("volume_percentage")),
                     volume_litres=_number(row.get("volume_litres")),
                     scrub_type=row.get("algae_method"), food_grams=_number(row.get("food_grams")),
                     protein_percent=_number(row.get("protein_percentage")))


def event_values(event: PondEvent) -> dict:
    """What an edit can change: kind, time and amounts."""
    return {"kind": event.kind.value, "time": event.time.isoformat(),
            "volume_percent": event.volume_percent, "volume_litres": event.volume_litres,
            "scrub_type": event.scrub_type, "food_grams": event.food_grams,
            "protein_percent": event.protein_percent}


def _same_amounts(a: PondEvent, b: PondEvent) -> bool:
    fa, fb = event_values(a), event_values(b)
    fa.pop("time")
    fb.pop("time")
    return fa == fb


@dataclass
class LedgerEntry:
    """One event the twin knows about. model_time is its place in the
    chemistry engine's order (its event time, unless placed late)."""
    event_id: str
    seq: int
    event: PondEvent
    model_time: datetime
    recorded_at: datetime
    source: str  # "api" or "reconcile"
    status: str = APPLIED
    row_id: Optional[int] = None
    row: Optional[dict] = None  # values of the row as last seen
    revision: int = 0
    legacy: bool = False

    @property
    def placed_late(self) -> bool:
        return self.model_time != self.event.time

    def to_dict(self) -> dict:
        return {"event_id": self.event_id, "seq": self.seq, "event": self.event.to_dict(),
                "model_time": self.model_time.isoformat(), "recorded_at": self.recorded_at.isoformat(),
                "source": self.source, "status": self.status, "row_id": self.row_id, "row": self.row,
                "revision": self.revision, "legacy": self.legacy}

    @classmethod
    def from_dict(cls, d: dict) -> "LedgerEntry":
        return cls(event_id=d["event_id"], seq=int(d["seq"]), event=PondEvent.from_dict(d["event"]),
                   model_time=parse_timestamp(d["model_time"]), recorded_at=parse_timestamp(d["recorded_at"]),
                   source=d.get("source", "api"), status=d.get("status", APPLIED), row_id=d.get("row_id"),
                   row=d.get("row"), revision=int(d.get("revision", 0)), legacy=bool(d.get("legacy", False)))


@dataclass
class Checkpoint:
    """The chemistry engine's state as of through (None: before any
    input). chemistry is a trimmed engine snapshot (PondTwin)."""
    through: Optional[datetime]
    chemistry: dict

    def to_dict(self) -> dict:
        return {"through": self.through.isoformat() if self.through else None, "chemistry": self.chemistry}

    @classmethod
    def from_dict(cls, d: dict) -> "Checkpoint":
        return cls(parse_timestamp(d["through"]) if d.get("through") else None, d["chemistry"])


def _before(through: Optional[datetime], t: datetime) -> bool:
    return through is None or through < t


@dataclass
class EventLedger:
    """since: rows created before it are treated as applied (None: none
    are). newest_input_at: the latest place in model time applied."""
    since: Optional[datetime] = None
    entries: dict[str, LedgerEntry] = field(default_factory=dict)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    next_seq: int = 1
    newest_input_at: Optional[datetime] = None

    # --- entries ------------------------------------------------------
    def get(self, event_id: str) -> Optional[LedgerEntry]:
        return self.entries.get(event_id)

    def add(self, event_id: Optional[str], event: PondEvent, *, recorded_at: datetime, source: str,
            row_id: Optional[int] = None, row: Optional[dict] = None) -> LedgerEntry:
        seq = self.next_seq
        self.next_seq += 1
        legacy = event_id is None
        key = f"{LEGACY_PREFIX}{seq}" if event_id is None else event_id
        entry = LedgerEntry(key, seq, event, event.time, recorded_at, source, row_id=row_id, row=row,
                            legacy=legacy)
        self.entries[key] = entry
        return entry

    def rekey(self, entry: LedgerEntry, event_id: str) -> None:
        """A legacy entry takes over its row's event_id."""
        self.entries.pop(entry.event_id, None)
        entry.event_id = event_id
        self.entries[event_id] = entry

    def live(self) -> list[LedgerEntry]:
        """Applied entries in model order."""
        return sorted((e for e in self.entries.values() if e.status == APPLIED),
                      key=lambda e: (e.model_time, e.seq))

    def newest_event_time(self) -> Optional[datetime]:
        live = self.live()
        return live[-1].model_time if live else None

    def legacy_match(self, event: PondEvent) -> Optional[LedgerEntry]:
        """The oldest unclaimed legacy entry with the same kind and amounts
        within LEGACY_MATCH of event's time."""
        for entry in sorted(self.entries.values(), key=lambda e: e.seq):
            if (entry.legacy and entry.row_id is None and entry.status == APPLIED
                    and _same_amounts(entry.event, event)
                    and abs(entry.event.time - event.time) <= LEGACY_MATCH):
                return entry
        return None

    # --- checkpoints --------------------------------------------------
    def checkpoint_before(self, t: datetime) -> Optional[Checkpoint]:
        """The newest checkpoint strictly before t."""
        found = [c for c in self.checkpoints if _before(c.through, t)]
        return found[-1] if found else None

    def add_checkpoint(self, checkpoint: Checkpoint) -> None:
        self.checkpoints.append(checkpoint)
        self.checkpoints.sort(key=lambda c: (c.through is not None, c.through or datetime.min))

    def drop_checkpoints_after(self, through: Optional[datetime]) -> None:
        """Checkpoint invalidation: a replay from through makes every later
        checkpoint stale; the replay takes new ones as it goes."""
        self.checkpoints = [c for c in self.checkpoints
                            if c.through is None or (through is not None and c.through <= through)]

    def prune(self) -> None:
        """Keeps the checkpoints covering WINDOW back from newest_input_at
        (and the newest one before it), and the entries ENTRY_GRACE
        beyond that."""
        if self.newest_input_at is None:
            return
        cutoff = self.newest_input_at - WINDOW
        older = [i for i, c in enumerate(self.checkpoints) if c.through is None or c.through <= cutoff]
        if older:
            self.checkpoints = self.checkpoints[older[-1]:]
        keep_from = cutoff - ENTRY_GRACE
        self.entries = {k: e for k, e in self.entries.items()
                        if max(e.model_time, e.event.time) >= keep_from}

    # --- snapshot -----------------------------------------------------
    def to_dict(self) -> dict:
        return {"version": LEDGER_VERSION,
                "since": self.since.isoformat() if self.since else None,
                "next_seq": self.next_seq,
                "newest_input_at": self.newest_input_at.isoformat() if self.newest_input_at else None,
                "entries": [e.to_dict() for e in sorted(self.entries.values(), key=lambda e: e.seq)],
                "checkpoints": [c.to_dict() for c in self.checkpoints]}

    @classmethod
    def from_dict(cls, d: dict) -> "EventLedger":
        ledger = cls(since=parse_timestamp(d["since"]) if d.get("since") else None,
                     next_seq=int(d.get("next_seq", 1)),
                     newest_input_at=parse_timestamp(d["newest_input_at"]) if d.get("newest_input_at") else None)
        for raw in d.get("entries") or []:
            entry = LedgerEntry.from_dict(raw)
            ledger.entries[entry.event_id] = entry
        ledger.checkpoints = [Checkpoint.from_dict(c) for c in d.get("checkpoints") or []]
        return ledger
