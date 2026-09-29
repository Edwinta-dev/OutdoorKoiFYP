# NEA in OutdoorKoi — What Earned Its Place, and What Didn't

**Summary of the NEA exploration · 2026-08-24 · eleven phases, 2,300+ days of
data, S104/Woodlands cluster**

---

## The one-paragraph answer

NEA supplies this project with **two products that performed completely
differently**, and conflating them is the single most common way to get the
justification wrong. The **real-time observation API** — rainfall, temperature,
humidity, wind — is load-bearing: it powers the rain gauge, the dosing
controller that cuts hardener 13.5%, the evaporation model and the air-to-water
thermal transfer. It justified itself early and has never been in doubt. The
**forecast API** — 24-hour, 24-hour periods, 4-day outlook — failed every
chemistry test put to it across eight phases, and earns its place on exactly
two narrow, well-validated claims about the *hardest days on the pond*: days
that are hot, bright, evaporative, algae-favourable and oxygen-thin. Between
them they speak on **≈24% of the year at 84–86% precision**, and both hold
**in all 7 individual years** of the record with no re-tuning. A third use —
rainfall as a *displayed probability* for planning rather than a thresholded
alert — is defensible on a directly measured calibration table (§2.5).

**The forecast is not a warning system for rain. It is a heads-up for heat.**

---

# Part 1 — Telemetry: the load-bearing half

Four things were built on the real-time API. Each is described here at the
level of detail the report needs: what it is, what it produces, what it is
worth, and where it is weak.

## 1.1 The rain gauge

**What it is.** A scraper against `api-open.data.gov.sg/v2/real-time/api/rainfall`,
pulling the 5-minute station network and collating to daily and hourly totals.
Pagination is `paginationToken = base64("offset=N")`; the absent-token case is
the authoritative done-signal (an early version assumed a fixed page count and
broke on days whose reading count was an exact multiple of 25).

**Station selection turned out to matter more than expected.** The obvious
Woodlands proxy, **S100 (Woodlands Road), is present on only 49.5% of days** —
1,172 of 2,367 — and those are whole missing days, not partial-day gaps that
the `completeness_pct` column already handles. Its cluster neighbours S104,
S210, S211 and S227 cover 92–94% each. Averaging the four gets to **25 missing
days out of 2,391 (99% coverage)**. Everything downstream uses that cluster.

**What it is worth.**

| Use | Precision | Coverage |
|---|---|---|
| ≥50 mm "check on the pond" alert | 100% | 100% |
| Rain input to the KH/nitrate model | exact | exact |
| Persistence baseline that beat the forecast repeatedly | — | — |

The ≥50 mm alert is trivially perfect because it fires on rain that has
**already fallen**. That sounds like a weak claim and is actually the strongest
one in the project: it is the reason the forecast kept losing. A measurement
delivers the same information as a forecast, with certainty, one step later —
and against a slow integrator, one step late costs nothing.

**Where it is weak.** Spatial downscaling. Scoring the same forecast against
progressively wider rainfall targets shows the pond cluster is the *hardest*
target: island-MAX rainfall scores AUC 0.744, the Woodlands cluster 0.678. That
**0.066 AUC gap is the price of asking about one postcode instead of the
island** — and it is a property of convective rain in a 50 km city-state, not
of the gauge.

## 1.2 The gauge-driven dosing controller

**What it is.** A running estimate of carbonate hardness driven by two inputs
the app already has — the feeding log (fish load) and the rain gauge — with
manual KH tests used only to correct accumulated drift. **Dead reckoning with
periodic position fixes.**

The chemistry is fixed stoichiometry, not fitted parameters: **7.14 mg CaCO₃
consumed per mg of NH₄-N nitrified**, and 1 dKH = 17.86 mg/L CaCO₃. For a
10,000 L pond at 100 g/day of 35%-protein feed that is **0.146 dKH/day**.

**The disturbance budget is what makes this tractable:**

| Source of KH depletion | Share | Observability |
|---|---|---|
| Nitrification (fish load) | **63%** | deterministic given stocking + feeding |
| The keeper's own water changes | **24%** | fully under user control, logged |
| Rain | **13%** | **measured in real time by the gauge** |

**87% of buffer depletion is either deterministic or directly observable, and
the remaining 13% is measured by a sensor you already have.** That is a
feedforward control problem with a known disturbance model — a fundamentally
easier class of problem than "predict whether an unobservable chemistry event
will harm fish."

