# WP3b: evaporation model alternatives (EXPLORATORY, not validated)
WP3 already examined this data (35 days, 10 runs, one open pan, no rain, no radiation). Everything here is selection on the same data: tier C exploratory at best.

## 1. Engine as implemented (Backend/koi/models/evaporation_engine.py)
- L145: u2 = u * (0.748 if 10 m else 1) * shelter_factor (default 0.6, L95).
- L146-148: es(T) Tetens in kPa (L120-122) x 7.50062 -> **mmHg** (L125-126). ea = es(Tair)*RH/100.
- L150-151: E = max(0.26 * (1 + 0.54*u2) * (es(Tw) - ea), 0) mm/day. No radiation, no Delta/(Delta+gamma) weighting, aerodynamic term only.
- **Discrepancy (likely bug, not tuning):** 0.26(1+0.54u) is the Penman open-water wind function for vapour pressure in **mbar/hPa**. Penman's original is 0.35(1+0.0098 u_miles/day) with e in **mmHg**; 0.0098 x 86400/1609.34 = 0.526 per m/s (so B=0.54 is fine), and 0.35/1.3332 = 0.2625 (so 0.26 is the hPa form). The engine feeds mmHg into the hPa coefficient, so it is low by a factor 1.333 (-25%) for all inputs. The docstring (L34-35) states mmHg with 0.26, which is the inconsistency. Not checked against FAO-56 Eq.6 (that is a Penman-Monteith form with different units; I compared with Penman 1948/1963 from memory, not a fetched source: verify before editing Backend).
- Other candidate reasons for underprediction (hypotheses): no radiation term (Penman's Eo = [Delta*Rn + gamma*Ea]/(Delta+gamma)); the default shelter 0.6 cuts wind on an open pan; pan heat/edge effects; daily mean applied to level drop with 0.1 mm resolution.

## 2. Results (leave-one-run-out, n=35, 10 runs; MAE mm/day, 95% run-bootstrap CI; dMAE negative = better than baseline)
| Candidate | Params fitted | MAE [CI] | Bias | r | dMAE vs LORO mean | dMAE vs persistence (25 d) |
|---|---|---|---|---|---|---|
| a engine sf=1.0 | 0 | 1.27 [0.89,1.79] | -1.20 | 0.73 | +0.25 [-0.11,0.58] | +0.43 [0.04,0.73] |
| a engine sf=0.6 (production default) | 0 | 1.47 [1.06,2.05] | -1.43 | 0.73 | +0.44 [0.08,0.78] | +0.60 [0.24,0.91] |
| b engine x k | 1 | 0.75 [0.53,0.98] | -0.05 | 0.66 | -0.27 [-0.66,-0.02] | -0.02 [-0.36,0.32] |
| c Dalton (a+b*u)*(es(Tw)-ea) | 2 | 0.77 [0.49,1.07] | 0.01 | 0.60 | -0.26 [-0.65,0.02] | -0.01 [-0.35,0.38] |
| d regression, <=3 of vpd,u,Tw,Ta,dep (nested) | 3-4 incl. intercept | 0.63 [0.45,0.82] | 0.15 | 0.77 | -0.40 [-0.87,-0.06] | -0.05 [-0.32,0.12] |
| e radiation (Hargreaves/PT/Penman Rn) | - | **Blocked** | | | | |
| f physics fix (0.35, B=0.526, mmHg, sf=1, no fit) | 0 | 0.86 [0.65,1.21] | -0.63 | 0.73 | -0.16 [-0.43,0.09] | +0.06 [-0.17,0.25] |
| f fix with wind x0.6 | 0 | 1.06 [0.76,1.53] | -0.94 | 0.73 | +0.03 [-0.26,0.32] | +0.20 [-0.10,0.44] |
| LORO training mean | 1 | 1.03 [0.67,1.55] | 0.03 | n/a | | |
| Persistence (25 d, 9 runs) | 0 | 0.78 | | | | |

Pooled predicted/observed: engine sf=1 0.585, physics fix 0.780 (observed mean 2.88 mm/day, mean wind 0.78 m/s).

## 3. Parameters, stability, deployment
- b: k per fold 1.58-1.79 (mean 1.68, sd 0.05). A pure unit fix gives 1.33, so about 0.35 of the factor remains unexplained (radiation/pan effects).
- c: a 0.46-0.58 (mean 0.50), b 0.11-0.24, b/a 0.21-0.53; physical Penman values are a=0.35, b/a=0.526. Stable but different from physics; wind range is tiny (mostly calm), so b is weakly identified.
- d: subset vpd+Tw chosen in 7/10 folds, vpd+Tw+Ta 2, u+Tw+dep 1. A selection among 25 subsets on 35 days; the CI excludes the mean but not persistence. Water temperature entering as a predictor is partly a seasonal proxy (June vs winter), i.e. it may learn the season, not the physics.
- n=35 days but only 10 independent runs (<=9 consecutive days each): even 2 parameters is a lot.
- e: PAN record has no site metadata or latitude (single web search for the authors/dataset found nothing), so Ra/Hargreaves cannot be computed: Blocked, not guessed. A lux scenario would not be radiation.
- Deployment inputs: a/b/c/f need Tw (ESP32), Tair, RH, wind (NEA, 10 m, plus shelter), all available. d needs the same (vpd, u, Tw, Ta). e would need Rn/Ra (not available; lux is not radiation). Rain: all variants ignore it (untested, all-dry data). Coefficients fitted on one open pan cannot be assumed to transfer to a sheltered ornamental pond (wind, shading, depth, heat storage, NEA-vs-local wind); shelter 0.6 is untested here and made things worse on the open pan.

## 4. Recommendation (evidence tier C, exploratory; selection on already-seen data)
**FIX the unit mismatch (verify against Penman 1948/FAO source first), then keep the physics structure; do not adopt fitted k/Dalton/regression.**
- The unit fix alone, with zero fitted parameters, halves the pooled shortfall (58% -> 78% of observed) and gives MAE 0.86, better than the training mean in point estimate (CI includes 0) and statistically tied with persistence. It is the only change justified by reference rather than by this dataset.
- The k/Dalton/regression candidates reach MAE 0.63-0.77, beating the mean, but not persistence, and would bake in an open-pan, single-site calibration. The residual ~22% underprediction after the fix is unexplained (radiation hypothesis untested).
- Caution: the unit fix raises production evaporation by 33% for a pond that was tuned (shelter 0.6, "4-5 mm/day" sanity check in the docstring) to the buggy formula; the shelter factor and the L38 sanity check should be re-derived together, and the fitted shelter path (fit_shelter_factor, issue #32) will absorb it once top-up logs exist.
- Credible validation needs a local pond water-balance log (top-ups, rain gauge, area, known losses), >=1 month dry+wet, with local wind and radiation/lux. Until then: no claim of accuracy on the koi pond.
