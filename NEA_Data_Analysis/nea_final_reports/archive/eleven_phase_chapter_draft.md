# Chapter 4 — Exploiting NEA Telemetry and Forecasts

*(Section numbering is placeholder; renumber to fit the surrounding thesis.)*

---

## 4.1 Introduction and headline finding

Singapore's National Environment Agency publishes free, high-frequency
environmental data through the `data.gov.sg` platform. For a project concerned
with the automated management of an outdoor koi pond, this is an obvious
resource: rain changes pond chemistry, heat changes fish metabolism, and both
are measured and forecast at no cost. The question this chapter answers is not
*whether* NEA data is available, but **which parts of it carry information the
pond controller cannot obtain more cheaply from its own sensors.**

The answer is narrower than the project initially assumed, and the process of
narrowing it is the substance of this chapter.

### The central distinction

NEA supplies two products through the same platform, and this chapter's most
important organising claim is that **they performed completely differently**:

> The **real-time observation API** — rainfall, air temperature, relative
> humidity, wind — is load-bearing. It powers the rain gauge, the
> gauge-driven dosing controller (−13.5% hardener consumption at matched water
> quality), the Penman evaporation model, and the air-to-water thermal
> transfer. Its value was established early and was never subsequently in
> doubt.
>
> The **forecast API** — 24-hour general, 24-hour periods, and 4-day outlook —
> **failed every water-chemistry test applied to it across eight successive
> experiments.** It earns inclusion on exactly two narrow claims concerning
> *thermal* stress, both of which are validated below against independent
> observations and shown to hold in every individual year of a 6.3-year record.

Conflating these two products is the most common way to misread this work. A
reader who takes "NEA was useful" as a single verdict will overstate the
forecast's contribution; a reader who takes "the forecast mostly failed" as the
verdict will discard a telemetry stream that carries the project.

### What survives

| Advisory | Rule | Fires on | Precision | Lift |
|---|---|---|---|---|
| **"Clear day"** | forecast tier = No Rain | 15.6% of days (57/yr) | **0.861** [0.822, 0.893] | 1.72 |
| **"Heat is holding"** | water ≥ 30 °C **and** no storm forecast | 11.0% of days (40/yr) | **0.842** [0.792, 0.882] | 2.80 |

Both hold in **7 of 7 individual calendar years** with no re-tuning, and both
survive a 2,000-shuffle permutation control. Together they speak on
approximately 24% of the year.

A third, weaker use survives in a different form. Rainfall forecasting fails as
an *alert* but is defensible as a **displayed probability** for planning, where
the calibrated P(rain | tier) is measured directly rather than tuned, and where
the absence of a decision threshold means there is no false-positive rate to
manage (§4.8.3).

Everything else NEA's forecast was asked to do — predict damaging rain,
schedule water changes, reduce buffer consumption — was tested and rejected.
Section 4.5 documents those rejections, because the credibility of the two
surviving claims rests substantially on the number of alternatives eliminated
around them.

---

## 4.2 Data sources and preparation

### 4.2.1 Endpoints and volumes

| Source | Endpoint | Cadence | Records used |
|---|---|---|---|
| Rainfall (observation) | `/v2/real-time/api/rainfall` | 5-minute station totals | 2,391 days |
| Air temperature (observation) | `/v2/real-time/api/air-temperature` | sub-hourly | 1,928 days |
| Relative humidity (observation) | `/v2/real-time/api/relative-humidity` | sub-hourly | 1,928 days |
| 24-hour general forecast | `/v2/real-time/api/two-hr-forecast` family | ~2/day | 18,921 issuances |
| 24-hour periods forecast | as above, 6-hour blocks × 5 regions | ~2/day | 300,400 rows |
| 4-day outlook | `/v2/real-time/api/four-day-outlook` | ~2/day, leads 0–4 | 21,536 rows |

All sources were merged on their **common date range first**, rather than each
file's own claimed range. The usable intersection is **2020-02-02 to
2026-08-19, 2,391 days**, of which 2,302 carry a complete forecast-and-rainfall
record. Gaps were excluded from every calculation rather than imputed as zero.

### 4.2.2 A data-quality finding that materially affected results

The obvious rainfall station for a Woodlands pond is **S100 (Woodlands Road)**.
Checking its coverage before use revealed that **S100 reports on only 49.5% of
days (1,172 of 2,367)** — whole days absent from the archive, not partial-day
gaps of the kind the `completeness_pct` field already handles.

Its cluster neighbours S104, S210, S211 and S227 each cover 92–94%. Averaging
the four reduces missing days to **25 of 2,391 (99% coverage)**. This
four-station cluster is the ground truth throughout.

This is reported not as housekeeping but as a methodological point: **a model
scored on half the available days is not a model**, and the first action taken
against any new endpoint in this project was a coverage audit rather than a
skill measurement.

### 4.2.3 Scraper architecture

All observation endpoints were harvested by purpose-built scrapers sharing one
architecture, described here because two design decisions were forced by
defects encountered in practice.

**Pagination.** The v2 API paginates at 25 readings per page. The
`paginationToken` returned by the server proved to be `base64("offset=N")` —
not an opaque cursor — which permits direct computation of any page. An early
implementation inferred completion from an assumed page count and **failed
silently on days whose reading count was an exact multiple of 25.** The
corrected implementation treats a short page, or an absent token, as the
authoritative completion signal.

**Raw-first storage.** Every page is persisted verbatim before any
interpretation. Collation into daily aggregates is a separate offline stage, so
that a defect in aggregation logic costs a re-parse rather than a re-scrape
against the rate limit. Scrapes are resumable per page, not merely per day.

**Sampling mode for the climate endpoints.** Rainfall is reported as 5-minute
totals (~288 readings/day, ~12 pages). Air temperature and humidity are
reported far more frequently, implying ~58 pages/day and, across 2,400 days and
two endpoints, roughly a quarter of a million requests. Because the downstream
thermal model consumes only a *daily mean* — it is a 2.5-day lag filter, so
sub-hourly structure is irrelevant — the scraper defaults to sampling eight
fixed instants per day via the API's timestamp query form, reducing this to
eight requests per day per endpoint. The scraper auto-detects and reports the
true reading cadence so this decision rests on observation rather than
assumption.

*Limitation arising:* the resulting daily maximum is the maximum of eight
sampled instants and therefore **systematically under-reads the true daily
peak.** All analyses in this chapter that use daily maximum use it as a *rank*,
which is unaffected; no absolute temperature comparison is drawn from it.

---

## 4.3 Method: the standard of evidence

Five techniques recur throughout this chapter. They are stated once here and
referenced by name thereafter, because the consistency with which they were
applied is what distinguishes an accumulation of negative results from an
accumulation of failed attempts.

