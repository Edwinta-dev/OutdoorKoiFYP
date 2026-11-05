"""The GCC signal of the algae engine (issue #92): the pond's daily GCC
series, its growth slope, its thresholds as offsets above the quiet level,
and the cell-grid zone rule. Pure functions over camera frames, no state;
AlgaeGrowthEngine (algae_engine.py) calls them and re-exports the public
names. docs/models.md documents every value."""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo


def _median(values: list) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _quantile(sorted_values: list, frac: float) -> float:
    """Linear-interpolated quantile over an already-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = frac * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    w = pos - lo
    return float(sorted_values[lo]) * (1 - w) + float(sorted_values[hi]) * w


# ============================================================
# GCC signal (issue #92)
# ============================================================
#
# green_ratio counts saturated green pixels, so on pond 455 it measures
# floating plants and the camera's view more than the water. GCC
# (G / (R + G + B)) is the colour of the water itself, and the PhenoCam
# method makes it robust: one high quantile per local day of the passing
# daytime frames, then a short moving window. Status compares the windowed
# level with offsets above the pond's own quiet level. Growth is the slope of
# the daily values (not the windowed ones, see gcc_growth and decision record
# 0092) against the spread of windowed day-to-day changes, and a 3x3 cell
# grid gives a local signal next to the pond-wide one. docs/models.md
# documents every value below.

# MAD x 1.4826 estimates a normal standard deviation (robust spread).
MAD_TO_SD = 1.4826
# Values (local days, or frames in frame mode) a cell needs in its
# baseline, before the run being judged, to be assessed at all.
ZONE_MIN_BASELINE = 3
# Floor on the pooled cell noise, so a perfectly flat (synthetic or
# quantised) series cannot turn every tiny excess into a flag.
ZONE_NOISE_FLOOR = 0.001
# Spikes reported per assessment, newest first.
MAX_REPORTED_SPIKES = 10
# Retained GCC frames, on top of the age limit in GccSignalConfig.
MAX_GCC_FRAMES = 1000
DAILY_STATISTICS = {"p90": 0.90, "median": 0.50}
RUN_UNITS = ("day", "frame")


@dataclass(frozen=True)
class GccSignalConfig:
    """The tuning of the GCC path. koi.settings.Settings.algae_signal_config
    builds it from the environment; the defaults here equal the Settings
    defaults, so an engine built without one behaves as the poller's."""
    daily_statistic: str = "p90"
    day_start_hour: int = 7
    day_end_hour: int = 19
    window_days: int = 3
    growth_window_days: int = 3
    slow_window_days: int = 7
    growth_k: float = 3.0
    growth_min_rate: float = 0.001
    min_quiet_diffs: int = 7
    watch_offset: float = 0.03
    action_offset: float = 0.06
    reference_cells: tuple = (6, 7, 8)
    object_cells: tuple = ()
    cell_k: float = 3.0
    cell_run: int = 2
    cell_unreliable_ratio: float = 4.0
    run_unit: str = "day"
    timezone: str = "Asia/Singapore"
    history_days: int = 30

    def __post_init__(self):
        if self.daily_statistic not in DAILY_STATISTICS:
            raise ValueError(f"daily_statistic must be one of {sorted(DAILY_STATISTICS)}")
        if self.run_unit not in RUN_UNITS:
            raise ValueError(f"run_unit must be one of {RUN_UNITS}")
        object.__setattr__(self, "reference_cells", tuple(int(i) for i in self.reference_cells))
        object.__setattr__(self, "object_cells", tuple(int(i) for i in self.object_cells))

    def to_dict(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "GccSignalConfig":
        """Unknown keys are ignored, so a snapshot written by a later
        version with more settings still loads."""
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in names})


DEFAULT_SIGNAL_CONFIG = GccSignalConfig()


def _local_time(stamp: str, tz: ZoneInfo) -> datetime:
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz)


