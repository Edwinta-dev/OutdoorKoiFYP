# Part 3 — The road here

Telemetry was never really in doubt. A rain gauge measures the thing that
happens to the pond; the only questions were engineering ones. **The forecast
was the question mark from the first day, and it took eleven phases and four
reversals to find the narrow place where it works.** This is that road, in
order, dead ends included — because the dead ends are most of the intellectual
content, and a report that shows only the final claim is a weaker report.

---

## Phase 1 — Build the pipe, and discover the data is not what it says

The first job was mechanical: scrape rainfall, merge it against the three
forecast products, filter to the common date range **first** rather than
trusting each file's claimed range. The usable intersection is 2020-02-02 to
2026-08-19, **2,391 days**.

Two things went wrong immediately, and both were data-quality problems rather
than modelling problems:

- **Pagination.** The scraper assumed a fixed page count and broke on days
  whose reading count was an exact multiple of 25. The fix was to trust the
  *absence* of a `paginationToken` as the authoritative done-signal.
- **S100.** The obvious station for a Woodlands pond is present on **49.5% of
  days**. Not partial-day gaps — whole days missing. Switching to the
  four-station cluster took coverage to 99%.

**Lesson that recurred all project: check coverage before checking skill.** A
model scored on half the days is not a model.

## Phase 2 — The forecast is weak, and the reason is the base rate

First real test: does the forecast tier predict ≥10 mm of rain?

| Source | Lead | AUC |
|---|---|---|
| 24-hour | 0 | **0.604** |
| 4-day outlook | 1 | 0.600 |
| 4-day outlook | 4 | 0.579 |

Weak-but-real. The diagnosis was the important part: **`TL` (Thundery Showers)
is 71–81% of all issuances.** Its hit rate for ≥10 mm is 27–28% against a base
rate of 23.8% — **lift 1.14**. It fires so often it functions as background
noise rather than a storm call.

A second finding here aged well: **lead-time decay is conditional.** At a
100 mm threshold, AUC falls 0.759 → 0.501 across four days — real decay to
chance. At 10 mm it barely moves, 0.604 → 0.579. The forecast's signal only
erodes when it is trying to call something severe, *because for routine rain it
was never precise enough to erode from*.

### Dead end 1 — spatial localisation

The obvious hypothesis was that `TL`'s weak AUC was a "right storm, wrong
region" problem, and scoping the forecast to the pond's own region would sharpen
it. **Refuted unambiguously.** Scoring each region against actual
Woodlands rainfall, the pond's own region is the *worst or near-worst of the
five* at every threshold — north is 4th of 5 at 10 mm and 5th of 5 at 30 mm and
60 mm. Pooling all five regions beats north-only everywhere (0.678 vs 0.637 at
10 mm).

Singapore is ~50 km across and convective systems routinely span most of it.
The region labels are five noisy readings of one island-scale process, so
averaging them cancels noise. **The value in the periods file turned out to be
temporal de-aggregation (0.604 → 0.678), not spatial localisation at all.**

Later quantified: scoring the same forecast against island-MAX rainfall gives
AUC 0.744 versus 0.678 for the pond cluster. **That 0.066 gap is the price of
asking about one postcode rather than the island** — and no bracket
(north/south/east/west/central) does materially better, so the choice of
Woodlands cost nothing.

### Dead end 2 — "bracing for a punch"

A genuinely good hypothesis: if meteorology sees a system coming, the forecast
might call it on day 1, miss, keep calling it on day 2, and so on — a delayed
punch producing a run of 70–80% false alarms. Tested three ways:

| | General tier | De-aggregated periods |
|---|---|---|
| P(call tomorrow \| called & HIT) | 0.855 | 0.638 |
| P(call tomorrow \| called & MISSED) | 0.837 | 0.628 |
| Withdrawal after a miss? | no | **no** |
| Streak length → today's outcome, AUC | 0.4948 | **0.4948** |
| Hazard at 0 / 3 / 5 prior misses | .294 / .309 / .304 | .366 / .367 / .455 |

**Streak length is exactly chance.** The forecast does not withdraw after a
miss, but neither does a long run of misses tell you anything. There is no
accumulating "punch" to brace for. *(The two AUCs matching to four decimals
looked like a bug; checked — 0.494759 vs 0.494826 on different sample sizes.
Coincidence, both chance.)*

**This was correctly re-run later.** The first version used the island-wide
general signal, which §4 had just exposed as a MAX-collapse artifact. Redoing
it on the de-aggregated signal changed the numbers and not the conclusion.

## Phase 3 — Model the user, and find a bug in my own work