**1. Base-rate discipline.** Every claim is expressed as *lift* — precision
divided by the rate at which the outcome occurs anyway. In a climate where 72%
of days carry a thundery forecast, raw hit rates are meaningless. Lift 1.0 is
chance.

**2. Iso-cost and iso-quality comparison.** Policies are never compared at
unmatched outcomes. Each control policy is tuned over its own parameters to the
cheapest setting that still satisfies a fixed water-quality constraint, and
only then compared on cost. Without this, any policy can be made to look
efficient by degrading the water.

**3. Null controls.** Where a policy appears to extract value from a signal,
the same policy is re-run with the signal replaced by a random one of identical
firing rate. A result that survives is information; a result that does not is
structure. Section 4.5.6 records a case where this distinction reversed a
conclusion.

**4. Ceiling tests.** Before asking whether *this* forecast helps, an oracle
possessing perfect knowledge of future conditions is substituted. This bounds
what *any* forecast, however good, could contribute — and settles some
questions without reference to forecast accuracy at all.

**5. Persistence baselines.** A forecast must beat the sensor the system
already owns. Since a rain gauge reports rain with certainty shortly after it
falls, and a thermometer reports today's temperature exactly, the operative
question is never "is the forecast accurate?" but "does the forecast know
something the existing instrument will not know soon enough?"

### 4.3.1 Corrections applied during the work

Five defects were identified in this author's own analyses and are recorded
here because the corrections changed published conclusions.

| Defect | Effect | Correction |
|---|---|---|
| Rain-gauge warning marker never reset when the accumulation bucket drained | Median warning lead-time overstated as 23–34 h | Corrected to **1–2 h**; reversed the forecast-vs-gauge comparison entirely (§4.4.3) |
| Control arm ran a 14-day change cycle settling nitrate at ~105 mg/L | Control was over target on **97% of days** — an optimised system was being compared against a failing one | Control re-tuned to weekly; comparison re-run (§4.5.5) |
| Dose *units* counted where dosing *trips* were the labour quantity | Conflated consumable with labour | Both reported separately thereafter |
| Claim that an earlier experiment "already captured" a dry-spell saving | Assertion presented as result; no such arm existed | Retracted and tested properly, with a result that partially contradicted the original claim (§4.5.6) |
| De-circularisation removed the rainfall term but retained evaporation, which is itself computed from **forecast humidity** — and forecast humidity correlates with forecast tier at **r = +0.432** | The "fair test" of §4.7.3 still shared an input with the predictor | Re-run against a stress index containing **no NEA forecast field at all** (§4.7.3). Conclusion survives; the reported lift falls |

---

## 4.4 The rain hypothesis and its collapse

### 4.4.1 The initial premise

The project began from an intuitive premise: rainfall dilutes pond water,
dilution destabilises chemistry, chemistry destabilisation harms fish, and
therefore a rainfall forecast permits pre-emptive intervention. Every early
design decision followed from this.

### 4.4.2 The forecast is weak, and the reason is the base rate

Scored against measured rainfall at the pond cluster, forecast tier alone
predicts a ≥10 mm day with ROC-AUC as follows:

| Source | Lead (days) | AUC |
|---|---|---|
| 24-hour general | 0 | **0.604** |
| 4-day outlook | 1 | 0.600 |
| 4-day outlook | 2 | 0.591 |
| 4-day outlook | 3 | 0.587 |
| 4-day outlook | 4 | 0.579 |

The diagnosis matters more than the number. **`TL` (Thundery Showers)
constitutes 71–81% of all issuances.** Its hit rate for ≥10 mm rain is 27–28%
against a cluster base rate of 23.8% — a **lift of 1.14–1.20**. A call that
fires on three days in four functions as background rather than as a signal.

A secondary finding: **lead-time decay is conditional.** At a 100 mm adverse
threshold, AUC falls from 0.759 at one day to 0.501 at four — real decay to
chance. At a 10 mm threshold it barely moves (0.604 → 0.579). The forecast's
skill erodes with lead time only when it attempts to call something severe,
because for routine rain it was never precise enough to erode from.

### 4.4.3 A correction that reversed the user-intervention result

An intermediate experiment modelled realistic keeper availability rather than
assuming instantaneous response — nobody performs a water change during a 3 a.m.
downpour. Three personas were defined:

| Persona | Response window | Availability |
|---|---|---|
| Retiree | daily 07:00–21:00 | 58.3% |
| Work-from-home | weekday mornings/evenings + two afternoons | 37.5% |
| Office worker | weekday mornings and evenings only | 30.3% |

Under this gating the forecast initially appeared advantageous, because a
forecast can reach a keeper during waking hours whereas rain cannot. That
result was **wrong**, and the cause was the latched warning-marker defect in
the first row of §4.3.1: the median gauge warning lead-time was reported as
23–34 hours when the true figure is **1–2 hours**.

With the defect corrected, condition-based response — acting on rain that has
already fallen — wins at every threshold and every persona:

| Threshold | Persona | Forecast-driven exposure | Condition-driven exposure |
|---|---|---|---|
| 30 mm | Retiree | 193 | **153** |
| 30 mm | WFH | 296 | **255** |
| 30 mm | Office | 352 | **325** |
| 60 mm | Retiree | 62 | **29** |

### 4.4.4 The dimensional error, and the magnitude that ends the argument

The simulator's early configuration treated pond *size* as the determinant of
rain vulnerability. Challenged by the observation that no keeper would
plausibly change water every five days to keep fish alive, the model was
re-derived and found to contain a dimensional error.

Rainfall depth and pond depth are both lengths. Dilution is

$$f = 1 - \exp\!\left(-\frac{d_{\text{rain}}}{d_{\text{pond}}}\right)$$

in which **surface area cancels entirely**. Pond volume does not determine
vulnerability; pond *depth* does. A large shallow pond is more vulnerable than a
small deep one.

With the error corrected, intervention frequencies became plausible:

| Rain threshold | Water changes/month (mean) | Median | SD |
|---|---|---|---|
| 30 mm | **3.11** | 3.0 | 2.01 |
| 60 mm | 1.57 | 1.0 | 1.33 |
| 100 mm | 0.91 | 1.0 | 0.89 |

And the consequence that governs the remainder of this chapter:

> **100 mm of rain in a single day — an extreme event in Singapore — dilutes
> carbonate hardness by 8%. One ordinary day of fish load consumes 4%.** The
> heaviest day the local climate produces costs approximately two days of
> normal biological demand.

### 4.4.5 The reframe

Quantifying the buffer budget explained why:

| Source of KH depletion | Share | Observability |
|---|---|---|
| Nitrification (fish load) | **63%** | Deterministic given stocking and feeding |
| The keeper's own water changes | **24%** | Fully under user control, logged |
| Rain | **13%** | **Measured in real time by the gauge** |