**What it is worth.**

| Result | Figure |
|---|---|
| Manual weekly test-and-dose | 112.6 min/month, 3.79 tests/month |
| Model-led with monthly testing | 98.8 min/month, **1.04 tests/month (−73%)** |
| Total labour with change deferral | **85.9 min/month, −24%** |
| Hardener at matched water quality (Phase 10) | **−13.5%** vs periodic dosing |
| Local sensing vs rule-based control (Phase 7) | **−18.6%** labour |

**The unlock is self-calibration.** Each manual KH test measures true KH, and
the gap between predicted and actual depletion since the last test *is a direct
measurement of the rate error*. Fed back with a damped correction:

| Test interval | Initial fish-load error | Static model | **Self-calibrating** |
|---|---|---|---|
| 28 days | 20% | 1.4 danger days | **0.0** |
| 28 days | 40% | 58.5 | **0.8** |
| 56 days | 40% | 216.7 | **8.6** |

**Monthly testing is safe even starting from a 40%-wrong fish-load estimate.**
Without self-calibration, monthly testing at 40% error is dangerous — 58 danger
days, worst seed 559.

**Where it is weak.** The controller runs the pond close to its danger line
(mean KH 3.51 against a 3.0 floor), and the binding constraint is *estimate
uncertainty*, not weather. Verified this is a real bound and not a grid edge:
at trigger 2.75 the danger rate jumps to 25%; at 3.00 it is 0%. Also,
`kh_tap = 2.0` is the most load-bearing unverified parameter in the whole
project — a keeper on harder tap water has a different and smaller problem.

## 1.3 The Penman evaporation model

**What it is.** The aerodynamic term of Penman (1948),
`E [mm/day] = 0.26·(1 + 0.54·u₂)·(es_water − ea_air)`, with vapour pressures in
mmHg from the Tetens equation. Every input comes from the NEA payload:
temperature and relative-humidity midpoints, wind speed, rainfall.

**Two corrections were needed and both are worth reporting.**

1. **Wind shelter (×0.60).** NEA's forecast wind band midpoint (15 knots ≈
   7.7 m/s) is about **twice the live anemometer reading in the same payload**
   (6.9 knots ≈ 3.5 m/s). NEA forecasts open/coastal wind; a garden pond is
   sheltered. Uncorrected this inflated evaporation by ~45%.
2. **Level calibration.** Even after shelter correction, raw Penman off the
   forecast fields gives **7.89 mm/day**, above the accepted 3.5–5.0 mm/day
   tropical open-water band, because the humidity band midpoint understates the
   true daily mean. The series is scaled to a 4.5 mm/day mean. **Every ratio and
   between-group difference is unchanged by this; absolute litres are good to
   about ±25%.**

**What it produces.**

| | evaporation | rainfall | **net** |
|---|---|---|---|
| Forecast-dry day | 5.71 mm/day | 1.74 mm | **−3.98 mm/day** |
| Every other day | 4.27 mm/day | 8.84 mm | **+4.57 mm/day** |

The sign of the water balance flips. In run terms, on a 10,000 L pond:

| Dry run | Water lost | % of a 1.2 m column | Litres |
|---|---|---|---|
| 3 days | 17 mm | 1.4% | 143 L |
| 7 days | 40 mm | 3.3% | 333 L |
| 14 days | 80 mm | 6.7% | 667 L |
| 17 days *(longest observed)* | 97 mm | 8.1% | 810 L |

Real dry runs: **490 of them, median 2 days, 90th percentile 5, maximum 17.**

**Grounding.** Evaporation concentrates dissolved solids at a predictable rate,
`dTDS/dt = TDS·(loss/volume)`, and that prediction can be checked against the
TDS slope the probe actually measures — two independent sources, so agreement
is real corroboration rather than self-confirmation.

**Where it is weak.** Surface area is derived as `volume / assumed_depth`
(default 1.2 m) because it is not stored anywhere; this is the largest error
source in the volume projection. And the shelter factor is the single most
calibratable constant in the model — **if actual top-up volumes are ever logged
against elapsed days, fit that first.**

## 1.4 Air-to-water temperature transfer

**What it is.** The pond's thermal state is not measured by NEA, so it is
modelled as a first-order lag on NEA air temperature:

```
T_water[t] = T_water[t−1] + (T_air[t] + offset − T_water[t−1]) / τ
```