Simulating perfect user response is a fantasy: **nobody changes water in a 3 a.m.
downpour.** Three availability tiers:

| Persona | Window | Availability |
|---|---|---|
| Retiree | daily 07:00–21:00 | 58.3% |
| Work-from-home | weekday mornings/evenings + Tue/Thu afternoons | 37.5% |
| Office worker | weekday mornings/evenings only | 30.3% |

Under realistic availability the forecast finally showed an advantage —
**8.5% (retiree) to 19.5% (office) less exposure than condition-based response
at 30 mm** — because a forecast can reach the user during a window they are
actually awake for.

### The gauge lead-time bug — published wrong numbers, corrected

The first version claimed the rain gauge gives a median **23–34 hours** of
warning and that forecast-only territory was 9.5–24.3% of breaches. **Both
wrong.** The warning marker was never reset when the accumulation bucket drained
below the warning level, so a single early warning stayed latched for the rest
of the record.

Corrected: **median gauge lead is 1–2 hours, not 34.** And with the corrected
figures, condition-based response *wins* at every threshold and persona:

| Threshold | Persona | Forecast | Condition |
|---|---|---|---|
| 30 mm | Retiree | 193 | **153** |
| 30 mm | Office | 352 | **325** |
| 60 mm | Retiree | 62 | **29** |

The bug is in the report because it should be. The corrected direction is the
one that held for the next eight phases.

## Phase 4 — The dimensional error, and the reality check that forced it

The model said a 30 mm event triggers a water change. You pushed back:
*"I have a hard time visualising any pond owner willing to change water every
five days just to keep their fish alive."*

You were right, and finding out why exposed a **dimensional error**. The
simulator had been treating pond *size* as the thing that sets vulnerability.
It isn't. Rainfall depth is measured in millimetres, and so is pond depth —
**surface area cancels.** Dilution is `1 − exp(−rain_depth / pond_depth)` and
depends only on how deep the pond is.

Once that was fixed the numbers collapsed to something sane:

| Rain threshold | Water changes/month (mean) | median | SD |
|---|---|---|---|
| **30 mm** | **3.11** | 3.0 | 2.01 |
| 60 mm | 1.57 | 1.0 | 1.33 |
| 100 mm | 0.91 | 1.0 | 0.89 |

And the deeper consequence: **100 mm of rain in one day dilutes KH by 8%. One
ordinary day of fish load removes 4%.** The heaviest day Singapore produces
costs about two days of fish load. This one number explains every subsequent
negative result about the forecast.

### The reframe that saved the project

If rain barely matters, what does? The buffer budget:

| Source of KH depletion | Share |
|---|---|
| Nitrification (fish load) | **63%** |
| The keeper's own water changes | 24% |
| Rain | **13%** |

Plus the Singapore-specific fact that makes it a real problem: **tap water here
is KH 1–3, so a water change *depletes* buffer rather than restoring it.**

This was the right reframe — from "predict rain to save fish" to "manage a
consumable under expensive, non-continuous measurement". Every positive result
in the project came after it.

## Phase 5 — Self-calibration, and the labour is somewhere else

The KH model plus monthly testing gives **73% fewer tests** at near-equal
safety. But the labour column only moved **−12%**, because at 40 min per water
change, **changes are 72% of the month's labour and tests are 17%.** Optimising
test frequency was optimising the small term.

The genuine unlock was **self-calibration**: each test measures true KH, and the
gap between predicted and actual depletion is a direct measurement of the rate
error. With it, monthly testing is safe from a 40%-wrong starting estimate;
without it, that configuration produces 58 danger days.

**Also: no dry-spell arm was ever built here.** That omission caused a wrong
claim five phases later — see Phase 10.

## Phase 6 — The digital twin, and an identifiability argument

Would a full feeding → ammonia → nitrite → nitrate → KH twin beat the lumped
model? Tested honestly, the answer is *it depends on the pond*:

| Regime | Lumped error | Twin error | Winner |
|---|---|---|---|
| Steady routine | **0.035 dKH** | 0.075 dKH | Lumped, by 2× |
| Variable (seasons, holidays, restocking) | 0.705 dKH | **0.079 dKH** | Twin, by 89% |
| 60% feeding-log error | **0.942** | 1.156 | Lumped again |

**The structural argument matters more than the numbers: one measurement
identifies one lumped parameter.** A chained twin has more states than the
single monthly KH test can constrain, so its extra structure is only worth
carrying when an *independent* input (the feeding log) is accurate enough to
pin the chain down. Above ~40% logging error the twin becomes worse than the
thing it replaced.