**87% of buffer depletion is either deterministic or directly observable, and
the remaining 13% is measured by a sensor the system already possesses.** The
problem is therefore not prediction but bookkeeping — and prediction of the
smallest term cannot rescue it.

A Singapore-specific fact compounds this: local tap water carries KH 1–3, so a
water change *depletes* buffer rather than restoring it. The controlled
variable is a consumable under expensive, non-continuous measurement — a
fundamentally different and more tractable problem than predicting an
unobservable chemistry event.

---

## 4.5 Systematic elimination

Seven hypotheses concerning forecast exploitation were formulated, tested and
rejected. Each is presented in a common format: the hypothesis, why it was
plausible, the test, and what the result eliminates.

### 4.5.1 Spatial localisation

**Hypothesis.** `TL`'s weak discrimination is partly a "right storm, wrong
region" problem; scoping the forecast to the pond's own region will sharpen it.

**Why plausible.** The periods forecast is issued per region; convective rain is
spatially patchy.

**Test.** Score each of the five regional forecasts against measured
Woodlands-cluster rainfall at four adverse thresholds.

**Result.** The pond's own region is consistently the *worst or near-worst* of
the five: **north ranks 4th of 5 at 10 mm (AUC 0.637) and 5th of 5 at both
30 mm (0.626) and 60 mm (0.674).** Pooling all five regions beats north-only at
every threshold (0.678 vs 0.637 at 10 mm).

**Eliminates.** Regional labels do not resolve spatial structure at pond scale.
Singapore spans ~50 km and convective systems routinely cover most of it; the
five regions are five noisy observations of one island-scale process, so
averaging cancels noise. **The periods file's value proved to be temporal
de-aggregation (AUC 0.604 → 0.678), not spatial localisation.**

A related measurement quantifies the cost of asking a local question: the same
forecast scored against island-*maximum* rainfall achieves AUC 0.744 versus
0.678 for the pond cluster. **That 0.066 AUC gap is the price of spatial
downscaling** — the forecast genuinely knows whether the island will be hit and
is measurably worse at knowing whether one postcode will be.

### 4.5.2 Forecast persistence ("bracing for a punch")

**Hypothesis.** Meteorological systems seen approaching may be called on day 1,
miss, be called again on day 2, and so on — producing runs of false alarms
before a delayed event, and inflating the apparent false-alarm rate.

**Why plausible.** It would explain the 70–80% non-realisation rate of storm
calls without implying the forecast is uninformative.

**Test.** Conditional issuance probabilities after hits and misses; hazard rate
as a function of consecutive prior misses; ROC of streak length against today's
outcome. Run on both the general tier and the de-aggregated periods signal.

| | General tier | De-aggregated periods |
|---|---|---|
| P(call tomorrow \| called and HIT) | 0.855 | 0.638 |
| P(call tomorrow \| called and MISSED) | 0.837 | 0.628 |
| Withdrawal after a miss? | no | **no** |
| Streak length → today's outcome, AUC | 0.4948 | **0.4948** |
| Hazard at 0 / 3 / 5 prior misses | .294 / .309 / .304 | .366 / .367 / .455 |

**Result.** The forecast does not withdraw after a miss, but **streak length is
exactly chance**. There is no accumulating signal to exploit.

*Note on process:* this experiment was originally run on the island-wide
general signal before §4.5.1 had exposed that signal as an aggregation
artefact, and was re-run on the de-aggregated signal afterwards. The numbers
changed; the conclusion did not.

A related architectural test — combining backward-looking rainfall history with
forward-looking forecast — produced the only additive gain observed anywhere in
the project: back-7-days + periods + outlook reaches test AUC **0.677** against
0.664 for the best single signal. Real, and small.

### 4.5.3 Pond size as a resistance factor

**Hypothesis.** Larger ponds are buffered against rainfall dilution.

**Result.** Refuted by dimensional analysis (§4.4.4). Surface area cancels;
only depth matters.

**Eliminates.** Volume-based vulnerability scoring, and — importantly — makes
the chapter's central negative result explicitly **depth-dependent**. See
§4.9.

### 4.5.4 Rain as a driver of pond chemistry

**Result.** 100 mm rain = 8% of KH; one day of fish load = 4% (§4.4.4).

**Eliminates.** The entire premise that motivated forecast integration. Every
subsequent negative result in this section is a consequence of this one
magnitude.

### 4.5.5 Forecast-scheduled water changes

**Hypothesis.** Deferring a due water change when heavy rain is forecast avoids
redundant intervention, since rain will perform part of the dilution.

**Test.** Three-arm attribution at matched water quality (nitrate held under
50 mg/L on ≥99% of days), with 36 deferral configurations swept.

| Arm | Labour | vs control |
|---|---|---|
| A. Rule-based control | 182.4 min/mo | — |
| **B. Local sensing** (gauge + feeding log + self-calibrating KH model) | **148.4 min/mo** | **−18.6%** |
| C. Local sensing + NEA outlook deferral | 173.5 min/mo | **+16.9% worse** |

**Result.** Local sensing earns −18.6%; adding the forecast gives it back. The
best result across all 36 swept configurations is an exact tie, in a setting
where the deferral rule almost never fires.

Testing the *high-precision* half of the signal instead (§4.6.1 establishes
that the forecast is better at ruling rain out than in) did not rescue it:

| Policy | Labour | vs no forecast |
|---|---|---|
| **No forecast at all** | **148.4** | — |
| Defer when wet forecast | 173.5 | +16.9% |
| Act only when confidently dry | 181.8 | +22.5% |
| Change ahead of a dry spell | 175.7 | +18.4% |

**Eliminates.** Forecast-driven scheduling in all three directions tested.

### 4.5.6 Dry-spell hardener reduction — and the null control that decided it

**Hypothesis.** Reducing buffer dosing during a forecast dry spell saves
hardener, because rain-driven dilution — the reason buffer is carried — is not
coming.

**Why plausible.** It is the contrapositive of the wet-side rule, and it acts on
the *stronger* half of the signal.

**Test.** Nine dosing policies, each tuned over its own parameters to the
cheapest setting holding true KH above 3 dKH on ≥99% of days. Critically, two
**null controls** and two **ceiling arms** were included.