def _daytime_frames(frames: list, config: GccSignalConfig) -> list:
    """(local time, frame) for the frames inside the daytime hours."""
    tz = ZoneInfo(config.timezone)
    out = []
    for frame in frames:
        local = _local_time(frame["t"], tz)
        if config.day_start_hour <= local.hour < config.day_end_hour:
            out.append((local, frame))
    return out


def gcc_daily_series(frames: list, config: GccSignalConfig = DEFAULT_SIGNAL_CONFIG) -> list:
    """One value per local day: the configured quantile of that day's
    daytime frames, plus "window", the mean of the daily values within the
    trailing window_days calendar days. A missing day is not invented; the
    window simply holds fewer values. frames are already quality-filtered."""
    by_day: dict = {}
    for local, frame in _daytime_frames(frames, config):
        by_day.setdefault(local.date(), []).append(float(frame["v"]))
    frac = DAILY_STATISTICS[config.daily_statistic]
    days = [{"date": day.isoformat(), "gcc": _quantile(sorted(by_day[day]), frac), "frames": len(by_day[day])}
            for day in sorted(by_day)]
    span = config.window_days - 1
    for row in days:
        today = date.fromisoformat(row["date"])
        inside = [r["gcc"] for r in days if 0 <= (today - date.fromisoformat(r["date"])).days <= span]
        row["window"] = sum(inside) / len(inside)
    return days


def _slope(points: list) -> Optional[float]:
    """Least-squares slope of (x, y) points, None for fewer than two x."""
    if len({x for x, _ in points}) < 2:
        return None
    n = len(points)
    x_mean = sum(x for x, _ in points) / n
    y_mean = sum(y for _, y in points) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in points)
    den = sum((x - x_mean) ** 2 for x, _ in points)
    return num / den


def _robust_spread(values: list) -> float:
    centre = _median(values)
    return MAD_TO_SD * _median([abs(v - centre) for v in values])


def gcc_growth(days: list, config: GccSignalConfig = DEFAULT_SIGNAL_CONFIG) -> dict:
    """d(GCC)/dt from the daily series.

    rate_per_day is the least-squares slope of the daily values over the
    last growth_window_days days, slow_rate_per_day over slow_window_days.
    noise is the standard deviation of the day-to-day changes of the
    windowed series BEFORE the short window, so a rise in progress stays
    out of its own threshold. Growth is flagged when rate_per_day exceeds max(growth_k x noise,
    growth_min_rate); with fewer than min_quiet_diffs changes the noise is
    unknown and nothing flags."""
    result: dict[str, Any] = {"rate_per_day": None, "slow_rate_per_day": None, "noise": None, "threshold": None,
              "flagged": False, "quiet_differences": 0, "window_days": config.growth_window_days,
              "slow_window_days": config.slow_window_days}
    if not days:
        return result
    dates = [date.fromisoformat(r["date"]) for r in days]
    last = dates[-1]

    def over(span: int) -> Optional[float]:
        return _slope([((d - last).days, r["gcc"]) for d, r in zip(dates, days, strict=True)
                       if (last - d).days <= span])

    quiet_end = last - timedelta(days=config.growth_window_days)
    diffs = [days[i]["window"] - days[i - 1]["window"] for i in range(1, len(days))
             if (dates[i] - dates[i - 1]).days == 1 and dates[i] <= quiet_end]
    noise = statistics.stdev(diffs) if len(diffs) >= max(config.min_quiet_diffs, 2) else None
    threshold = max(config.growth_k * noise, config.growth_min_rate) if noise is not None else None
    short = over(config.growth_window_days)
    result.update(rate_per_day=short, slow_rate_per_day=over(config.slow_window_days), noise=noise,
                  threshold=threshold, quiet_differences=len(diffs),
                  flagged=bool(short is not None and threshold is not None and short > threshold))
    return result


