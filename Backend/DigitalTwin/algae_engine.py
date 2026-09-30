"""
algae_engine.py

Lookahead model for the "Algal & Solar Monitor" detail graph screen
(DetailGraphScreen metricType == 'lux' / 'algae').

--------------------------------------------------------------------
THE GROUNDING MECHANISM - HOW THE ESP32-CAM FITS IN
--------------------------------------------------------------------
The other two lookaheads are pure models: the chemistry engine infers
TAN/NO2/NO3 that nobody ever measures directly, and the evaporation
engine computes a physical rate from weather. Algae is different, and
better - the ESP32-CAM gives an actual measured observable.

Backend/Camera/camera.py already runs an HSV green-pixel-ratio
analysis on each frame and writes it to `imageTable` (green_ratio, plus a
`current_state` JSON pair [state, smoothed_green]). That is a real time
series of how green the pond has actually become.

So rather than predicting algae from first principles and hoping, this
engine does the honest thing:

  1. FIT the pond's own realised growth rate from the camera history
     (regression on ln(green_ratio) -> specific growth rate mu, /day).
  2. BACK OUT an intrinsic rate by dividing that realised rate by the
     environmental favourability that actually prevailed during the
     fitted window (light x temperature x nutrients).
  3. PROJECT forward by re-applying that intrinsic rate against the
     FORECAST favourability for each coming day.

Step 2 is what makes this a forecast rather than a straight-line
extrapolation. If the last week was overcast and the next four days are
"Fair (Day)", a naive trend line would under-predict badly; dividing out
the low recent favourability and re-multiplying by the high forecast
favourability captures that swing.

Growth itself is logistic (dG/dt = mu*G*(1 - G/K)) rather than
exponential, because green coverage is a bounded ratio - it physically
cannot exceed 1.0, and in practice self-shades and nutrient-limits long
before that.

--------------------------------------------------------------------
CROSS-DOMAIN LINK TO THE CHEMISTRY ENGINE
--------------------------------------------------------------------
Nitrate is algae's nitrogen source, and WaterChemistryEngine already
projects NO3 forward day by day. This engine accepts that NO3 trajectory
as its nutrient input, so a pond whose nitrate is climbing gets a faster
predicted bloom than one whose nitrate is being diluted out by water
changes. The two lookaheads are genuinely coupled rather than three
independent widgets that happen to share a screen.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# ============================================================
# Environmental response curves
# ============================================================

# Light: photosynthesis saturates. Michaelis-Menten style half-saturation
# constant in lux. Green algae saturate well below full tropical noon sun.
_LUX_HALF_SATURATION = 8000.0

# Nutrient: nitrogen half-saturation for green algae, ppm as NO3.
# Below this, growth is nutrient-limited; well above it, light and
# temperature dominate.
_NO3_HALF_SATURATION_PPM = 5.0

# A pond is never truly nitrogen-free (fish excrete continuously and TAN
# is always in transit through the nitrogen cycle), so nutrient
# favourability is floored rather than allowed to reach zero. Without
# this, a pond reading NO3 = 0.0 would be predicted to never bloom, which
# is wrong - it just blooms slower.
_MIN_NUTRIENT_FACTOR = 0.15

# Carrying capacity as a green-pixel ratio. Even a fully choked pond does
# not read 1.0 through this camera because the frame includes non-water
# background, surface glare and shadow.
DEFAULT_CARRYING_CAPACITY = 0.75

# Literature fallback intrinsic growth rate (per day) used only when there
# is not enough camera history to fit one. Green algae in warm nutrient
# rich water can roughly double in 1-2 days under ideal light, giving
# mu_max in the 0.35-0.7/day range; 0.45 is a mid, slightly conservative
# choice. Flagged as low confidence whenever it is used.
FALLBACK_INTRINSIC_RATE = 0.45

# Green-ratio thresholds. Deliberately expressed relative to the pond's
# own established baseline as well as in absolute terms - see
# resolve_thresholds() for why.
ABSOLUTE_WATCH_RATIO = 0.25
ABSOLUTE_ACTION_RATIO = 0.40

# A manual scrub does not sterilise the pond; it knocks the visible
# biomass down and it regrows from what remains. Matches the 5-day
# suppression window WaterChemistryEngine already applies for ALGAE_SCRUB.
SCRUB_REMOVAL_EFFICIENCY = 0.70

# ------------------------------------------------------------
# HUMAN SEVERITY RATINGS
# ------------------------------------------------------------
# The ordinal severity scale the user picks from on the algae detail
# screen. "obstruction" is deliberately NOT in this list - it is a
# data-quality flag on a different axis, not a level above "severe", and
# the engine handles it completely differently (it excludes a reading
# rather than describing one).
SEVERITY_LEVELS = ["none", "minor", "moderate", "severe"]

# Minimum labelled observations of a class before its class-conditional
# median is trusted as that class's target green_ratio. Two is low, but
# with a single pond and a months-long collection window, waiting for a
# statistically comfortable sample means never calibrating at all. The
# fallback below degrades gracefully, and confidence is reported.
MIN_LABELS_PER_CLASS = 2

# Where each severity class sits in the pond's OWN observed distribution,
# used to place class targets before enough labels exist to measure them.
# Anchoring to quantiles of this camera's history rather than to absolute
# green_ratio values is the same reasoning as resolve_thresholds(): the
# absolute numbers are meaningless across installations.
_SEVERITY_QUANTILE = {"none": 0.10, "minor": 0.40, "moderate": 0.70, "severe": 0.90}

# Last-resort targets when there is no camera history at all.
_SEVERITY_DEFAULT = {"none": 0.01, "minor": 0.08, "moderate": 0.22, "severe": 0.45}

# How many ratings to retain in the engine snapshot. Calibration only
# needs the recent window, and an unbounded list would bloat the JSONB.
MAX_RETAINED_RATINGS = 200


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


def light_factor(lux: float) -> float:
    """Saturating light response, 0..1."""
    lux = max(lux, 0.0)
    return lux / (lux + _LUX_HALF_SATURATION)


def temperature_factor(temp_c: float) -> float:
    """Green algae thermal response. Peaks around 30C - notably warmer
    than the nitrifying-bacteria curve in engine.py, which peaks 20-28C.
    That difference matters in Singapore: at 30-33C the pond's nitrifying
    filter is slightly past its best while algae are at their most
    aggressive, which is exactly the combination that produces a bloom.
    """
    def lerp(x, x0, x1, y0, y1):
        return y0 + (y1 - y0) * ((x - x0) / (x1 - x0))

    if temp_c < 5:
        return 0.02
    if temp_c < 12:
        return lerp(temp_c, 5, 12, 0.02, 0.20)
    if temp_c < 20:
        return lerp(temp_c, 12, 20, 0.20, 0.60)
    if temp_c < 30:
        return lerp(temp_c, 20, 30, 0.60, 1.00)
    if temp_c < 36:
        return lerp(temp_c, 30, 36, 1.00, 0.75)
    return 0.45


def nutrient_factor(no3_ppm: Optional[float]) -> float:
    """Saturating nitrogen response, floored at _MIN_NUTRIENT_FACTOR.
    Returns a neutral 0.6 when NO3 is unknown (chemistry engine has no
    projection yet) so the algae forecast degrades gracefully instead of
    refusing to run."""
    if no3_ppm is None:
        return 0.6
    no3 = max(no3_ppm, 0.0)
    raw = no3 / (no3 + _NO3_HALF_SATURATION_PPM)
    return max(raw, _MIN_NUTRIENT_FACTOR)


def favourability(lux: float, temp_c: float, no3_ppm: Optional[float]) -> float:
    """Combined multiplicative environmental favourability, 0..1.
    Multiplicative (Liebig-style co-limitation) rather than additive:
    algae need light AND warmth AND nitrogen, so any one of them being
    near zero should collapse growth regardless of the others."""
    return light_factor(lux) * temperature_factor(temp_c) * nutrient_factor(no3_ppm)


# ============================================================
# Parsing the camera history
# ============================================================

@dataclass
class GreenSample:
    time: datetime
    green_ratio: float
    smoothed_green: Optional[float] = None
    state: Optional[str] = None


def parse_image_rows(rows: list) -> list:
    """Turns raw `imageTable` rows into GreenSample objects, oldest first.

    Handles the `current_state` column's actual on-disk shape, which is a
    two-element JSON array like ["base", 0.1235] - element 0 is the state
    machine's label and element 1 is hsvEngine's smoothed (EMA) baseline.
    Supabase may hand this back already decoded as a Python list, or as a
    JSON string depending on the column type, so both are handled.

    Prefers the smoothed value over the raw green_ratio when fitting,
    because single frames are noisy (passing cloud, surface glare, a fish
    breaking the surface) while the EMA is the filtered signal.
    """
    import json

    samples = []
    for row in rows or []:
        ts = row.get("created_at")
        if ts is None:
            continue
        if not isinstance(ts, datetime):
            ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))

        raw = row.get("green_ratio")
        if raw is None:
            continue

        state_label, smoothed = None, None
        cs = row.get("current_state")
        if isinstance(cs, str):
            try:
                cs = json.loads(cs)
            except (ValueError, TypeError):
                cs = None
        if isinstance(cs, (list, tuple)) and len(cs) >= 2:
            state_label = str(cs[0])
            try:
                smoothed = float(cs[1])
            except (TypeError, ValueError):
                smoothed = None
        elif isinstance(cs, str):
            state_label = cs

        samples.append(
            GreenSample(
                time=ts,
                green_ratio=float(raw),
                smoothed_green=smoothed,
                state=state_label,
            )
        )

    samples.sort(key=lambda s: s.time)
    return samples


def fit_specific_growth_rate(samples: list) -> Optional[float]:
    """Least-squares slope of ln(green) against time in days = the
    realised specific growth rate mu (per day).

    Works in log space because algal growth is multiplicative - a pond
    going 0.01 -> 0.02 is the same biological event as 0.20 -> 0.40, and
    a linear fit would treat the second as twenty times more significant.

    Samples whose state is "obstruction" are excluded: those frames are
    flagged by hsvEngine as physically blocked (a leaf on the lens, a
    hand in shot) and their green ratio is not a measurement of the pond.

    Returns None when there is too little usable history, or when the
    values are so close to zero that the log transform becomes unstable.
    """
    usable = [
        s for s in samples
        if s.state != "obstruction"
        and (s.smoothed_green if s.smoothed_green is not None else s.green_ratio) > 1e-5
    ]
    if len(usable) < 4:
        return None

    t0 = usable[0].time
    xs, ys = [], []
    for s in usable:
        value = s.smoothed_green if s.smoothed_green is not None else s.green_ratio
        xs.append((s.time - t0).total_seconds() / 86400.0)
        ys.append(math.log(value))

    span_days = xs[-1] - xs[0]
    if span_days < 0.5:
        # All samples inside half a day - a rate fitted over that window
        # would be extrapolating a daily trend from noise.
        return None

    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    if den == 0:
        return None
    return num / den


def resolve_thresholds(
    samples: list,
    absolute_watch: float = ABSOLUTE_WATCH_RATIO,
    absolute_action: float = ABSOLUTE_ACTION_RATIO,
) -> dict:
    """Green-ratio alarm thresholds.

    Absolute thresholds alone are a poor fit here. The real imageTable
    data for user 15 sits between 0.0002 and 0.0174 - two orders of
    magnitude below the 0.25 "watch" level - because green_ratio depends
    heavily on what the camera happens to be pointed at (how much of the
    frame is water vs decking, liner colour, mounting angle). A fixed
    0.25 would simply never fire for that installation, while another
    pond framed tightly on green-tinged water might sit above it
    permanently.

    So the operative threshold is whichever is LOWER: the absolute level,
    or a multiple of this pond's own established baseline. That makes the
    alert meaningful per-installation without needing per-pond manual
    calibration.
    """
    clean = [
        (s.smoothed_green if s.smoothed_green is not None else s.green_ratio)
        for s in samples
        if s.state != "obstruction"
    ]
    baseline = None
    if len(clean) >= 4:
        ordered = sorted(clean)
        # Median of the lower half - a robust "quiet state" estimate that
        # is not dragged upward by the bloom we are trying to detect.
        lower_half = ordered[: max(len(ordered) // 2, 1)]
        baseline = sum(lower_half) / len(lower_half)

    if baseline is None or baseline <= 1e-6:
        return {
            "watch": absolute_watch,
            "action": absolute_action,
            "baseline": baseline,
            "mode": "absolute",
        }

    return {
        "watch": min(absolute_watch, baseline * 3.0),
        "action": min(absolute_action, baseline * 6.0),
        "baseline": baseline,
        "mode": "baseline_relative",
    }


# ============================================================
# Config + assessment types
# ============================================================

@dataclass
class AlgaeConfig:
    carrying_capacity: float = DEFAULT_CARRYING_CAPACITY

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class AlgaeDayEnvironment:
    lux: float
    temp_c: float
    no3_ppm: Optional[float] = None


@dataclass
class AlgaeAssessment:
    """Current-state readout, pushed to Supabase every poll cycle so the
    dashboard alert badge reads a cached value rather than recomputing."""
    status: str        # "Green" | "Amber" | "Red"
    category: str      # "Clear" | "Watch" | "Scrub Due"
    green_ratio: float
    watch_threshold: float
    action_threshold: float
    threshold_mode: str
    growth_rate_per_day: float
    intrinsic_rate_per_day: float
    rate_source: str
    confidence: str
    sample_count: int
    days_to_scrub: Optional[int]
    advisory: str
    scrub_now: bool
    label_count: int = 0
    calibrated: bool = False
    camera_drift: str = "insufficient_data"

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# ============================================================
# Engine  (STATEFUL)
# ============================================================

class AlgaeGrowthEngine:
    """Persistent, stateful per-pond algae model.

    STATEFULNESS - AND WHY THE CAMERA MAKES THIS DIFFERENT
    ------------------------------------------------------
    The chemistry and evaporation engines model quantities nobody
    measures directly, so their state IS the only estimate available.
    Algae is the opposite: the ESP32-CAM periodically hands us a real
    measurement of the very thing being modelled.

    That means this engine runs a genuine measurement-correction loop
    rather than either extreme:

      * Pure model  -> drifts, because growth rate is only approximate
                       and nothing ever pulls it back to reality.
      * Pure camera -> useless between frames. The camera sleeps for
                       hours at a time (imageSchedule's base schedule is
                       six fixed slots a day, and deep-sleep intervals
                       stretch further), so a dashboard reading straight
                       off the last frame is stale most of the time, and
                       cannot answer "when will this cross the line".

    So: advance_state() propagates logistic growth continuously between
    frames using live lux/temperature/nitrate, and ingest_camera_samples()
    pulls the modelled level back toward each new measurement via a
    complementary filter (CAMERA_TRUST). The model fills the gaps; the
    camera keeps it honest.

    Growth is logistic (dG/dt = mu*G*(1 - G/K)) because green coverage is
    a bounded ratio - it cannot exceed 1.0, and self-shades long before.

    NOT thread-safe on its own; callers hold the per-user lock.
    """

    # How hard a fresh camera frame pulls the modelled level toward it.
    # 0.0 = ignore the camera entirely, 1.0 = snap to it and discard the
    # model. 0.7 leans on the measurement (it IS ground truth) while still
    # damping single-frame noise from glare, a passing cloud, or a fish
    # breaking the surface - the same noise hsvEngine's own EMA exists to
    # suppress.
    CAMERA_TRUST = 0.7

    # A person standing at the pond looking at it is better evidence than
    # a 640x480 JPEG of one corner of it, so a rating pulls harder than a
    # frame does. Not 1.0 though: the rating is an ordinal class mapped
    # onto a continuous scale through a target that is itself estimated,
    # so some of the model's prior is worth keeping. It also means a
    # mis-tap degrades the estimate rather than destroying it - and the
    # undo path restores it exactly regardless.
    HUMAN_TRUST = 0.85

    def __init__(self, config: Optional[AlgaeConfig] = None):
        self.config = config or AlgaeConfig()
        self._green_ratio: Optional[float] = None
        self._last_advance_time: Optional[datetime] = None
        self._intrinsic_rate: float = FALLBACK_INTRINSIC_RATE
        self._rate_source: str = "literature_fallback"
        self._confidence: str = "low"
        self._last_camera_time: Optional[datetime] = None
        self._last_scrub_time: Optional[datetime] = None
        self._sample_count: int = 0
        self._obstructed_count: int = 0
        self._last_growth_rate: float = 0.0
        # Bounded rolling buffer of clean (time, value) camera readings,
        # kept so the growth-rate fit survives a process restart without
        # having to re-query the whole of imageTable.
        self._camera_history: list = []
        # ROUND 3 FIX: ephemeral cache of the GreenSample list ingest_camera_
        # samples() last built from _camera_history - not persisted in
        # to_snapshot() (it's trivially recomputable via _history_as_samples()
        # and would just be redundant with _camera_history in the JSONB).
        self._last_history_samples: list = []
        self._thresholds: dict = {
            "watch": ABSOLUTE_WATCH_RATIO,
            "action": ABSOLUTE_ACTION_RATIO,
            "baseline": None,
            "mode": "absolute",
        }
        # Human severity ratings, oldest first. Each entry pairs a class
        # label with the green_ratio the camera was reporting at that
        # moment - that pairing is what makes calibration possible.
        self._ratings: list = []
        # Bounded undo stack of (rating_key, prior_state) so a mis-tap is
        # exactly reversible rather than approximately.
        self._undo_stack: list = []

    # ----------------------------------------------------------
    # Camera assimilation (the grounding step)
    # ----------------------------------------------------------
    def ingest_camera_samples(self, samples: list) -> int:
        """Folds in any camera frames newer than the last one seen.

        Returns the number of NEW frames assimilated (0 is the normal case
        - the poller runs every 15 min while the camera may sleep for
        hours, so most ticks bring nothing new and must not re-apply an
        old frame).

        Three things happen per batch:
          1. obstructed frames are counted but otherwise discarded
          2. the growth-rate fit and thresholds are recomputed over the
             accumulated clean history
          3. the modelled level is pulled toward the newest measurement
        """
        if not samples:
            return 0

        fresh = [
            s for s in samples
            if self._last_camera_time is None or s.time > self._last_camera_time
        ]
        if not fresh:
            return 0

        obstructed = [s for s in fresh if s.state == "obstruction"]
        self._obstructed_count += len(obstructed)

        clean = [s for s in fresh if s.state != "obstruction"]
        # Advance the watermark past obstructed frames too, otherwise they
        # are re-examined on every subsequent tick forever.
        self._last_camera_time = max(s.time for s in fresh)

        if not clean:
            return 0

        for s in clean:
            value = s.smoothed_green if s.smoothed_green is not None else s.green_ratio
            self._camera_history.append({"t": s.time.isoformat(), "v": float(value)})
        if len(self._camera_history) > 300:
            self._camera_history = self._camera_history[-300:]

        self._sample_count += len(clean)

        # ROUND 3 FIX: this used to be its own inline copy of the same
        # rebuild _history_as_samples() already does elsewhere in this
        # class - and refit_growth_rate(), called immediately after this by
        # every caller (see pond_twin.py, app.py's /forecast/algae), had
        # its OWN second copy, re-parsing the same up-to-300 ISO timestamps
        # a second time on every camera frame. Build it once here, reuse it
        # for resolve_thresholds below, and cache it so refit_growth_rate
        # can reuse it too instead of re-deriving it from scratch.
        history_samples = self._history_as_samples()
        self._thresholds = resolve_thresholds(history_samples)
        self._last_history_samples = history_samples

        measured = clean[-1].smoothed_green
        if measured is None:
            measured = clean[-1].green_ratio
        measured = max(float(measured), 1e-6)

        if self._green_ratio is None:
            # First ever measurement - nothing to blend with.
            self._green_ratio = measured
        else:
            self._green_ratio = (
                self.CAMERA_TRUST * measured
                + (1.0 - self.CAMERA_TRUST) * self._green_ratio
            )

        self._last_advance_time = self._last_camera_time
        return len(clean)

    def refit_growth_rate(
        self,
        recent_lux: float,
        recent_temp_c: float,
        recent_no3_ppm: Optional[float],
        history_samples: Optional[list] = None,
    ) -> None:
        """Re-derives the intrinsic growth rate from the retained camera
        history, dividing out the environmental favourability that
        actually prevailed. This is what turns a trend line into a
        forecast: if the fitted window was overcast and the coming days
        are Fair, re-multiplying by the higher forecast favourability
        captures a swing straight extrapolation would miss.

        history_samples: ROUND 3 FIX - pass the list ingest_camera_samples()
        just built (self._last_history_samples) to avoid re-parsing the
        whole camera history a second time; every real caller does have one
        available, since this is always called right after
        ingest_camera_samples() (see pond_twin.py, app.py's
        /forecast/algae). Defaults to deriving it fresh via
        _history_as_samples() so standalone callers (tests) keep working
        unchanged."""
        if history_samples is None:
            history_samples = self._history_as_samples()
        realised = fit_specific_growth_rate(history_samples)
        recent_fav = favourability(recent_lux, recent_temp_c, recent_no3_ppm)

        if realised is None or recent_fav <= 1e-6:
            self._intrinsic_rate = FALLBACK_INTRINSIC_RATE
            self._rate_source = "literature_fallback"
            self._confidence = "low"
        elif realised <= 0:
            # The pond is getting cleaner. Carrying a negative rate forward
            # would predict the algae vanishing entirely, which is not a
            # safe thing to tell someone. Hold level and say so.
            self._intrinsic_rate = 0.0
            self._rate_source = "measured_declining"
            self._confidence = "medium"
        else:
            self._intrinsic_rate = realised / recent_fav
            self._rate_source = "fitted_from_camera"
            self._confidence = "high" if len(self._camera_history) >= 8 else "medium"

    # ----------------------------------------------------------
    # State advancement (called by the poller each cycle)
    # ----------------------------------------------------------
    def advance_state(self, now: datetime, env: AlgaeDayEnvironment) -> float:
        """Propagates logistic growth over the interval since the last
        advance, using live conditions. Returns the change in green ratio
        (0.0 on the first call, which only sets the time baseline)."""
        if self._green_ratio is None:
            # No camera measurement has ever arrived - there is nothing to
            # propagate. Refuse to invent a starting level.
            self._last_advance_time = now
            return 0.0

        fav = favourability(env.lux, env.temp_c, env.no3_ppm)
        mu = self._intrinsic_rate * fav
        self._last_growth_rate = mu

        if self._last_advance_time is None:
            self._last_advance_time = now
            return 0.0

        elapsed_days = (now - self._last_advance_time).total_seconds() / 86400.0
        if elapsed_days <= 0:
            self._last_advance_time = now
            return 0.0

        # Same outage guard as the evaporation engine - do not integrate
        # more than a day of unobserved growth in one step.
        capped = min(elapsed_days, 1.0)

        before = self._green_ratio
        K = self.config.carrying_capacity
        # Sub-step so explicit Euler stays accurate even for a full day.
        steps = max(int(capped * 4), 1)
        dt = capped / steps
        g = self._green_ratio
        for _ in range(steps):
            g = g + mu * g * (1.0 - g / K) * dt
            g = min(max(g, 0.0), K)
        self._green_ratio = g

        self._last_advance_time = now
        return self._green_ratio - before

    # ----------------------------------------------------------
    # Human severity ratings
    # ----------------------------------------------------------
    #
    # The camera measures a proxy (green pixel fraction) whose mapping to
    # "how bad is the algae" is unknown and installation-specific. A human
    # rating supplies that mapping directly, and does three jobs:
    #
    #   1. CORRECTS the modelled level, harder than a camera frame does.
    #      This is the only mechanism that can catch a lying camera - a
    #      fouled lens reads as pond algae and is otherwise invisible.
    #   2. CALIBRATES the alert thresholds, replacing the baseline-relative
    #      guesses with boundaries measured between labelled classes.
    #   3. FLAGS obstruction, retroactively removing a poisoned frame from
    #      the growth fit rather than leaving it to skew the rate forever.

    def _class_targets(self) -> dict:
        """Target green_ratio for each severity class.

        Where enough labels exist for a class, its target is the median
        green_ratio observed when the user picked it - a measured mapping
        from human semantics into model units. Classes without enough
        labels fall back to quantiles of this pond's own camera history,
        and if there is no history at all, to fixed defaults.

        The result is forced monotonic (a running max across the ordinal
        scale). With small samples a noisy median can easily put "minor"
        above "moderate", and a non-monotonic target set would make the
        correction move the estimate the wrong way.
        """
        by_class: dict = {}
        for r in self._ratings:
            if r.get("obstructed"):
                continue
            g = r.get("green_at")
            sev = r.get("severity")
            if g is None or sev not in SEVERITY_LEVELS:
                continue
            by_class.setdefault(sev, []).append(float(g))

        targets: dict = {}
        for cls, vals in by_class.items():
            if len(vals) >= MIN_LABELS_PER_CLASS:
                targets[cls] = _median(vals)

        history = sorted(h["v"] for h in self._camera_history)
        for cls in SEVERITY_LEVELS:
            if cls in targets:
                continue

            if cls == "none":
                # "none" is the ONE class with an absolute physical anchor:
                # no algae means approximately no green, whatever this
                # particular camera happens to be reporting.
                #
                # It must NOT fall back to a quantile of the camera's own
                # history, because that is circular in precisely the case
                # this whole feature exists to catch. If the lens has
                # fouled and every frame reads 0.28-0.30, the 10th
                # percentile of that range is 0.282 - so a user saying
                # "there is no algae" would move the estimate by ~5% and
                # the lying camera would win. Anchoring to an absolute
                # floor instead makes the correction decisive.
                #
                # Taking the MIN of the two also handles the opposite
                # case: a well-framed camera whose clean readings sit
                # below the default gets the lower, better value.
                #
                # This is deliberately biased low. A pond whose frame
                # includes green decking might genuinely read 0.05 when
                # clean, and this will under-shoot to 0.01 until two
                # "none" labels arrive and the measured median takes over.
                # Under-shooting is the safe direction: it says the pond
                # is cleaner than it is, and the user has just told us it
                # is clean, so suppressing an alarm is the correct
                # behaviour anyway.
                observed_floor = history[0] if history else _SEVERITY_DEFAULT["none"]
                targets[cls] = min(observed_floor, _SEVERITY_DEFAULT["none"])
            elif history:
                # minor/moderate/severe have no absolute meaning - how
                # green "moderate" looks depends entirely on framing - so
                # quantiles of this pond's own observed range remain the
                # best available guess until labels replace them.
                targets[cls] = _quantile(history, _SEVERITY_QUANTILE[cls])
            else:
                targets[cls] = _SEVERITY_DEFAULT[cls]

        # Enforce monotonicity across the ordinal scale.
        running = 0.0
        for cls in SEVERITY_LEVELS:
            running = max(running, float(targets[cls]))
            targets[cls] = running
        return targets

    def label_counts(self) -> dict:
        counts = {cls: 0 for cls in SEVERITY_LEVELS}
        counts["obstruction"] = 0
        for r in self._ratings:
            key = "obstruction" if r.get("obstructed") else r.get("severity")
            if key in counts:
                counts[key] += 1
        return counts

    def labels_needed(self) -> dict:
        """How many more ratings of each class are required before the
        thresholds become label-calibrated. Drives the progress hint on
        the rating card - "2 more moderate" is far more actionable than a
        bare "not yet calibrated"."""
        counts = self.label_counts()
        return {
            cls: max(MIN_LABELS_PER_CLASS - counts.get(cls, 0), 0)
            for cls in SEVERITY_LEVELS
        }

    def calibrate_thresholds(self) -> dict:
        """Alert thresholds from labelled class boundaries where possible.

        watch  sits between "minor" and "moderate" - where the user starts
               caring but nothing is urgent.
        action sits between "moderate" and "severe".

        Each boundary is the midpoint of the two class-conditional
        medians, and is only used when BOTH adjoining classes have enough
        labels. Otherwise that boundary falls back to the existing
        baseline-relative logic, so the two schemes can coexist while a
        calibration set is still being collected.
        """
        fallback = resolve_thresholds(self._history_as_samples())
        counts = self.label_counts()
        targets = self._class_targets()

        def calibrated(low: str, high: str):
            if counts.get(low, 0) >= MIN_LABELS_PER_CLASS and \
               counts.get(high, 0) >= MIN_LABELS_PER_CLASS:
                return (targets[low] + targets[high]) / 2.0
            return None

        watch = calibrated("minor", "moderate")
        action = calibrated("moderate", "severe")

        if watch is None and action is None:
            return fallback

        resolved_watch = watch if watch is not None else fallback["watch"]
        resolved_action = action if action is not None else fallback["action"]
        # A calibrated boundary can land below a fallback one; keep them ordered.
        if resolved_action <= resolved_watch:
            resolved_action = resolved_watch * 1.5

        labelled = sum(counts.get(c, 0) for c in SEVERITY_LEVELS)
        return {
            "watch": resolved_watch,
            "action": resolved_action,
            "baseline": fallback.get("baseline"),
            "mode": "label_calibrated" if (watch and action) else "partially_calibrated",
            "label_count": labelled,
            "watch_calibrated": watch is not None,
            "action_calibrated": action is not None,
        }

    def _history_as_samples(self) -> list:
        return [
            GreenSample(
                time=datetime.fromisoformat(h["t"]),
                green_ratio=h["v"],
                smoothed_green=h["v"],
                state="base",
            )
            for h in self._camera_history
        ]

    def detect_camera_drift(self) -> dict:
        """Looks for the failure mode nothing else in this system can see:
        the camera itself becoming greener over time (algae growing on the
        lens, staining, water spots) while the pond does not.

        Method: take only the frames the user labelled "none". If the pond
        genuinely has no algae on all of those occasions, their green_ratio
        should be flat. A rising trend across them means the MEASUREMENT is
        drifting, not the pond. Comparing the first half against the second
        half of that series is crude but needs very few labels, which is
        the binding constraint here.

        Returns a verdict rather than silently compensating - a fouled lens
        wants cleaning, not a software correction that hides it.
        """
        clean = [
            float(r["green_at"])
            for r in self._ratings
            if r.get("severity") == "none"
            and not r.get("obstructed")
            and r.get("green_at") is not None
        ]
        if len(clean) < 4:
            # Return the SAME key set as every other path. Omitting
            # "detail" here left the card rendering an empty explanation
            # line in the most common state a new pond is in.
            return {
                "verdict": "insufficient_data",
                "samples": len(clean),
                "early_median": None,
                "late_median": None,
                "ratio": None,
                "detail": (
                    f"Rate the pond \"no algae\" a few more times "
                    f"({4 - len(clean)} to go) and this will start checking "
                    f"whether the camera itself is drifting."
                ),
            }

        half = len(clean) // 2
        early, late = _median(clean[:half]), _median(clean[half:])
        if early <= 1e-9:
            ratio = float("inf") if late > 1e-9 else 1.0
        else:
            ratio = late / early

        # A doubling of the "no algae" reading is well beyond photometric
        # noise and indicates the baseline itself has moved.
        if ratio >= 2.0:
            verdict = "drift_suspected"
        elif ratio >= 1.4:
            verdict = "drift_possible"
        else:
            verdict = "stable"

        return {
            "verdict": verdict,
            "samples": len(clean),
            "early_median": round(early, 5),
            "late_median": round(late, 5),
            "ratio": round(ratio, 3) if ratio != float("inf") else None,
            "detail": (
                "Frames you rated 'no algae' are reading greener than they used to - "
                "the camera lens may be fouling. Worth wiping it."
                if verdict != "stable" else
                "Frames you rated 'no algae' read consistently over time - camera "
                "baseline looks stable."
            ),
        }

    def apply_severity_rating(
        self,
        time: datetime,
        severity: str,
        green_ratio_at_rating: Optional[float] = None,
        image_id: Optional[int] = None,
        rating_id: Optional[int] = None,
    ) -> dict:
        """Assimilates one human rating.

        severity: one of SEVERITY_LEVELS, or "obstruction".
        green_ratio_at_rating: what the camera reported for the frame being
            rated. Required for calibration to work - it is the other half
            of the (label, measurement) pair. The correction itself works
            without it.

        Returns a summary including the before/after level so the UI can
        show what changed and offer a meaningful undo.
        """
        severity = (severity or "").strip().lower()
        is_obstruction = severity == "obstruction"
        if not is_obstruction and severity not in SEVERITY_LEVELS:
            raise ValueError(
                f"severity must be one of {SEVERITY_LEVELS + ['obstruction']}, got {severity!r}"
            )

        prior = {
            "green_ratio": self._green_ratio,
            "thresholds": dict(self._thresholds),
            "intrinsic_rate": self._intrinsic_rate,
            "rate_source": self._rate_source,
            "confidence": self._confidence,
            "camera_history": [dict(h) for h in self._camera_history],
            "ratings_len": len(self._ratings),
        }

        before = self._green_ratio
        removed_frames = 0

        if is_obstruction:
            # The rated frame is not a measurement of the pond. Pull it out
            # of the history so it stops skewing the growth fit, and undo
            # the level correction it caused by reverting to the previous
            # clean reading.
            removed_frames = self._remove_history_near(
                time, green_ratio_at_rating
            )
            self._obstructed_count += 1
            if removed_frames and self._camera_history:
                self._green_ratio = self._camera_history[-1]["v"]
        else:
            targets = self._class_targets()
            target = targets[severity]
            if self._green_ratio is None:
                # First ever observation of any kind - adopt the target
                # outright rather than blending with nothing.
                self._green_ratio = target
            else:
                self._green_ratio = (
                    self.HUMAN_TRUST * target
                    + (1.0 - self.HUMAN_TRUST) * self._green_ratio
                )
            self._green_ratio = min(max(self._green_ratio, 1e-6),
                                    self.config.carrying_capacity)

        self._ratings.append({
            "t": time.isoformat(),
            "severity": None if is_obstruction else severity,
            "obstructed": is_obstruction,
            "green_at": float(green_ratio_at_rating) if green_ratio_at_rating is not None else None,
            "image_id": image_id,
            "rating_id": rating_id,
        })
        if len(self._ratings) > MAX_RETAINED_RATINGS:
            self._ratings = self._ratings[-MAX_RETAINED_RATINGS:]

        # Removing an obstructed frame changes the fit, so re-derive it.
        if removed_frames:
            self._refit_from_history()

        self._thresholds = self.calibrate_thresholds()
        self._last_advance_time = time

        self._undo_stack.append((rating_id, prior))
        if len(self._undo_stack) > 10:
            self._undo_stack = self._undo_stack[-10:]

        return {
            "green_ratio_before": round(before, 5) if before is not None else None,
            "green_ratio_after": round(self._green_ratio, 5) if self._green_ratio is not None else None,
            "removed_frames": removed_frames,
            "thresholds": self.thresholds,
            "label_counts": self.label_counts(),
            "undoable": True,
        }

    def _remove_history_near(
        self, time: datetime, green_value: Optional[float]
    ) -> int:
        """Drops the camera history entry the user just flagged as
        obstructed. Matches on value first (exact, when the UI passed the
        frame's green_ratio) and falls back to nearest-in-time, so this
        still works if the client omits the value."""
        if not self._camera_history:
            return 0

        if green_value is not None:
            for i in range(len(self._camera_history) - 1, -1, -1):
                if abs(self._camera_history[i]["v"] - float(green_value)) < 1e-9:
                    self._camera_history.pop(i)
                    return 1

        target_ts = time.timestamp()
        best_i, best_d = None, None
        for i, h in enumerate(self._camera_history):
            d = abs(datetime.fromisoformat(h["t"]).timestamp() - target_ts)
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        # Only remove if it is genuinely close in time (within 2 days),
        # otherwise we would be deleting an unrelated frame.
        if best_i is not None and best_d is not None and best_d <= 2 * 86400:
            self._camera_history.pop(best_i)
            return 1
        return 0

    def _refit_from_history(self) -> None:
        """Recomputes the growth rate over the retained history, keeping
        the favourability that was divided out last time. Used after a
        retroactive removal, where the conditions context has not changed
        but the data has."""
        realised = fit_specific_growth_rate(self._history_as_samples())
        if realised is None:
            self._intrinsic_rate = FALLBACK_INTRINSIC_RATE
            self._rate_source = "literature_fallback"
            self._confidence = "low"
        elif realised <= 0:
            self._intrinsic_rate = 0.0
            self._rate_source = "measured_declining"
            self._confidence = "medium"
        else:
            # Reuse the last known favourability implicitly by preserving
            # the ratio between realised and intrinsic from before.
            self._intrinsic_rate = max(realised, 0.0) / max(
                self._last_known_favourability(), 1e-6
            )
            self._rate_source = "fitted_from_camera"
            self._confidence = "high" if len(self._camera_history) >= 8 else "medium"

    def _last_known_favourability(self) -> float:
        """Recovers the favourability implied by the current
        realised/intrinsic pair, so a refit does not silently rescale the
        rate. Falls back to a mid value when nothing is known yet."""
        if self._intrinsic_rate > 1e-9 and self._last_growth_rate > 0:
            return min(max(self._last_growth_rate / self._intrinsic_rate, 1e-6), 1.0)
        return 0.5

    def undo_last_rating(self, rating_id: Optional[int] = None) -> bool:
        """Reverses the most recent rating exactly, restoring the level,
        thresholds, growth fit and camera history as they were.

        Only the MOST RECENT rating is exactly reversible - that is the
        mis-tap case, which is the one that matters. Deleting an older
        rating is handled by the caller removing its row and letting the
        next calibration pass re-derive thresholds without it; the level
        correction it caused has long since been overtaken.

        Returns False when there is nothing to undo, or when rating_id is
        supplied and does not match the top of the stack.
        """
        if not self._undo_stack:
            return False
        top_id, prior = self._undo_stack[-1]
        # When the caller names a rating, the top of the stack must BE
        # that rating. The earlier `top_id is not None` guard here was a
        # bug: after undoing rating 99 the stack top becomes an older
        # entry whose id is None, so a second tap of Undo passed the check
        # and silently reverted a legitimate earlier rating.
        if rating_id is not None and top_id != rating_id:
            return False

        self._undo_stack.pop()
        self._green_ratio = prior["green_ratio"]
        self._thresholds = prior["thresholds"]
        self._intrinsic_rate = prior["intrinsic_rate"]
        self._rate_source = prior["rate_source"]
        self._confidence = prior["confidence"]
        self._camera_history = prior["camera_history"]
        self._ratings = self._ratings[: prior["ratings_len"]]
        return True

    def load_ratings(self, rows: list) -> None:
        """Rehydrates ratings from algae_severity_ratings. Called when a
        twin is first created from a snapshot that predates ratings, so an
        existing calibration set is not lost."""
        parsed = []
        for r in rows or []:
            ts = r.get("rated_at")
            if ts is None:
                continue
            if not isinstance(ts, datetime):
                ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            parsed.append({
                "t": ts.isoformat(),
                "severity": r.get("severity") if not r.get("is_obstructed") else None,
                "obstructed": bool(r.get("is_obstructed")),
                "green_at": r.get("green_ratio_at_rating"),
                "image_id": r.get("image_id"),
                "rating_id": r.get("id"),
            })
        parsed.sort(key=lambda x: x["t"])
        self._ratings = parsed[-MAX_RETAINED_RATINGS:]
        self._thresholds = self.calibrate_thresholds()

    # ----------------------------------------------------------
    # Intervention handling (the "state reset")
    # ----------------------------------------------------------
    def apply_scrub(self, time: datetime, scrub_type: Optional[str] = None) -> None:
        """A logged ALGAE_SCRUB knocks visible biomass down; it does not
        sterilise the pond, so growth resumes from what remains. Applied
        immediately rather than waiting for the next camera frame, because
        the camera may not wake for hours and the user expects the card to
        reflect what they just did."""
        if self._green_ratio is not None:
            self._green_ratio = max(
                self._green_ratio * (1.0 - SCRUB_REMOVAL_EFFICIENCY), 1e-6
            )
        self._last_scrub_time = time

    def apply_water_change(self, time: datetime, volume_percent: Optional[float]) -> None:
        """A water change dilutes suspended algae in proportion to the
        volume replaced. Only meaningful for green-water (planktonic)
        algae, not for film on the liner, so the effect is deliberately
        damped to half the replaced fraction rather than applied fully -
        the camera looks at a surface where both coexist."""
        if self._green_ratio is None or volume_percent is None:
            return
        pct = min(max(float(volume_percent) / 100.0, 0.0), 1.0)
        self._green_ratio = max(self._green_ratio * (1.0 - pct * 0.5), 1e-6)

    # ----------------------------------------------------------
    # Current-state assessment
    # ----------------------------------------------------------
    @property
    def green_ratio(self) -> Optional[float]:
        return self._green_ratio

    @property
    def thresholds(self) -> dict:
        return dict(self._thresholds)

    @property
    def has_measurement(self) -> bool:
        return self._green_ratio is not None

    def assess(self, days_to_scrub: Optional[int] = None) -> Optional[AlgaeAssessment]:
        """Returns None when the camera has never reported - the honest
        answer is "unknown", not a fabricated Green."""
        if self._green_ratio is None:
            return None

        g = self._green_ratio
        watch = self._thresholds["watch"]
        action = self._thresholds["action"]

        if g >= action:
            status, category = "Red", "Scrub Due"
            advisory = (
                f"Green coverage is {g * 100:.2f}%, past this pond's alert level of "
                f"{action * 100:.2f}%. Scrub now - heavy algae swings dissolved oxygen "
                f"hard between day and night, which is when fish get into trouble."
            )
            scrub_now = True
        elif g >= watch:
            status, category = "Amber", "Watch"
            advisory = (
                f"Green coverage is {g * 100:.2f}%, above the {watch * 100:.2f}% watch "
                f"level. Growth is running at about {self._last_growth_rate * 100:.1f}%/day - "
                f"worth scheduling a scrub before it accelerates."
            )
            scrub_now = False
        else:
            status, category = "Green", "Clear"
            advisory = (
                f"Algae under control at {g * 100:.2f}% coverage"
                + (
                    ", currently receding."
                    if self._rate_source == "measured_declining"
                    else f", growing at about {self._last_growth_rate * 100:.1f}%/day."
                )
            )
            scrub_now = False

        counts = self.label_counts()
        labelled = sum(counts.get(c, 0) for c in SEVERITY_LEVELS)

        return AlgaeAssessment(
            status=status,
            category=category,
            green_ratio=round(g, 5),
            watch_threshold=round(watch, 5),
            action_threshold=round(action, 5),
            threshold_mode=self._thresholds.get("mode", "absolute"),
            growth_rate_per_day=round(self._last_growth_rate, 5),
            intrinsic_rate_per_day=round(self._intrinsic_rate, 5),
            rate_source=self._rate_source,
            confidence=self._confidence,
            sample_count=self._sample_count,
            days_to_scrub=days_to_scrub,
            advisory=advisory,
            scrub_now=scrub_now,
            label_count=labelled,
            calibrated=self._thresholds.get("mode") == "label_calibrated",
            camera_drift=self.detect_camera_drift()["verdict"],
        )

    # ----------------------------------------------------------
    # Forward projection
    # ----------------------------------------------------------
    def project_forward(
        self,
        *,
        daily_environment: list,
        horizon_days: int = 21,
        steps_per_day: int = 4,
        start_green: Optional[float] = None,
    ) -> dict:
        """Projects green_ratio forward from CURRENT engine state.
        Operates on local copies only - never mutates the engine.

        start_green overrides the starting level, used by
        project_scrub_benefit to ask the counterfactual "what if I scrub
        right now" without touching real state.
        """
        if horizon_days < 1:
            raise ValueError("horizon_days must be >= 1")
        if steps_per_day < 1:
            raise ValueError("steps_per_day must be >= 1")
        if not daily_environment:
            raise ValueError("daily_environment must contain at least one day")

        green = start_green if start_green is not None else self._green_ratio
        if green is None:
            return {
                "error": "no_camera_measurement",
                "detail": (
                    "The pond camera has not reported a clean frame yet, so there is "
                    "no measured algae level to project from."
                ),
            }

        initial_green = green
        K = self.config.carrying_capacity
        watch = self._thresholds["watch"]
        action = self._thresholds["action"]
        dt_days = 1.0 / steps_per_day

        trajectory = []
        first_watch_day = None
        first_action_day = None

        if green >= action:
            first_action_day = 0
        if green >= watch:
            first_watch_day = 0

        for day_index in range(horizon_days):
            days_from_now = day_index + 1
            env = daily_environment[min(day_index, len(daily_environment) - 1)]
            fav = favourability(env.lux, env.temp_c, env.no3_ppm)
            mu = self._intrinsic_rate * fav

            for _ in range(steps_per_day):
                green = green + mu * green * (1.0 - green / K) * dt_days
                green = min(max(green, 0.0), K)

            if green >= watch and first_watch_day is None:
                first_watch_day = days_from_now
            if green >= action and first_action_day is None:
                first_action_day = days_from_now

            trajectory.append({
                "days_from_now": days_from_now,
                "green_ratio": round(green, 5),
                "growth_rate_per_day": round(mu, 5),
                "favourability": round(fav, 4),
                "lux_assumed": env.lux,
                "temp_c_assumed": env.temp_c,
                "no3_ppm_assumed": env.no3_ppm,
            })

        candidates = [d for d in (first_watch_day, first_action_day) if d is not None]

        return {
            "current_green_ratio": round(initial_green, 5),
            "predicted_scrub_days_from_now": min(candidates) if candidates else None,
            "first_watch_days_from_now": first_watch_day,
            "first_action_days_from_now": first_action_day,
            "thresholds": self.thresholds,
            "intrinsic_rate_per_day": round(self._intrinsic_rate, 5),
            "realised_rate_per_day": round(self._last_growth_rate, 5),
            "rate_source": self._rate_source,
            "confidence": self._confidence,
            "sample_count": self._sample_count,
            "obstructed_sample_count": self._obstructed_count,
            "carrying_capacity": K,
            "days_using_real_forecast": min(len(daily_environment), horizon_days),
            "last_scrub_at": self._last_scrub_time.isoformat() if self._last_scrub_time else None,
            "label_counts": self.label_counts(),
            "labels_needed": self.labels_needed(),
            "camera_drift": self.detect_camera_drift(),
            "class_targets": {k: round(v, 5) for k, v in self._class_targets().items()},
            "trajectory": trajectory,
            "caveat": (
                "green_ratio is a camera-framing-dependent measure, not an absolute "
                "algae concentration. Thresholds are anchored to this pond's own "
                "observed baseline where enough history exists."
            ),
        }

    def project_scrub_benefit(
        self, *, daily_environment: list, horizon_days: int = 21
    ) -> dict:
        """Answers "if I scrub today, how long until I'm back here?".
        A date is useful; "scrubbing today buys you 12 days" is what
        actually helps someone decide whether to do it this weekend."""
        if self._green_ratio is None:
            return {"error": "no_camera_measurement"}

        post_scrub = max(self._green_ratio * (1.0 - SCRUB_REMOVAL_EFFICIENCY), 1e-6)
        projected = self.project_forward(
            daily_environment=daily_environment,
            horizon_days=horizon_days,
            start_green=post_scrub,
        )
        return {
            "post_scrub_green_ratio": round(post_scrub, 5),
            "removal_efficiency": SCRUB_REMOVAL_EFFICIENCY,
            "days_bought": projected.get("predicted_scrub_days_from_now"),
        }

    # ----------------------------------------------------------
    # Snapshot serialization
    # ----------------------------------------------------------
    def to_snapshot(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "green_ratio": self._green_ratio,
            "last_advance_time": self._last_advance_time.isoformat() if self._last_advance_time else None,
            "intrinsic_rate": self._intrinsic_rate,
            "rate_source": self._rate_source,
            "confidence": self._confidence,
            "last_camera_time": self._last_camera_time.isoformat() if self._last_camera_time else None,
            "last_scrub_time": self._last_scrub_time.isoformat() if self._last_scrub_time else None,
            "sample_count": self._sample_count,
            "obstructed_count": self._obstructed_count,
            "last_growth_rate": self._last_growth_rate,
            "camera_history": self._camera_history[-300:],
            "thresholds": self._thresholds,
            "ratings": self._ratings[-MAX_RETAINED_RATINGS:],
            # _undo_stack is deliberately NOT persisted. Undo is a
            # within-session affordance for a mis-tap; restoring a stale
            # pre-rating level after a restart (and after several poll
            # cycles have moved the model on) would be worse than not
            # offering it.
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "AlgaeGrowthEngine":
        engine = cls(AlgaeConfig(**snapshot.get("config", {})))
        engine._green_ratio = snapshot.get("green_ratio")
        lat = snapshot.get("last_advance_time")
        engine._last_advance_time = datetime.fromisoformat(lat) if lat else None
        engine._intrinsic_rate = snapshot.get("intrinsic_rate", FALLBACK_INTRINSIC_RATE)
        engine._rate_source = snapshot.get("rate_source", "literature_fallback")
        engine._confidence = snapshot.get("confidence", "low")
        lct = snapshot.get("last_camera_time")
        engine._last_camera_time = datetime.fromisoformat(lct) if lct else None
        lst = snapshot.get("last_scrub_time")
        engine._last_scrub_time = datetime.fromisoformat(lst) if lst else None
        engine._sample_count = snapshot.get("sample_count", 0)
        engine._obstructed_count = snapshot.get("obstructed_count", 0)
        engine._last_growth_rate = snapshot.get("last_growth_rate", 0.0)
        engine._camera_history = list(snapshot.get("camera_history", []))
        engine._ratings = list(snapshot.get("ratings", []))
        engine._thresholds = snapshot.get("thresholds") or {
            "watch": ABSOLUTE_WATCH_RATIO,
            "action": ABSOLUTE_ACTION_RATIO,
            "baseline": None,
            "mode": "absolute",
        }
        return engine