| Arm | dKH/month | vs periodic |
|---|---|---|
| Periodic dosing, no weather input | 6.52 | — |
| Periodic + reactive top-up after measured rain | 7.05 | **+8.1%** |
| **Periodic + dry-spell reduction (NEA forecast)** | **6.26** | **−4.0%** |
| *Null: same reduction rate, chosen by coin flip* | *6.25* | *−4.0%* |
| *Null: simply a smaller weekly dose, no weather* | *5.97* | *−8.3%* |
| *Ceiling: dry reduction with **perfect** foresight* | *5.95* | *−8.7%* |
| **Gauge-driven threshold dosing, no forecast** | **5.63** | **−13.5%** |
| Gauge + forecast-adaptive target | 5.63 | −13.5% |
| Gauge + **perfect-foresight** adaptive target | 5.63 | −13.5% |

**Result.** The forecast-driven policy does beat naïve periodic dosing by 4.0%.
**A coin flip firing at the same rate achieves 6.25 dKH/month — statistically
identical.** Dosing a slightly smaller fixed amount every week, with no weather
input whatsoever, achieves 5.97 (−8.3%) — twice the saving.

The apparent benefit is **dose quantisation, not information.** Six units per
week over-doses; five under-doses and fails the quality bar; the dry-spell rule
delivers an effective 5.8 units, and anything that trims that overshoot
captures the same gain.

The ceiling arms settle it definitively. On a periodic schedule, perfect
seven-day foresight is worth **8.7%** against 8.3% for a fixed smaller dose — a
margin of 0.4 percentage points. On the gauge-driven controller, perfect
foresight is worth **exactly 0.0%**: offered free and perfect knowledge of the
coming week, the tuner declines to use it.

**Mechanism.** Over any long horizon, hardener added must equal hardener lost:

```
added = fish-load consumption          ← fixed by stocking; not a lever
      + rain dilution   = KH_held × (1 − exp(−mm / depth))
      + water-change    = change_frac × (KH_held − KH_tap)
```

Both loss terms scale with **the KH being carried**. Across all nine policies,
the correlation between mean KH held and hardener consumed is **r = +0.99**.
There is one lever, and it is buffer level — not schedule, and not weather.

**Eliminates.** Dry-spell dosing policy, and by extension the last remaining
route by which the forecast might reduce a consumable.

### 4.5.7 Collapsing the wet tiers

**Hypothesis.** The rare tiers (Moderate, Severe) are precise but cover almost
nothing; Thundery covers everything but says nothing. A combined "heavier than
average rain" bucket should occupy a useful middle.

**Result.** Moderate and Severe together are 55 days; Thundery is 1,658. The
combined bucket *is* the Thundery bucket with a rounding error:

| Bucket | days/yr | % of days | P(≥10 mm) | Lift | Coverage |
|---|---|---|---|---|---|
| Moderate + Severe only | 9 | 2.4% | 0.527 | 2.19 | 5.2% |
| **Moderate + Severe + Thundery** | **272** | **74.4%** | **0.294** | **1.22** | **90.8%** |
| Thundery alone | 263 | 72.0% | 0.286 | 1.19 | 85.6% |

There is no middle to find, because the tier distribution has none: 16% Fair,
10% Light, **72% Thundery**, 2.4% everything above.

A second result eliminates the "stock up in advance" framing independently:
tested against whether the *following three days* fall in the wettest 30%,
Moderate/Severe calls achieve **lift 0.97** — precisely the base rate. Whatever
those tiers know, they know about the current day only.

**Magnitude check.** Even when such an alert is correct, the additional buffer
at risk is **+0.057 dKH** over three days — under a quarter of one dose —
against 0.45 dKH of fish-load consumption over the same period. The message
fails on magnitude before it fails on statistics.

### 4.5.8 A structural aside: the digital twin

A related architectural question was tested at the same time: does a full
feeding → ammonia → nitrite → nitrate → KH twin outperform a lumped model?

| Regime | Lumped error | Twin error | Winner |
|---|---|---|---|
| Steady routine | **0.035 dKH** | 0.075 dKH | Lumped, by 2× |
| Variable (seasonal, holidays, restocking) | 0.705 dKH | **0.079 dKH** | Twin, by 89% |
| 60% feeding-log error | **0.942 dKH** | 1.156 dKH | Lumped again |

**The structural argument matters more than the numbers: one measurement
identifies one lumped parameter.** A chained twin carries more states than a
single monthly KH titration can constrain, so its additional structure is worth
carrying only when an independent input — the feeding log — is accurate enough
to pin the chain down. Above roughly 40% logging error the twin becomes worse
than the model it replaces.

### 4.5.9 Why real information failed to help — the unifying principle

Across §4.5.1–4.5.7 the pattern is consistent, and the explanation is not that
the forecast is uninformative:

> **The forecast's information about rain is real. Information about rain is
> simply not scarce in this control problem.** Nitrate and carbonate hardness
> already integrate rainfall history; when rain falls, the gauge reports it with
> certainty shortly afterwards and the control trigger fires on its own. Being
> one step late costs nothing against a slow integrator with a wide safe band.
>
> **Anticipation pays only when acting late is expensive.**

This principle, rather than any individual negative result, is the transferable
finding of this section — and §4.6 is the consequence of taking it seriously
and asking where in the pond system acting late *is* expensive.

---

## 4.6 The reframe: thermal, not chemical

### 4.6.1 Asking the forecast a different question

Every experiment to this point asked *"does the forecast predict rain?"* In a
climate where 72% of days carry a thundery call, this asks the common call to
do the work. The rare call is the **no-rain** call, and a rare call can carry
far more information.

| Call | % of days | P(dry) | Lift | P(wet) | Lift |
|---|---|---|---|---|---|
| **No Rain / Fair** | 15.6% | **0.861** | **1.72** | 0.067 | 0.28 |
| Light | 10.0% | 0.574 | 1.15 | 0.117 | 0.49 |
| **Thundery** | **72.0%** | 0.424 | 0.85 | 0.286 | **1.19** |
| Moderate | 1.6% | 0.081 | 0.16 | 0.568 | 2.36 |
| Severe | 0.8% | 0.222 | 0.44 | 0.444 | 1.85 |

The asymmetry holds across every horizon tested:

| Window | "quiet" → dry spell | "wet" → wet spell |
|---|---|---|
| 1 day | 1.91 | 1.32 |
| **3 days** | **2.29** | 1.39 |
| 7 days | 1.99 | 1.35 |
| 14 days | 1.82 | 1.34 |
| 30 days | 1.41 | 1.33 |

> **The forecast is approximately 1.7× better at ruling rain OUT than at ruling
> it IN, at every horizon tested.** A rare "Fair" call carries lift 1.72; another
> "Thundery Showers" carries 1.19.

*(Figure 4.x — `chart_phase8.png`)*

### 4.6.2 What a dry day actually is

The dry call was initially framed as a maintenance-scheduling convenience. This
understates it. A forecast-dry day differs from other days in ways that are
predominantly **thermal and optical, not chemical**:

| Change on a forecast-dry day vs all others | |
|---|---|
| Rainfall | −80.4% |
| **Algal favourability (light × temperature)** | **+59.9%** |
| **Probability of a ≥30 °C water day** | **+48.2%** |
| **Evaporation** | **+33.7%** |
| Fish O₂ / ammonia demand | +1.9% |
| KH after a 7-day spell, net of top-up | +1.3% |
| Dissolved-oxygen saturation (mean) | −0.5% |

*(Figure 4.x — `chart_phase11.png`)*

The three effects large enough to act on are thermal and optical. The two
chemical effects sit inside the noise — the same boundary §4.5 established,
observed from the opposite direction.

### 4.6.3 The oxygen mechanism

Warm water holds less oxygen **and** fish demand more of it. Supply and demand
move in opposition simultaneously:

| Water temp | DO saturation | vs 28 °C | Fish O₂ demand (Q₁₀ = 2) | **Net margin** |
|---|---|---|---|---|
| 28 °C | 7.72 mg/L | — | 1.00× | — |
| 30 °C | 7.44 mg/L | −3.7% | 1.15× | **−16.2%** |
| 32 °C | 7.16 mg/L | −7.3% | 1.32× | **−29.8%** |

This is why standard koi ration tables reduce feeding from 2.5% to 1.5% of body
weight per day above 30 °C — a 40% action attached to a real boundary.

### 4.6.4 Evaporation

The water balance changes sign on a dry-call day:

| | Evaporation | Rainfall | **Net** |
|---|---|---|---|
| Forecast-dry day | 5.71 mm/day | 1.74 mm | **−3.98 mm/day** |
| All other days | 4.27 mm/day | 8.84 mm | **+4.57 mm/day** |

Observed dry runs number 490 across the record, with median length 2 days, 90th
percentile 5 days and maximum 17. A 7-day run costs 40 mm — 3.3% of a 1.2 m
column, or **333 L on a 10,000 L pond**; the longest observed run costs 810 L.

However, the persistence baseline applies here too: predicting the next three
days' evaporation load, **today's own measured conditions score AUC 0.916
against the outlook's 0.809.** Water level is the most directly observable state
variable in the system. The forecast's contribution is confined to the case
where the keeper is absent — planning a top-up reservoir before a trip — which
is genuine but rare.

### 4.6.5 The first result in which the forecast beats a local sensor

The decisive test applies the persistence baseline (§4.3, technique 5) to
temperature. Water temperature is modelled as a first-order lag on air
temperature (τ = 2.5 d, chosen from the thermal time constant of a 1.2 m water
column). The question: **does the 4-day outlook add anything to what the pond's
own thermometer already knows?**

| Horizon | Thermometer alone | + NEA outlook | Gain | 95% CI | 1st / 2nd half |
|---|---|---|---|---|---|
| **+1 day** | 0.965 | 0.924 | **−0.041** | [−0.049, −0.030] | **harmful** |
| **+3 days** | 0.875 | 0.900 | **+0.026** | [+0.014, +0.037] | +0.029 / +0.031 |
| **+5 days** | 0.804 | 0.855 | **+0.052** | [+0.040, +0.065] | +0.042 / +0.073 |

Paired bootstrap, 2,000 resamples. The combination is an unfitted rank average,
so no overfitting can explain the gain, and it is stable across both halves of
the record.

> **At one day out, adding the forecast makes the prediction worse — use the
> thermometer. At three to five days out, the forecast contributes skill the
> thermometer cannot supply.** This is the only crossover identified in the
> entire investigation, and it is on temperature rather than rain.

### 4.6.6 Why anticipation pays here and did not for chemistry

§4.5.9 established that anticipation pays only when acting late is expensive.
Carbonate hardness failed that test on three counts; oxygen passes on all three:

| | KH / rain | Heat / oxygen |
|---|---|---|
| Safe band | wide; slow integrator | **narrow — −30% margin at 32 °C** |
| Cost of acting late | nil — the gauge reports an hour later | **a dawn oxygen crash, measured in hours** |
| Risk shape | instantaneous, small | **cumulative over a multi-day stretch** |
| Action lead time | a dose acts immediately | **ration changes take days to propagate** |

Hard days also **cluster**: 20.9% of days are the second consecutive hard day,
against 9.0% if they occurred independently. Clustering is precisely why
anticipation has value here — stretches damage fish, isolated days do not.

---

## 4.7 Validation

### 4.7.1 The two claims, and year-by-year stability

A single multi-year precision figure can be produced by a rule that has merely
learned the local climatology, which would be worthless as a live product.
Splitting by calendar year — with thresholds fixed once on the full record and
applied unchanged to every year — tests whether the rule performs within each
year individually.

**Claim 1 — "Clear day": forecast calls No Rain → the day is genuinely dry
(<1 mm).** Verified against **measured rain-gauge observations**, the only claim
resting on a fully independent ground truth.

Full record: 360 days, 15.6% of days, 57/yr, precision **0.861** [0.822, 0.893],
base 0.500, **lift 1.72**, coverage 26.9%.

| Year | Fires | % days | Precision | Base | Lift |
|---|---|---|---|---|---|
| 2020 | 44 | 13.4% | 0.841 | 0.523 | 1.61 |
| 2021 | 62 | 17.7% | 0.903 | 0.509 | 1.78 |
| 2022 | 45 | 12.7% | **0.756** | 0.431 | 1.75 |
| 2023 | 41 | 12.2% | 0.902 | 0.503 | 1.79 |
| 2024 | 46 | 13.6% | 0.870 | 0.499 | 1.74 |
| 2025 | 68 | 18.7% | 0.824 | 0.484 | 1.70 |
| 2026 | 54 | 23.4% | **0.926** | 0.589 | 1.57 |

Precision SD 0.059. **The base rate varies from 0.431 to 0.589 across years
while lift stays within 1.57–1.79** — the signature of a rule performing
short-horizon work rather than tracking a climatological trend.

**Claim 2 — "Heat is holding": pond already ≥30 °C and no storm forecast → the
day lands in the top 30% of a composite pond-stress index** (equal-weight mean
of z-scores of water temperature, Penman evaporation, and −log rainfall).

Full record: 253 days, 11.0% of days, 40/yr, precision **0.842** [0.792, 0.882],
base 0.300, **lift 2.80**, coverage 30.8%.

| Year | Fires | % days | Precision | Base | Lift |
|---|---|---|---|---|---|
| 2020 | 31 | 9.4% | 0.806 | 0.295 | 2.74 |
| 2021 | 24 | 6.9% | 0.833 | 0.280 | 2.98 |
| 2022 | 22 | 6.2% | 0.818 | 0.175 | **4.68** |
| 2023 | 45 | 13.4% | **0.933** | 0.327 | 2.85 |
| 2024 | 55 | 16.3% | 0.873 | 0.356 | 2.45 |
| 2025 | 41 | 11.3% | **0.756** | 0.302 | 2.50 |
| 2026 | 35 | 15.2% | 0.829 | 0.407 | 2.04 |

