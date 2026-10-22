# DO-free hypoxia redesign: hindcast results (round 2)

Sources (pinned): OutdoorKoiFYP `52d12764`, FWI `4cfa138e`, Oman `d95207b6`. Production repository not modified.
Code: `code/` (`load.py`, `harness.py`, `final.py`, `nowcast.py`, `night.py`, `variants.py`, `fig.py`). Seed 20261022.

## What each dataset can validate
| Dataset | Supervised hypoxia test? | Use here |
|---|---|---|
| FWI India (17 ponds) | **Yes**: DO is withheld ground truth; unseen-pond CV | All results below |
| Oman | No usable positives (zero retained <3 mg/L after quarantine, per round-1 audit; same commit) | False-alarm/transfer stress only; not re-run |
| Indonesia | No DO | Input QA / domain shift only; not re-run |

## Protocol (same definitions as round-1 report, reimplemented)
- 15-min bins, strict QC (channel flags, zeros, 8 Jan calibration day, damaged pH bulb). DO is used **only** for labels.
- Episode: ≥2 consecutive bins <3 mg/L, merged across ≤30 min normal gaps. An onset is scored only if the preceding 2 h were observed and normal → **374 scored onsets** (round 1 counted 310 with slightly different censoring).
- Alert: confirmed by 2 consecutive positives; cleared by the first negative. "Warned ≥30 min" means a *new* alert started 30–120 min before onset. A false alert is an alert that starts with no low DO in the next 2 h.
- 5 pond folds: train on 3 folds, select the threshold on the next fold (budget ≤0.177 false alerts/pond-day, ≤25% occupancy), test on the held-out fold. No pond ID is used as a feature. CIs come from a pond-level bootstrap (2,000 resamples).

## The change: pond-relative (self-normalised) features
Within a pond, pH tracks DO closely (e.g. ara2_0677080b: pH ≈8.45 ↔ DO ≈2 mg/L), but absolute pH differs between ponds and probes. New features use only each pond's own trailing pH and temperature: pH minus the 24 h/72 h min, median and 10th percentile; position within the 72 h pH range; drop from the 12 h maximum; change versus 24 h ago; 3 h and 6 h slopes; 24 h pH and temperature amplitude; a projected pH at dawn. All features are causal. They need about 24–72 h of warm-up.

## Result 1: advance warning of episode onset (unseen ponds, strict QC)
| Model | Warned ≥30 min | % (95% CI) | False alerts/pond-day (95% CI) |
|---|---|---|---|
| Round-1 report comparator | 14/310 | 4.5 | 0.177 |
| Prior-style booster, core features (my reimplementation) | 25/374 | 6.7 (3.4–11.0) | 0.342 (0.13–0.61) |
| **Onset logistic, core + pond-relative** | **49/374** | **13.1 (8.9–17.3)** | **0.189 (0.13–0.25)** |
| Onset booster, core + pond-relative | 56/374 | 15.0 (8.8–20.9) | 0.267 (0.14–0.41) |
| Low-DO@+60 min booster, clock + pond-relative | 53/374 | 14.2 (8.7–20.3) | 0.193 (0.08–0.33) |
| Night-only rule 18:00–06:00 | 48/374 | 12.8 | 1.086 |
| Existing `hypoxia.py` (clock-proxy lux) | 0/374 | 0 | 0 (temperature ≥30 °C in 0.01% of readings) |

Paired pond-bootstrap gain over the prior-style model: the booster gains +8.2 pp (3.4 to 13.0); the logistic gains +6.3 pp (−0.9 to 12.4).
With relaxed QC, the logistic scores 13.8% at 0.257/day, against 4.3% for the prior-style model.
Figure: `fig_warning_vs_fa.png`. That figure is a pooled sweep over thresholds on out-of-fold scores, for illustration only. The table rows above use thresholds chosen on validation ponds.

## Result 2: is DO low now / soon? (row-level, unseen ponds, `nowcast_unseen_pond.txt`)
| Inputs | Now: AUC / macro AP | +2 h: AUC / macro AP |
|---|---|---|
| Clock only | 0.85 / 0.57 | 0.85 / 0.56 |
| Core pH/temp + clock | 0.85 / 0.60 | 0.84 / 0.60 |
| Core + pond-relative (booster) | **0.90 / 0.71** | **0.89 / 0.70** |
Prevalence is about 22%. Raw pH and temperature add nothing over the clock. Pond-relative pH adds about +0.1 AP.

## Result 3: will tonight be bad? (issued 19:00, `night_level.txt`)
76% of monitored nights reach <3 mg/L, and 54–56% reach <2 mg/L (depending on night filtering). On unseen ponds, a 19:00 model separates bad from safe nights only weakly: AUC 0.59 (<3) and 0.63 (<2). The median first low-DO reading comes 8.5 h after 19:00, around 03:30.

## Verified findings
1. On every one of the 374 scored onsets, the 30 °C night gate in the existing engine stayed silent. These ponds rarely reach 30 °C, yet low DO occurs on most nights.
2. Pond-relative pH features about triple advance-warning yield compared with the round-1 comparator at a similar alarm burden (13.1% vs 4.5% at ~0.18/day). They match the night-only rule's yield with about one sixth of its false alerts.
3. Even so, about 85% of onsets get no ≥30 min warning at an acceptable burden. A dependable early-warning claim is **not** supported.
4. Clock time explains most of the detectability of low DO. Absolute pH and temperature add little across ponds; pH relative to the pond's own baseline adds a modest, consistent amount.

## Hypotheses (not established)
- pH responds to the same respiration/photosynthesis balance as DO, so it moves **with** DO rather than ahead of it. That would cap lead time with pH-only sensing. Explains Results 1 and 2, but not tested directly.
- Lux, aeration state and algae coverage could separate bad nights from safe ones; there is no data to test this yet.
- Transfer to Singapore koi ponds (cooler night temperature swings, ornamental stocking densities) is untested; FWI is one region and one season.

## Recommendation
Don't ship any of these as an "early warning". Replace the 30 °C gate with:
1. A **default night advisory** ("oxygen is unmeasured; low DO is common before dawn; aerate overnight"). This rule warns about every bad night, and has a ~24% false-night rate on FWI.
2. An optional **"pH pattern consistent with low oxygen now"** indicator from the pond-relative model, run in **shadow/research mode** until it is validated on your own ponds with a DO probe.
3. **`unknown` during warm-up** (<24 h of history) and whenever pH or temperature is stale.
Keep the legacy output fields (`level`, `flagged`, `explanation`, `advice`) and add `model_id`, `horizon`, `support`.

## FYP paragraph
> Using 17 Indian aquaculture ponds with dissolved oxygen withheld as ground truth, the original night-and-≥30 °C heuristic issued no warning for any of 374 low-oxygen onsets, because water rarely reached 30 °C while low oxygen occurred on 76% of nights. Features normalising pH to each pond's own trailing history improved unseen-pond warning yield from 4.5% (earlier analysis) to 13.1% (95% CI 8.9–17.3%) of onsets warned at least 30 minutes ahead, at 0.19 false alerts per pond-day. That matched a night-only rule's yield with about one sixth of its false alarms. Most onsets still received no advance warning. pH appears to track oxygen concurrently rather than lead it. The redesign therefore uses a conservative nightly advisory, with the pH-based indicator kept experimental pending local validation against a DO probe.
