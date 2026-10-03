"""The pond profile: volume, depth, fish and tap water, effective-dated.

A pond's configuration is a history of rows (pond_profile, migration
0008), each in force from its effective_from until the next row's. The
engines read the row in force at each simulated time, so a pond resized
or restocked today changes what the model computes from today onward and
leaves earlier steps as they were computed.

Lookup rule: the profile in force at t is the one with the latest
effective_from <= t; a time before the first profile uses the first. A
history built from UserData alone (a pond with no pond_profile rows) is
one profile with no effective_from, in force at every time.

Times are aware UTC instants. Unknown values stay None: depth_m None
means the evaporation model uses its default depth, and the tap and fish
fields fall back to PondConfig's defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from koi.models.engine import PondConfig

# The fields a profile row carries besides its pond, time and bookkeeping.
PROFILE_FIELDS = ("volume_l", "depth_m", "biomass_g", "fish_type", "fish_count", "tap_tds_ppm",
                  "tap_nitrate_ppm", "aeration")


@dataclass(frozen=True)
class PondProfile:
    volume_l: float
    biomass_g: float
    effective_from: Optional[datetime] = None
    depth_m: Optional[float] = None
    fish_type: Optional[str] = None
    fish_count: Optional[int] = None
    tap_tds_ppm: Optional[float] = None
    tap_nitrate_ppm: Optional[float] = None
    aeration: Optional[bool] = None

    def pond_config(self, base: Optional[PondConfig] = None) -> PondConfig:
        """The chemistry engine's config under this profile. Fields the
        profile leaves unknown take PondConfig's defaults; time_zone (not
        part of the profile) is kept from base."""
        defaults = PondConfig(volume_litres=self.volume_l, estimated_biomass_grams=self.biomass_g)
        return PondConfig(
            volume_litres=float(self.volume_l),
            estimated_biomass_grams=float(self.biomass_g),
            fish_type=self.fish_type if self.fish_type is not None else defaults.fish_type,
            fish_count=int(self.fish_count) if self.fish_count is not None else defaults.fish_count,
            tap_tds_ppm=float(self.tap_tds_ppm) if self.tap_tds_ppm is not None else defaults.tap_tds_ppm,
            tap_nitrate_ppm=(float(self.tap_nitrate_ppm) if self.tap_nitrate_ppm is not None
                             else defaults.tap_nitrate_ppm),
            time_zone=base.time_zone if base is not None else defaults.time_zone,
        )

    def to_dict(self) -> dict:
        return {
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            **{name: getattr(self, name) for name in PROFILE_FIELDS},
        }


class ProfileHistory:
    """A pond's profiles in effective order. Never empty."""

    def __init__(self, profiles: list[PondProfile]):
        if not profiles:
            raise ValueError("a profile history needs at least one profile")
        undated = [p for p in profiles if p.effective_from is None]
        if undated and len(profiles) > 1:
            raise ValueError("an undated profile must be the only one")
        # sorted() is stable, so rows with equal times (which the unique
        # key rules out) would keep their storage order.
        self.profiles = sorted(profiles, key=lambda p: p.effective_from or datetime.min)

    def at(self, t: datetime) -> PondProfile:
        """The profile in force at t."""
        current = self.profiles[0]
        for profile in self.profiles[1:]:
            assert profile.effective_from is not None
            if profile.effective_from > t:
                break
            current = profile
        return current

    def changes_between(self, start: datetime, end: datetime) -> list[tuple[datetime, PondProfile]]:
        """(effective_from, profile) for each profile that takes effect
        after start and at or before end, in order."""
        return [(p.effective_from, p) for p in self.profiles
                if p.effective_from is not None and start < p.effective_from <= end]

    @property
    def latest(self) -> PondProfile:
        return self.profiles[-1]


def _instant(value: object) -> datetime:
    """A timestamptz value (ISO text or datetime) as an aware datetime;
    no offset means UTC."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def profile_from_row(row: dict) -> PondProfile:
    """A pond_profile row (as storage returns it) as a PondProfile."""
    def num(name: str) -> Optional[float]:
        value = row.get(name)
        return float(value) if value is not None else None

    return PondProfile(
        effective_from=_instant(row["effective_from"]),
        volume_l=float(row["volume_l"]),
        biomass_g=float(row["biomass_g"]),
        depth_m=num("depth_m"),
        fish_type=row.get("fish_type"),
        fish_count=int(row["fish_count"]) if row.get("fish_count") is not None else None,
        tap_tds_ppm=num("tap_tds_ppm"),
        tap_nitrate_ppm=num("tap_nitrate_ppm"),
        aeration=row.get("aeration"),
    )


def profile_from_userdata_config(config: dict) -> PondProfile:
    """The undated profile of a pond with no pond_profile rows, from its
    UserData config (Storage.fetch_pond_config: litres and grams)."""
    return PondProfile(volume_l=float(config["volume_litres"]),
                       biomass_g=float(config["estimated_biomass_grams"]))