Precision SD 0.055; lift clears 1.0 in **7 of 7 years**. **2022 was the wettest
year in the record (base rate 0.175 against 0.407 in 2026) and produced the
rule's highest lift, 4.68.** The rule performs within each year, including those
furthest from the average.

### 4.7.2 Permutation control

Both claims were tested against 2,000 random selections of the same number of
days:

| Claim | Real precision | Random, same n | Verdict |
|---|---|---|---|
| Clear day | **0.861** | 0.500 [0.453, 0.550] | **REAL** |
| Heat is holding | **0.842** | 0.301 [0.230, 0.375] | **REAL** |

### 4.7.3 De-circularisation

**Anticipated objection.** The composite stress index contains a rainfall term.
No-Rain days are low-rain by definition, so part of the measured skill is
tautological.

**First response — and why it was insufficient.** The index was initially
rebuilt from temperature and evaporation only, which moved the lift from 2.41
to 2.20 [2.03, 2.36] and appeared to settle the matter. It did not. The
evaporation term is computed from **forecast humidity**, and forecast humidity
correlates with the forecast rain tier at **r = +0.432** — they arrive in the
same payload and describe the same weather. The "fair test" still shared an
input with the predictor it was testing.

**Full response.** The stress index was rebuilt a second time from **observed
station data only** — measured air temperature and measured relative humidity,
with no NEA forecast field appearing anywhere in the target. Scored on the
1,829 days where observed climate and forecast records overlap:

| Rule | vs modelled stress | vs **observed-only** stress | n |
|---|---|---|---|
| No-Rain call | 2.51 [2.30, 2.70] | **2.06 [1.86, 2.25]** | 252 |
| No-Rain + hot outlook | 2.65 [2.33, 2.91] | **2.61 [2.32, 2.83]** | 106 |
| **Water ≥30 °C + no storm forecast** *(shipped Claim 2)* | 3.04 [2.80, 3.22] | **3.26 [3.13, 3.31]** | 140 |

**The conclusion survives, and the shipped rule improves.** Contamination cost
approximately 18% of the No-Rain call's lift and essentially nothing for either
combined rule; Claim 2 scores *higher* against a target built entirely from
independent measurements than against the modelled one.

This is recorded at length because the first de-circularisation looked adequate
and was not. Removing the obviously shared variable is not the same as removing
every shared input, and the difference only became visible once independent
observations were available (§4.7.4).

### 4.7.4 Independent validation against observed temperature

**Anticipated objection.** Every temperature figure above derives from NEA's own
forecast payload, verified against NEA's own same-day forecast. Shared model
bias could inflate the agreement.

**Response.** The air-temperature and relative-humidity observation endpoints
were scraped specifically to close this hole (§4.2.3), yielding 1,928 days of
station-measured temperature, of which 1,926 pass a ≥4-sample quality gate and
1,850 join to the forecast record.

Two immediate findings:

- **Correlation between observed daily maximum and forecast `temp_high` is only
  +0.565**, and the forecast reads **+1.82 °C high** on average. Part of that
  bias is the eight-instant sampling artefact of §4.2.3, but the modest
  correlation is real. **NEA's forecast temperature should never be quoted as an
  absolute value for a specific pond.**
- **Observed temperature has 101 distinct values; the forecast has 11.** The
  forecast is integer-quantised, which means a 70th-percentile cut lands on
  34 °C and sweeps up **47.5% of days** rather than 30%. *A 30%-threshold
  question could not previously be asked at all.*

### 4.7.5 The residual heat test

**Anticipated objection.** The rule may detect only obvious extremes, which
would make it useless in practice — Singapore's genuinely extreme days are rare
and self-evident.

**Response.** A test was constructed to make extremes incapable of carrying the
result:

1. Rank all 1,850 days by observed maximum temperature; the hottest 30%
   (t_max ≥ 32.60 °C, 558 days) form **Tier 1**.
2. **Delete them** — from the observed record and from the forecaster's
   predictions alike. 1,292 ordinary days remain, spanning 24.0–32.5 °C.
3. Within that residual, take the hottest 30% (t_max ≥ 31.70 °C, 406 days) as
   **Tier 2**.
4. Ask whether the forecaster's surviving calls still identify Tier 2.

| Predictor | Lift on Tier 1 | Lift on Tier 2 | Excess retained |
|---|---|---|---|
| 4-day outlook temp, top quartile | 1.45 | 1.39 | 86% |
| 24h forecast temp_high, top quartile | 1.58 | 1.41 | 71% |
| No-Rain call | 1.61 | 1.55 | 91% |
| **No-Rain AND hot outlook** | **2.13** | **2.18** | **104%** |

*"Excess retained" = (lift₂ − 1) ÷ (lift₁ − 1); 100% denotes fully graded skill,
0% an extreme-detector only.*

All four predictors survive at Fisher one-sided p < 1×10⁻⁵ and permutation
p = 0.0000 over 20,000 shuffles.

> **The forecast is not an extreme-detector.** With every extreme day removed, the
> combined signal still identifies the warmer end of an ordinary population at
> 2.18× the base rate — marginally *better* than it performs on the extremes.
> The skill is graded, which is the property the advisory requires.

A secondary observation: substituting daily *mean* for daily *maximum*, the
combined signal holds (93% retained) but the No-Rain call alone falls to 43%.
**The dry call tracks peak afternoon heating specifically** — consistent with a
cloud-cover mechanism.

---

## 4.8 Resulting specification

### 4.8.1 What NEA telemetry contributes

| Component | Inputs | Validated contribution |
|---|---|---|
| Rain gauge | rainfall API, 4-station cluster | ≥50 mm check-up alert at 100% precision and 100% coverage (it fires on rain already fallen); exact rain input to the chemistry model |
| Gauge-driven dosing controller | feeding log + gauge + periodic KH test | **−73% titrations**, −24% total labour, **−13.5% hardener** at matched water quality |
| Penman evaporation model | temperature, humidity, wind | Dry-day water balance changes sign; ~333 L per dry week on a 10,000 L pond |
| Air→water thermal transfer | air temperature | Converts a weather variable into the oxygen-margin calculation of §4.6.3 |

The dosing controller's enabling mechanism is worth recording: each manual KH
titration measures true KH, and the discrepancy between predicted and actual
depletion since the previous test *is a direct measurement of the model's rate
error*. Fed back with damped correction, the model self-calibrates:

| Test interval | Initial fish-load error | Static model | **Self-calibrating** |
|---|---|---|---|
| 28 days | 20% | 1.4 danger days | **0.0** |
| 28 days | 40% | 58.5 | **0.8** |
| 56 days | 40% | 216.7 | **8.6** |

*(mean days below safe KH, 12 seeds)*

**Monthly titration is safe even from a 40%-incorrect initial fish-load
estimate.** This is dead reckoning with periodic position fixes, and it is the
single largest labour reduction in the system.

### 4.8.2 What the NEA forecast contributes

Two advisories, and an explicit decision to remain silent otherwise:

| Message | Rule | Fires | Precision |
|---|---|---|---|
| "Clear day — good window for maintenance" | tier = No Rain | 57/yr | 0.861 |
| "Heat is holding — ease off feeding" | water ≥30 °C AND no storm forecast | 40/yr | 0.842 |
| *(silent)* | — | ~76% of days | — |

**Design consequence: the thundery call must not be surfaced.** It fires on 72%
of days at lift 1.19 and is the single feature most likely to train a user to
disregard the application.

**A second design consequence** follows from §4.6.2 and should be stated in the
interface, not merely in this chapter:

> The same 16% of days are the best days to work on the pond **and** the worst
> days for the fish in it — highest evaporation, highest water temperature,
> strongest algal growth, thinnest oxygen margin. An advisory that mentions only
> the convenience communicates half the finding.

### 4.8.3 The rain channel: a calibrated probability, not an alert

Sections 4.4 and 4.5 rejected rainfall forecasting for **control**. That
rejection does not extend to **planning**, and the distinction is worth making
precisely, because the two uses have different error economics: a control
action taken wrongly costs labour and consumables, whereas a planning nudge
taken wrongly costs the owner nothing but a rescheduled Saturday.

**The confidence weight requires no fitting — it is directly measurable.** The
empirical P(outcome | tier), over 2,280 days with Wilson intervals, *is* the
weight:

| Tier | % of days | P(≥1 mm) | P(≥10 mm) | P(≥30 mm) |
|---|---|---|---|---|
| No Rain | 15.7% | 0.140 | 0.067 | 0.014 |
| Light | 10.0% | 0.425 | 0.118 | 0.031 |
| Thundery | 72.0% | 0.576 | 0.285 | 0.091 |
| Moderate | 1.6% | **0.917** | **0.583** | 0.111 |
| Severe | 0.8% | 0.778 | 0.444 | 0.111 |

Two observations. The tier ordering is **not monotonic at the top** — Moderate
outperforms Severe at every threshold — though at n = 36 and n = 18 this is
weakly determined. And the table is usable *as it stands*: no tuning parameter
is required to display "Thundery — 29% chance of ≥10 mm today".

**A scalar confidence multiplier is a threshold in disguise.** Multiplying every
score by a constant preserves rank order and therefore cannot change which days
outrank which; against a fixed decision threshold it is exactly equivalent to
raising that threshold, and it moves in discrete tier-sized jumps. Tested
directly, multipliers of ×0.8 and ×0.6 produce **identical output** — 54 days at
precision 0.537 — because both resolve to "Moderate and above".