Three bugs had to be fixed before the twin behaved at all: a headroom band too
tight for the test interval, an unclamped estimate compared against a clamped
truth, and an unstable gain feedback term.

## Phase 7 — Two errors in my own headline number

The three-arm attribution:

| Arm | Labour | vs control |
|---|---|---|
| A. Rule-based control | 182.4 min/mo | — |
| **B. Local sensing** | **148.4 min/mo** | **−18.6%** |
| C. Local + NEA outlook | 173.5 min/mo | **+16.9% worse** |

Before publishing this I found **two errors in my own earlier −24% claim**:

1. **The control arm was failing, not merely unoptimised.** It ran a 14-day
   cycle that settled nitrate at ~105 mg/L — over target on **97% of days**. I
   had been comparing an optimised system against a broken one. Fixed to weekly.
2. **I counted dose *units*, not dosing *trips*.** A trip is the labour; the
   units are the consumable. Different quantity entirely.

A self-referential over-target metric was also replaced with a fixed
`nitrate_yardstick = 50.0` so that a policy could not move its own goalposts.

**Local sensing earns −18.6%. NEA gives it back.** Swept 36 deferral
configurations; the best result anywhere is an exact tie, in a setting where
the rule almost never fires.

## Phase 8 — "Lose the resolution, see if we can gain the accuracy"

Your instruction, and it produced the largest single reversal in the project.

Every earlier analysis had asked *"does the forecast predict rain?"* In a
climate where 72% of days carry a thundery call, that asks the common call to
do the work. **The rare call is the no-rain call.**

| Call | % of days | P(dry) | Lift | P(wet) | Lift |
|---|---|---|---|---|---|
| **No Rain / Fair** | 15.6% | **0.861** | **1.72** | 0.067 | 0.28 |
| Light | 10.0% | 0.574 | 1.15 | 0.117 | 0.49 |
| **Thundery** | **72.0%** | 0.424 | 0.85 | 0.286 | **1.19** |
| Moderate | 1.6% | 0.081 | 0.16 | 0.568 | 2.36 |
| Severe | 0.8% | 0.222 | 0.44 | 0.444 | 1.85 |

And across every horizon the asymmetry holds — **the forecast is roughly 1.7×
better at ruling rain OUT than ruling it IN**, peaking at lift **2.29** over a
3-day window.

Your look-back/look-forward architecture also helped, modestly and honestly:

| Feature set | Test AUC |
|---|---|
| Look-back only (7-day rain) | 0.574 |
| Look-forward only (periods) | 0.664 |
| **Back-7 + periods + outlook** | **0.677** |

**But it still didn't rescue the scheduler.** Driving the water-change policy
off the *high-precision* half of the signal was tested three ways
(WET_DEFER / DRY_VETO / DRY_ADVANCE) across four thresholds each, and every one
lost to using no forecast at all. Which produced the sentence that explains the
entire project:

> **The forecast's information about rain is real. Information about rain just
> isn't scarce in this control problem.** Nitrate already integrates rain
> history; the gauge delivers the same information with certainty one step
> later; and one step late costs nothing against a slow integrator with a wide
> safe band. Anticipation only pays when acting late is expensive.

## Phase 9 — The advisory design, half of which didn't survive

Your proposal — speak only on the ~20% of days that carry information, stay
silent on the Light/Thundery noise — was **half right**, and the failing half
failed on *coverage*, which precision hides.

| Claim | n | Lift | 95% CI | Verdict |
|---|---|---|---|---|
| "Fair" → dry day | 360 | 1.72 | [1.64, 1.79] | **SOLID** |
| "Moderate" → wet day | 37 | 2.36 | [1.70, 2.97] | **SOLID** |
| "Moderate" → **severe rain** | 37 | 1.47 | [0.58, 3.37] | **not significant** |
| "Severe" → **severe rain** | 18 | 1.51 | [0.42, 4.47] | **not significant** |

Speaking only on Moderate/Heavy/Severe fires **8.7 times a year** and is present
for **6 of the 169 severe rain days in six years — 3.6% coverage at 11%
precision.**

> An advisory silent for 96% of the events it claims to warn about is not a
> warning system.

So: ship the dry side, cut *"severe weather impending, stock up asap"*
entirely.

## Phase 10 — The claim I got wrong, and the null test that caught it

You challenged the assertion that "reactive dosing already captures the
dry-spell saving." **You were right — Phase 5 never had a dry-spell arm at
all.** My claim was an assertion dressed as a result.

Tested properly, nine arms at matched water quality:

| Arm | dKH/mo | vs periodic |
|---|---|---|
| Periodic dosing, no weather | 6.52 | — |
| Periodic + reactive wet top-up | 7.05 | **+8.1%** |
| **Periodic + dry-spell reduction (NEA)** | **6.26** | **−4.0%** |
| *Same reduction, **coin flip*** | *6.25* | *−4.0%* |
| *Just a smaller weekly dose, no weather* | *5.97* | *−8.3%* |
| **Gauge threshold, no forecast** | **5.63** | **−13.5%** |
| Gauge + **perfect** forecast | 5.63 | −13.5% |

**A coin flip saved exactly as much as the forecast.** The −4% was dose
granularity, not information: six units a week overshoots, five undershoots, and
the dry rule quietly delivers an effective 5.8. And a perfect-foresight oracle
on the gauge controller saves **exactly 0.0%** — given free perfect knowledge of
the coming week, the tuner declines to use it.

The mechanism, finally stated cleanly:

```
hardener = fish-load consumption      ← fixed by stocking, not a lever
         + rain dilution   = KH_held × (1 − exp(−mm/depth))
         + water-change    = change_frac × (KH_held − KH_tap)
```

Both loss terms scale with the KH carried. Across all nine policies,
**corr(mean KH held, hardener used) = +0.99.** There is one lever and it is the
buffer level.

Also tested here: **collapsing the wet tiers finds no usable middle.**
Moderate+Severe is 55 days; Thundery is 1,658. The combined bucket *is* the
Thundery bucket with a rounding error (lift 1.19 → 1.22). And Moderate/Severe
calls carry **lift 0.97** — literally the base rate — for whether the *next
three days* are wet, so no "stock up for the coming days" message can be built
on them.

## Phase 11 — Asking the right variable at last

Eleven phases of asking the dry-day signal about **chemistry**, when a dry day
is barely about rain at all:

| What a forecast-dry day changes vs every other day | |
|---|---|
| Rainfall | −80.4% |
| **Algal favourability (light × temperature)** | **+59.9%** |
| **Chance of a ≥30 °C water day** | **+48.2%** |
| **Evaporation** | **+33.7%** |
| Fish O₂ / ammonia demand | +1.9% |
| KH after a 7-day spell, net of top-up | +1.3% |
| DO saturation (mean) | −0.5% |

And for the first time in the project, **the forecast beat a local sensor**:

| Horizon | Thermometer alone | + NEA outlook | Gain | 95% CI |
|---|---|---|---|---|
| +1 day | 0.965 | 0.924 | **−0.041** | [−0.049, −0.030] |
| +3 days | 0.875 | 0.900 | **+0.026** | [+0.014, +0.037] |
| +5 days | 0.804 | 0.855 | **+0.052** | [+0.040, +0.065] |

At one day out the forecast makes the answer worse — use the thermometer. At
three to five days it adds skill the thermometer cannot supply.

**Why anticipation pays here when it never did for KH:** the margin is narrow
(−30% at 32 °C, and a DO crash is measured in hours), the action has lead time
(ration changes should not be whipsawed daily), and the risk is cumulative — a
five-day hot bright stretch with a matured algal bloom is what kills fish at
dawn, not one hot afternoon. That is precisely the profile anticipation exists
for, and it is the exact opposite of KH on all three counts.

---

## What the dead ends were worth

Seven hypotheses were tested and rejected, and the report is stronger for each:

| # | Hypothesis | Killed by |
|---|---|---|
| 1 | Spatial localisation sharpens the forecast | North is 5th of 5 for a north pond |
| 2 | Forecasts "brace for a punch" in runs | Streak-length AUC = 0.4948, exactly chance |
| 3 | Bigger ponds are more rain-resistant | Surface area cancels; only depth matters |
| 4 | Rain drives pond chemistry | 100 mm = 8% of KH; one day of fish = 4% |
| 5 | Forecast-deferred water changes save labour | +16.9% worse than no forecast |
| 6 | A full digital twin beats the lumped model | Only above ~40% logging accuracy |
| 7 | Dry-spell dosing saves hardener | A coin flip matched it exactly |

And **four times the conclusion reversed under a fair test** — iso-cost and
iso-quality comparison, applied consistently, changed the answer in Phases 3, 7,
8 and 10. Two of those reversals were errors of my own that the fair test
caught: the latched gauge-warning marker, and the failing control arm.

> The single sentence the whole exploration converges on:
> **information about rain was never scarce in this problem — the gauge always
> had it. Information about the next five days of heat is scarce, and that is
> the only thing NEA's forecast sells that nothing else can.**