def resolve_gcc_thresholds(days: list, config: GccSignalConfig = DEFAULT_SIGNAL_CONFIG) -> dict:
    """Watch and action as offsets above the pond's quiet GCC level: the
    median of the lower half of the windowed daily values since the last
    baseline reset. Offsets, not multiples: GCC never sits near zero, and a
    multiple of a near-zero baseline alarmed on noise (issue #92)."""
    windows = sorted(r["window"] for r in days)
    if not windows:
        return {"watch": None, "action": None, "baseline": None, "mode": "gcc_offset"}
    baseline = _median(windows[: max(len(windows) // 2, 1)])
    return {"watch": baseline + config.watch_offset, "action": baseline + config.action_offset,
            "baseline": baseline, "mode": "gcc_offset"}


def _runs(series: list, threshold: float, by_day: bool) -> list:
    """Maximal runs of consecutive values above threshold, as lists of
    (key, excess). In day mode a missing local day breaks a run."""
    runs: list[list] = []
    current: list = []
    previous = None
    for key, excess in series:
        adjacent = (previous is not None and
                    (not by_day or (date.fromisoformat(key) - date.fromisoformat(previous)).days == 1))
        if excess > threshold:
            if current and not adjacent:
                runs.append(current)
                current = []
            current.append((key, excess))
        elif current:
            runs.append(current)
            current = []
        previous = key
    if current:
        runs.append(current)
    return runs


def gcc_zones(frames: list, config: GccSignalConfig = DEFAULT_SIGNAL_CONFIG) -> Optional[dict]:
    """The zone rule on the cell grid. None when no frame has a grid.

    Each frame's reference-cell mean is subtracted from every pond cell,
    cancelling a white-balance shift that moves all cells together. A
    cell's value per local day (per frame in frame mode) is the median of
    its adjusted values; its baseline is the median of those values before
    the last cell_run (the run being judged stays out of its own
    baseline). Noise is the robust spread pooled over the pond cells'
    baseline residuals. A cell flags when its excess stays above cell_k x
    noise for cell_run consecutive values; a shorter run that fell back is
    a spike, and one still open is pending. A cell whose own spread is
    above cell_unreliable_ratio x the pooled noise is reported unreliable
    (something loose in view) and not judged."""
    grids = [(local, frame["c"]) for local, frame in _daytime_frames(frames, config) if frame.get("c")]
    if not grids:
        return None
    size = len(grids[-1][1])
    objects, refs, pond = _zone_cell_roles(size, config)
    by_day = config.run_unit == "day"
    result: dict[str, Any] = {"run_unit": config.run_unit, "reference_cells": refs, "pond_cells": pond,
              "object_cells": sorted(objects), "noise": None, "threshold": None,
              "flagged": [], "spikes": [], "pending": [], "unreliable": [], "insufficient": []}
    if not refs or not pond:
        result["note"] = "no_reference_cells" if not refs else "no_pond_cells"
        return result

    adjusted = _reference_adjusted_cells(grids, size, refs, pond, by_day)
    series, baselines, spreads, residuals, result["insufficient"] = _cell_baselines(adjusted, pond, config)
    if not series:
        return result

    unreliable, reliable, noise = _pooled_cell_noise(series, spreads, residuals, config)
    threshold = config.cell_k * noise
    result.update(noise=noise, threshold=threshold, unreliable=unreliable)
    result["flagged"], result["pending"], spikes = _judge_cells(reliable, series, baselines, threshold, by_day, config)
    result["spikes"] = sorted(spikes, key=lambda s: s["since"], reverse=True)[:MAX_REPORTED_SPIKES]
    return result


def _zone_cell_roles(size: int, config: GccSignalConfig) -> tuple[set, list, list]:
    """(object cells, reference cells, pond cells) of a grid of size cells:
    configured indices outside the grid are dropped, and an object cell is
    never a reference."""
    objects = {i for i in config.object_cells if 0 <= i < size}
    refs = [i for i in config.reference_cells if 0 <= i < size and i not in objects]
    pond = [i for i in range(size) if i not in objects and i not in refs]
    return objects, refs, pond


def _reference_adjusted_cells(grids: list, size: int, refs: list, pond: list, by_day: bool) -> dict:
    """{pond cell: {local day (or frame time): [values]}} with each frame's
    reference-cell mean subtracted, so a white-balance shift that moves all
    cells together cancels."""
    adjusted: dict = {i: {} for i in pond}
    for local, cells in grids:
        if len(cells) != size:
            continue
        ref = [cells[i] for i in refs if cells[i] is not None]
        if not ref:
            continue
        shift = sum(ref) / len(ref)
        key = local.isoformat() if not by_day else local.date().isoformat()
        for i in pond:
            if cells[i] is not None:
                adjusted[i].setdefault(key, []).append(cells[i] - shift)
    return adjusted


def _cell_baselines(adjusted: dict, pond: list, config: GccSignalConfig) -> tuple[dict, dict, dict, dict, list]:
    """(series, baselines, spreads, residuals, insufficient) per pond cell.
    A cell's baseline is the median of its values before the last cell_run
    (the run being judged stays out of its own baseline); a cell with too
    few of them is listed in insufficient and not judged."""
    series: dict[int, list] = {}
    baselines: dict[int, float] = {}
    spreads: dict[int, float] = {}
    residuals: dict[int, list] = {}
    insufficient: list = []
    for i in pond:
        values = [(key, _median(v)) for key, v in adjusted[i].items()]
        base = [v for _, v in values[:-config.cell_run]]
        if len(base) < ZONE_MIN_BASELINE:
            insufficient.append(i)
            continue
        series[i] = values
        baselines[i] = _median(base)
        residuals[i] = [v - baselines[i] for v in base]
        spreads[i] = _robust_spread(base)
    return series, baselines, spreads, residuals, insufficient


def _pooled_cell_noise(series: dict, spreads: dict, residuals: dict,
                       config: GccSignalConfig) -> tuple[list, list, float]:
    """(unreliable, reliable, noise): the robust spread pooled over the
    cells' baseline residuals, recomputed without the cells whose own spread
    is above cell_unreliable_ratio x the pooled noise (something loose in
    view) when any cell remains."""
    pooled = _robust_spread([r for i in series for r in residuals[i]])
    unreliable = [i for i in series if spreads[i] > config.cell_unreliable_ratio * max(pooled, ZONE_NOISE_FLOOR)]
    reliable = [i for i in series if i not in unreliable]
    if unreliable and reliable:
        pooled = _robust_spread([r for i in reliable for r in residuals[i]])
    return unreliable, reliable, max(pooled, ZONE_NOISE_FLOOR)


def _judge_cells(reliable: list, series: dict, baselines: dict, threshold: float, by_day: bool,
                 config: GccSignalConfig) -> tuple[list, list, list]:
    """(flagged, pending, spikes): a cell flags when its excess stays above
    the threshold for cell_run consecutive values; a shorter run that fell
    back is a spike, and one still open is pending."""
    flagged: list = []
    pending: list = []
    spikes: list = []
    for i in reliable:
        excess = [(key, value - baselines[i]) for key, value in series[i]]
        runs: list[list] = _runs(excess, threshold, by_day)
        open_run = runs[-1] if runs and runs[-1][-1][0] == excess[-1][0] else None
        for run in runs:
            entry = {"index": i, "since": run[0][0], "run_length": len(run),
                     "excess": round(max(e for _, e in run), 5)}
            if run is not open_run:
                if len(run) < config.cell_run:
                    spikes.append(entry)
            elif len(run) >= config.cell_run:
                flagged.append({"index": i, "since": run[0][0], "run_length": len(run),
                                "latest_excess": round(excess[-1][1], 5)})
            else:
                pending.append(entry)
    return flagged, pending, spikes