with **τ = 2.5 days** and a constant **+1.0 °C offset**. The time constant is
physically motivated: a 1.2 m water column stores ≈ 5.0 × 10⁶ J/m²K, and the
water–air exchange coefficient including evaporative loss is ≈ 25 W/m²K, giving
τ ≈ 2.3 days. A constant offset is used rather than a fitted regression because
over Singapore's narrow 25–34 °C band a fitted slope would be noise.

**Why it matters.** It converts a weather variable into the two things that
actually threaten fish:

| Water temp | DO saturation | vs 28 °C | Fish O₂ demand (Q10 = 2) | **Net margin** |
|---|---|---|---|---|
| 28 °C | 7.72 mg/L | — | 1.00× | — |
| 30 °C | 7.44 mg/L | −3.7% | 1.15× | **−16.2%** |
| 32 °C | 7.16 mg/L | −7.3% | 1.32× | **−29.8%** |

Supply falls and demand rises **together**. This is why the koi ration table
cuts feeding from 2.5% to 1.5% body weight/day above 30 °C.

**Where it is weak — and this is the serious one.** Both constants are assumed,
not fitted, and the model was tested across 15 combinations of offset (0–2 °C)
and τ (1.5–4 days):

- **The frequency of ≥30 °C days is NOT identified.** It swings from **2% to
  90%** across plausible offsets. **No absolute "hot days per year" figure
  should be reported** until the offset is fitted to the pond's own temperature
  sensor. That fit is one line of code against data already being logged.
- **The forecast's incremental skill IS robust.** The AUC gain over persistence
  is positive in all 15 combinations (+0.03 to +0.08), and the dry-call lift on
  hot days is positive throughout (1.03× to 3.64×).

## 1.5 Telemetry, summarised

| Component | Input | Output | Validated worth | Main weakness |
|---|---|---|---|---|
| Rain gauge | rainfall API, S104 cluster | daily/hourly mm | ≥50 mm alert at 100/100; beat the forecast in every control test | 0.066 AUC cost of postcode-level downscaling |
| Dosing controller | feeding log + gauge + periodic KH test | KH estimate, dose trigger | −73% tests, −24% labour, −13.5% hardener | rests on `kh_tap = 2.0` |
| Penman evaporation | temp, RH, wind | mm/day, top-up litres | dry-day water balance flips sign; 333 L per dry week | level calibrated, ±25%; surface area assumed |
| Air→water thermal | air temp | water temp, DO margin | converts weather into the O₂ squeeze | **frequency not identified; must fit offset** |

> **The observation API justified itself in Phase 3 and has never been the
> question.** Everything that follows is about the forecast.

---

# Part 2 — The forecast: the narrow, defensible half

**Validated 2026-08-24 on 2,302 days (2020-02-02 to 2026-08-19).** Script:
`nea_final_validation.py`. Nothing is fitted — thresholds are either NEA's own
labels or distribution quantiles computed once on the full record and applied
unchanged to every year.

## 2.1 Bounding the claim: what the forecast is NOT

The claim only means something because it is narrow. Eight phases of negative
results define its edges, and all of them belong in the report:

| The forecast cannot | Evidence |
|---|---|
| Predict rain usefully | `TL` is 72% of days at lift **1.19** |
| Identify severe rain | "Severe" → ≥30 mm: lift 1.51, **CI [0.42, 4.47]** |
| Warn about severe rain | fires 8.7×/yr, present for **6 of 169** severe days = **3.6% coverage** |
| Schedule water changes | forecast-deferred scheduling is **+16.9% worse** than no forecast |
| Save hardener | **a coin flip matched it exactly** (−4.0% vs −4.0%) |
| Beat a perfect oracle's ceiling | perfect 7-day foresight on the gauge controller saves **0.0%** |
| Help at 1-day lead on temperature | adding it makes AUC **worse**: −0.041 [−0.049, −0.030] |

**A combined "heavier than average" bucket does not rescue the wet side
either.** Moderate+Severe is 55 days; Thundery is 1,658 — merging them moves
lift from 1.19 to 1.22. There is no middle because the tier distribution has
none.

## 2.2 The two claims that survive

### CLAIM 1 — "Clear day"

The forecast calls **NO RAIN**. Verified against **measured rain-gauge
observations** — the only claim here resting on a fully independent ground truth.

| | |
|---|---|
| Fires on | **15.6% of days · 57/yr · n = 360** |
| Precision (day is genuinely dry, <1 mm) | **0.861** |
| 95% CI | [0.822, 0.893] |
| Base rate | 0.500 |
| **Lift** | **1.72** [1.64, 1.79] |