**The threshold cannot be optimised, because the loss ratio is unmeasured.** No
data exists on what a false alarm costs a keeper relative to a missed event. The
question can however be inverted: at an optimal threshold *p\**, acting breaks
even when C_miss/C_FA = (1 − *p\**)/*p\**, so each candidate threshold implies a
cost ratio the owner can evaluate directly.

| Fire when tier ≥ | Alerts/yr | Precision | Recall | Implied C_miss : C_FA |
|---|---|---|---|---|
| Light | 308 | 0.272 | 0.956 | 2.7 : 1 |
| Moderate | 272 | 0.293 | 0.907 | 2.4 : 1 |
| Heavy | 3 | 0.444 | 0.015 | 1.3 : 1 |

**The frontier is degenerate.** Because Thundery occupies 72% of days, the
operating points available are approximately 272 alerts per year or 3. There is
no usable middle, which is a structural property of the tier distribution rather
than a tuning failure.

**Week-ahead planning follows the same asymmetry as everything else.**

| Outlook | P(≥30 mm day in next 7 d) | Lift | P(≥100 mm total in next 7 d) | Lift |
|---|---|---|---|---|
| Wettest 20% | 0.488 | 1.29 | 0.146 | 1.12 |
| **Quietest 20%** | **0.215** | **0.57** | **0.040** | **0.30** |
| *(base rate)* | 0.379 | — | 0.131 | — |

Predicting a wet week is weak. **Predicting a quiet week reduces heavy-rain risk
by 70%** — the same dry-side advantage established in §4.6.1, now measured at the
week scale against forward rainfall totals.

**Design consequence.** For the planning use, do not threshold at all. A
threshold forces the system to assume a loss ratio on the owner's behalf, and
that ratio is precisely the unmeasured quantity. Displaying the calibrated
probability transfers the decision to the person who holds the loss function.
**A displayed probability has no false-positive rate, because it makes no
claim** — which is the one presentation under which a precision-0.285 signal is
honest rather than merely weak.

#### The rainfall signal's other, larger role

A logistic model fitted with a time split gives the relative weight of the two
forecast signals empirically. Fitted against the *modelled* stress index, the
outlook's **wetness** rank appeared to dominate its **temperature** (held-out
AUC 0.763 against 0.572) — a result that would have inverted this chapter's
emphasis. Re-fitted against **observed station temperature**, the ordering
reverses:

| Predictor of an observed top-30% hot day | Held-out AUC |
|---|---|
| Forecast temperature only | **0.706** |
| Forecast wetness only | 0.556 |
| Both | 0.706 |

The first result was the humidity contamination of §4.7.3 seen from another
angle. Against observed daily *mean* temperature the combination does help
slightly (0.699 → 0.719), with fitted coefficients of **+0.480 for temperature
and −0.236 for wetness** — approximately **2 : 1 in favour of temperature**, and
a negative sign for wetness because cloud suppresses heating.

That mechanism is independently confirmed: **days in the outlook's quietest 20%
run 1.85 °C hotter, by station thermometers, than days in its wettest 20%**
(31.86 °C against 30.02 °C).

> **The rainfall indicator therefore earns a weight — but as a secondary
> cloud-cover proxy inside the thermal channel, at roughly half the weight of
> temperature, not as a rain warning.**

Finally, the two signals should **not** be blended into a single confidence
score. Fitted against a ≥10 mm rain day, the temperature signal contributes a
coefficient of **+0.002** — nothing. The signals predict different outcomes, so
the correct architecture is two independent channels with separately stated
confidence, not one weighted composite.

### 4.8.4 What was explicitly rejected

For completeness, and because these are the alternatives a reader is likely to
propose:

| # | Hypothesis | Eliminated by |
|---|---|---|
| 1 | Spatial localisation sharpens the forecast | North ranks 5th of 5 for a north pond |
| 2 | Forecasts "brace for a punch" in runs | Streak-length AUC = 0.4948, exactly chance |
| 3 | Larger ponds resist rain better | Surface area cancels; only depth matters |
| 4 | Rain drives pond chemistry | 100 mm = 8% of KH; one day of fish = 4% |
| 5 | Forecast-deferred water changes save labour | +16.9% worse than using no forecast |
| 6 | A full digital twin beats the lumped model | Only above ~40% feeding-log accuracy |
| 7 | Dry-spell dosing saves hardener | **A coin flip matched it exactly** |

Additionally rejected: any wet-side warning. A "severe weather impending"
advisory would fire 8.7 times per year and be present for **6 of the 169 severe
rain days in six years — 3.6% coverage at 10.9% precision.** An advisory silent
for 96% of the events it purports to cover is not a warning system.

---

## 4.9 Limitations

Presented with the experiment that would remove each, and the direction in which
the conclusion would be expected to move — because a limitation without a
remedy is an admission, whereas a limitation with one is a research programme.

| Limitation | Claim affected | Removed by | Expected direction |
|---|---|---|---|
| **Depth dependence.** All conclusions assume a 1.2 m column. At 400 mm, 100 mm of rain removes **22% of KH rather than 8%** | The central negative result (§4.4.4) and everything downstream of it | Repeating the analysis at multiple depths | **Conclusions could invert for shallow ponds.** This is the most consequential limitation in the chapter |
| `kh_tap = 2.0` never measured | All buffer economics | One titration of the actual supply | Harder tap water → smaller problem, weaker case for the whole subsystem |
| Water temperature modelled, not measured (τ = 2.5 d, +1.0 °C offset both assumed) | Absolute frequency of ≥30 °C days, which **varies from 2% to 90% across plausible offsets** | Fitting the offset to the pond's own temperature sensor — one line, against data already logged | Frequency unidentified; **incremental forecast skill was positive in all 15 (τ, offset) combinations tested (+0.03 to +0.08 AUC)** |
| Penman level calibrated, not derived (raw output 7.89 mm/day against an accepted 3.5–5.0 band) | Absolute volumes (±25%) | Logging top-up volumes against elapsed days; fit the shelter factor first | Ratios and between-group differences unaffected |
| Algal light term is an **assumed** tier→cloud-cover map; nothing in the payload measures solar radiation | Every algae claim | Fitting realised growth from the camera's `green_ratio` series against forecast tier | Currently a hypothesis, not a result. **No algae claim should be cited as a finding until this is done** |
| Observed daily maximum is the maximum of 8 sampled instants | Absolute temperature comparisons | One week at full API resolution, compared against sampled mode | Under-reads the true peak; rank-based analyses unaffected |
| Several thresholds chosen after inspecting the data (percentile cuts, the ≥10/≥30 mm lines) | Effect sizes generally | Validation on a held-out period with pre-registered thresholds | Expect softening |
| Single station cluster, single climate | External validity | Replication elsewhere | Singapore's high, even rainfall is precisely what makes dry spells rare and therefore informative; a drier climate would invert the asymmetry of §4.6.1 |
| Forecast tier ordering is **non-monotonic at the top** — Moderate outperforms Severe at every rain threshold | The calibration table of §4.8.3 | More years, or pooling Moderate and Severe into one displayed band | n = 36 and n = 18; likely small-sample noise, but the table should not be presented as a monotonic ladder |
| Simulation not calibrated against a real pond's logged interventions | All labour figures | Instrumenting a working pond | Unknown |

---

## 4.10 Conclusion

The investigation began from the premise that a rainfall forecast would permit
pre-emptive management of pond chemistry. That premise was tested in seven
distinct forms and rejected in all seven, for a single quantifiable reason: **the
heaviest day of rain the local climate produces costs approximately two days of
ordinary biological demand**, and the rain gauge reports it with certainty
shortly after it falls.

What the investigation found instead was that the forecast's reliable statement
is not about rain at all. A No-Rain call — 16% of days, 86% precise, stable
across seven years — identifies days that are hot, bright, evaporative and
oxygen-thin. Combined with the pond's own thermometer it identifies genuinely
stressful days at 2.80× the base rate, and does so in a *graded* fashion:
with every extreme day removed from the analysis, the signal retains 104% of its
above-chance skill.

Rainfall forecasting is not thereby discarded, but it is relocated. It fails as
an alert because no threshold on it offers both precision and coverage, and
because the loss ratio required to place such a threshold has never been
measured. It is defensible as a **displayed probability** — the calibrated
P(rain | tier) is a direct measurement requiring no tuning, and a probability
that makes no claim has no false positives to suppress. Its larger contribution
turned out to be indirect: as a cloud-cover proxy it carries roughly half the
weight of temperature inside the thermal channel, with days in the outlook's
quietest quintile running **1.85 °C hotter by station thermometers** than those
in its wettest.

The distinction that should be carried forward is between NEA's two products.
The observation API justified its inclusion within the first phase of work and
was never subsequently challenged. The forecast API justifies its inclusion on
two thresholded messages, one displayed probability, and nothing else — a
narrower claim than the project set out to make, and the only one that survived
every test applied to it.

> Information about rain was never scarce in this problem — the gauge always had
> it. Information about the next five days of heat *is* scarce, and it is the
> only thing NEA's forecast supplies that nothing else can.

---

### Figures referenced

| Figure | File | Carries |
|---|---|---|
| 4.a | `chart_tier_auc_by_leadtime.png` | Forecast discrimination decays with lead only for severe events |
| 4.b | `chart_aggregation_collapse.png` | Localisation refuted; de-aggregation is where the value lies |
| 4.c | `chart_reality_check.png` | Intervention frequency after the dimensional correction |
| 4.d | `chart_phase8.png` | The dry/wet asymmetry across horizons |
| 4.e | `chart_phase9.png` | Which claims survive a confidence interval; the coverage problem |
| 4.f | `chart_phase10.png` | **The forecast and a coin flip save the same amount** |
| 4.g | `chart_phase11.png` | A dry day moves light, heat and water — not chemistry; the persistence crossover |

### Reproduction

```bash
python3 localisation.py                    # required first
python3 phase8_conditional.py              # tier table and conditionals
python3 phase10_hardener_arms.py           # nine-arm comparison + null controls
python3 phase11_dry_day_implications.py    # five domains + persistence controls
python3 nea_final_validation.py            # the two claims, year by year
python3 residual_heat_test.py              # observed-temperature validation
python3 rain_weighting.py                  # calibration table, cost ratios, fitted weights
```