**Year by year:**

| Year | Days | Fires | % days | Precision | 95% CI | Base | Lift |
|---|---|---|---|---|---|---|---|
| 2020 | 329 | 44 | 13.4% | 0.841 | [0.706, 0.921] | 0.523 | 1.61 |
| 2021 | 350 | 62 | 17.7% | 0.903 | [0.805, 0.955] | 0.509 | 1.78 |
| 2022 | 355 | 45 | 12.7% | **0.756** | [0.613, 0.858] | 0.431 | 1.75 |
| 2023 | 336 | 41 | 12.2% | 0.902 | [0.775, 0.961] | 0.503 | 1.79 |
| 2024 | 337 | 46 | 13.6% | 0.870 | [0.743, 0.939] | 0.499 | 1.74 |
| 2025 | 364 | 68 | 18.7% | 0.824 | [0.716, 0.896] | 0.484 | 1.70 |
| 2026 | 231 | 54 | 23.4% | **0.926** | [0.824, 0.971] | 0.589 | 1.57 |

**Precision spread 0.170, SD 0.059; lift clears 1.0 in 7/7 years and stays in a
tight 1.57–1.79 band.** The base rate moves a lot year to year (0.431 in 2022 to
0.589 in 2026) and the *lift* barely moves — which is exactly the signature of a
rule doing real short-horizon work rather than reciting climatology.

This is the highest-precision statement available anywhere in the project, and
**nothing else in the system can produce it — a rain gauge cannot tell you
tomorrow is dry.**

### CLAIM 2 — "Heat is holding"

The pond is **already ≥30 °C** (sensor) **and no storm is forecast** to break the
heat (forecast). Outcome: the day lands in the **top 30% of a composite pond-stress
index** — the equal-weight mean of z-scores of water temperature, Penman
evaporation, and −log rainfall.

| | |
|---|---|
| Fires on | **11.0% of days · 40/yr · n = 253** |
| Precision (day is a top-30% stress day) | **0.842** |
| 95% CI | [0.792, 0.882] |
| Base rate | 0.300 |
| **Lift** | **2.80** |
| Coverage | 30.8% of all hard days |

**Year by year — this is the test that matters:**

| Year | Days | Fires | % days | Precision | 95% CI | Base | Lift |
|---|---|---|---|---|---|---|---|
| 2020 | 329 | 31 | 9.4% | 0.806 | [0.637, 0.908] | 0.295 | 2.74 |
| 2021 | 350 | 24 | 6.9% | 0.833 | [0.641, 0.933] | 0.280 | 2.98 |
| 2022 | 355 | 22 | 6.2% | 0.818 | [0.615, 0.927] | 0.175 | 4.68 |
| 2023 | 336 | 45 | 13.4% | **0.933** | [0.821, 0.977] | 0.327 | 2.85 |
| 2024 | 337 | 55 | 16.3% | 0.873 | [0.760, 0.937] | 0.356 | 2.45 |
| 2025 | 364 | 41 | 11.3% | **0.756** | [0.607, 0.862] | 0.302 | 2.50 |
| 2026 | 231 | 35 | 15.2% | 0.829 | [0.673, 0.919] | 0.407 | 2.04 |

**Precision spread 0.177, SD 0.055. Lift clears 1.0 in 7/7 years.** Worst year
(2025, 0.756) still sits well above that year's base rate of 0.302. 2022 was the
wettest year in the record — base rate 0.175 against 0.407 in 2026 — and the rule
held at 0.818 precision with **lift 4.68**, its best. **The rule is not tracking
the climate trend; it works within each year, including the years that differ
most from the average.**

### Operating points — the honest frontier

There is no single rule that is both precise and broad. Precision and coverage
trade directly:

| Rule | Fires on | Precision | Lift | Coverage |
|---|---|---|---|---|
| forecast: NO RAIN | 15.6% | 0.722 | 2.41 | **37.6%** |
| sensor: water ≥30 °C | 35.3% | 0.571 | 1.90 | **67.1%** |
| NO RAIN + hot outlook | 6.6% | 0.796 | 2.65 | 17.5% |
| water ≥30 °C + hot outlook | 16.9% | 0.588 | 1.96 | 33.0% |
| water ≥30 °C + NO RAIN | 7.6% | **0.886** | **2.95** | 22.4% |
| **water ≥30 °C + not thundery** ← shipped | **11.0%** | **0.842** | 2.80 | 30.8% |

Pick the operating point from how loud the message is. A quiet heads-up can
afford lower precision; anything that asks the keeper to *act* belongs at the
precise end. **Note the honest gap against the original target:** "80–90%
precision on 16%+ of days" is met by Claim 1 (15.6% at 86.1%) but the composite
stress outcome is stricter — at 16.9% of days the best available precision is
0.588. Together the two shipped messages speak on **≈24% of the year**.

### Permutation control

Both claims tested against 2,000 random selections of the same number of days:

| Claim | Real precision | Random, same n | Verdict |
|---|---|---|---|
| Clear day | **0.861** | 0.500 [0.453, 0.550] | **REAL** |
| Heat is holding | **0.842** | 0.301 [0.230, 0.375] | **REAL** |

### What a hard day measurably is

| | Hard days | All others | Difference |
|---|---|---|---|
| Water temperature | 30.24 °C | 29.59 °C | **+0.65 °C** |
| Evaporation | 5.61 mm/day | 4.02 mm/day | **+1.58** |
| Rainfall | 0.56 mm | 10.81 mm | **−10.24** |
| O₂ saturation | 7.404 mg/L | 7.496 mg/L | −0.092 |
| Fish O₂ demand | 1.169× | 1.117× | +0.051 |
| **O₂ margin (supply/demand)** | 6.346 | 6.722 | **−5.6%** |

**Hard days cluster: 20.9% of days are the second hard day in a row, against
9.0% if they were independent.** That clustering is why anticipation is worth
anything here — hard days arrive in stretches, and a stretch is what actually
hurts fish.

### The incremental-skill test — the one that matters

Precision alone proves nothing; the forecast has to beat the sensor the pond
already has. Predicting a ≥30 °C water day, paired bootstrap over 2,000
resamples, unfitted rank-average combination:

| Horizon | Thermometer alone | + NEA outlook | Gain | 95% CI | 1st / 2nd half |
|---|---|---|---|---|---|
| +1 day | 0.965 | 0.924 | **−0.041** | [−0.049, −0.030] | **harmful** |
| +3 days | 0.875 | 0.900 | **+0.026** | [+0.014, +0.037] | +0.029 / +0.031 |
| +5 days | 0.804 | 0.855 | **+0.052** | [+0.040, +0.065] | +0.042 / +0.073 |

> **Below three days, use the thermometer. Past three days, the forecast adds
> real skill nothing local can supply.** This is the only crossover found in
> eleven phases.

## 2.3 What a "hard day" actually is

Four stresses, one cause. They are not independent — they are the same clear-sky
day seen four ways — and that is *why* a single signal can call all four:

| Stress | Mechanism | Change on a forecast-dry day |
|---|---|---|
| **Evaporation** | high VPD + wind, no rain offset | **+33.7%** (net balance flips to −3.98 mm/day) |
| **Water temperature** | solar gain, no rain cooling | **+48.2%** chance of a ≥30 °C day |
| **Algal growth** | light × temperature × nutrient | **+59.9%** favourability; ×2.25 biomass over a week |
| **Dissolved oxygen** | supply ↓ and demand ↑ together | margin **−16.2%** at 30 °C, **−29.8%** at 32 °C |

DO is derived, not measured: `DO_sat(T) = 14.652 − 0.41022T + 0.007991T² −
0.000077774T³` for supply, and Q10 = 2 metabolic scaling for demand.

## 2.4 Why this one works when everything else failed

Phase 8's rule: **anticipation only pays when acting late is expensive.** KH
failed that test on all three counts; heat passes on all three.

| | KH / rain | Heat / oxygen |
|---|---|---|
| Safe band | wide, slow integrator | **narrow — −30% margin at 32 °C** |
| Cost of acting late | nil — the gauge tells you an hour later | **a dawn DO crash, measured in hours** |
| Risk shape | instantaneous and small | **cumulative over a 5-day stretch** |
| Action lead time | dose acts immediately | **ration changes take days to work through** |

## 2.5 The rain channel — a probability, not an alert

Rainfall forecasting fails as an alert (§2.1) but survives as a **displayed
probability**, because the confidence weight is directly measurable rather than
tuned. P(outcome | tier), 2,280 days, Wilson intervals:

| Tier | % of days | P(≥1 mm) | P(≥10 mm) | P(≥30 mm) |
|---|---|---|---|---|
| No Rain | 15.7% | 0.140 | 0.067 | 0.014 |
| Light | 10.0% | 0.425 | 0.118 | 0.031 |
| Thundery | 72.0% | 0.576 | 0.285 | 0.091 |
| Moderate | 1.6% | **0.917** | **0.583** | 0.111 |
| Severe | 0.8% | 0.778 | 0.444 | 0.111 |

*Caution: the ordering is non-monotonic at the top — Moderate beats Severe
everywhere — at n = 36 and n = 18.*

**A scalar confidence multiplier does nothing.** It preserves rank order, so
against a fixed threshold it is exactly equivalent to raising that threshold,
moving in discrete tier-sized jumps. Tested: ×0.8 and ×0.6 give **identical**
output (54 days, precision 0.537).

**The threshold cannot be optimised** — the false-alarm-to-miss cost ratio is
unmeasured. Inverted, each threshold implies a ratio the owner can judge:
Light 2.7 : 1, Moderate 2.4 : 1, Heavy 1.3 : 1. The frontier is degenerate
(272 alerts/yr or 3) because Thundery is 72% of days.

**Week-ahead follows the same asymmetry.** P(≥100 mm in the next 7 days) is
0.131 at base, **0.040 after a quietest-20% outlook (lift 0.30)**, and only
0.146 after a wettest-20% outlook (lift 1.12). Predicting a quiet week works;
predicting a wet one does not.

**Design rule: do not threshold the rain channel.** A threshold assumes a loss
ratio on the owner's behalf, and that is exactly the unmeasured quantity. A
displayed probability has no false-positive rate because it makes no claim.

### The rainfall signal's larger role — as a cloud proxy

Fitted against **observed station temperature** with a time split:

| Predictor of an observed top-30% hot day | Held-out AUC |
|---|---|
| Forecast temperature only | **0.706** |
| Forecast wetness only | 0.556 |
| Both | 0.706 |

Against observed daily *mean* the combination helps slightly (0.699 → 0.719),
coefficients **+0.480 temperature / −0.236 wetness ≈ 2 : 1**. Mechanism
confirmed independently: **quietest-20% outlook days run 1.85 °C hotter by
thermometer** than wettest-20% days (31.86 vs 30.02 °C).

**Do not blend the two signals into one score.** Against a ≥10 mm rain day the
temperature coefficient is **+0.002** — nothing. They predict different
outcomes; use two channels with separately stated confidence.

## 2.6 Threats to validity — read before citing

1. **Both claims hold in 7/7 individual years**, so they are not artifacts of
   pooling. Precision SD is 0.059 (Claim 1) and 0.055 (Claim 2).
2. **De-circularisation was initially incomplete.** Removing the rainfall term
   left evaporation, which is computed from *forecast humidity* — and forecast
   humidity correlates with forecast tier at **r = +0.432**. Re-run against a
   stress index built from **observed station data only**: No-Rain lift 2.51 →
   **2.06 [1.86, 2.25]**; the shipped Claim 2 rises 3.04 → **3.26**. The
   conclusion survives and the shipped rule improves.
3. **We do not hold observed temperature for the full record.** The verification target uses NEA's
   same-day (lead-0) forecast; the predictor uses the outlook issued 1–4 days
   earlier. Real separation in time, **none in source** — shared model bias
   could inflate agreement. *Claim 1 is immune to this: rainfall is measured.*
4. **Water temperature is modelled, not measured.** Across 15 (offset, τ)
   combinations the **frequency of ≥30 °C days swings from 2% to 90%** — do not
   report any absolute "hot days per year" until the offset is fitted to the
   pond's own sensor. The **incremental skill was positive in all 15**.
5. **Evaporation level is calibrated, not derived** — ratios hold, absolute
   litres ±25%.
5. **The algal light term is invented** — an assumed tier→cloud-free map. The
   temperature half is physical; the light half is a guess doing most of the
   work. **The camera's `green_ratio` series can settle this in an afternoon,
   and until it does, no algae claim belongs in the report as a result.**
7. **One station cluster, one climate.** Singapore's narrow 26–36 °C band makes
   every thermal effect small in absolute terms.

---

# Part 3 — The road here

*See the companion file `_part3_story.md` — the chronological account of eleven
phases, seven rejected hypotheses and four reversals. It is written and complete;
it was left as a separate file only because the shell was unavailable to
concatenate it into this one.*
