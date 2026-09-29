#!/usr/bin/env python3
"""
rain_weighting.py
==================

QUESTION
--------
The heat signal is the precise one. But an owner still benefits from knowing
rain is coming today, or that heavy rain is due next week -- even at lower
confidence -- because PLANNING tolerates error that CONTROL does not. What is
the right confidence weight for the rainfall indicators, and can it be
resolved empirically rather than guessed?

FOUR THINGS THIS RESOLVES
-------------------------
1. CALIBRATION. The empirical P(outcome | tier) IS the confidence weight. It
   needs no model and no tuning -- it is a lookup table measured from 2,302
   days, reported with Wilson intervals.

2. THE SCALAR-MULTIPLIER IDEA. A flat multiplier on a score is mathematically
   identical to moving the decision threshold, because it preserves ranking.
   So the honest parameterisation is a threshold, and the frontier below shows
   every threshold's cost.

3. THE IMPLIED COST RATIO. We have no data on what a false alarm costs a koi
   owner versus a missed event, so the optimal threshold cannot be computed.
   But the question can be INVERTED: for each threshold, report the cost ratio
   at which that threshold would be optimal. The owner can then answer "is a
   false alarm one fifth as bad as a miss?" -- a question a human can actually
   answer -- instead of "what multiplier should I use?", which nobody can.

4. THE RELATIVE WEIGHT of rain vs heat signals, fitted by logistic regression
   with a TIME SPLIT, so the weight is validated out of sample rather than
   asserted.
"""

import numpy as np
import pandas as pd
from data_pipeline import build_merged_dataset, TIER_ORDER

TIER_RANK = {t: i for i, t in enumerate(TIER_ORDER)}


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def build():
    d = pd.read_csv("nea_validation_daily.csv", parse_dates=["target_date"])
    d["date"] = d.target_date.dt.tz_localize(None).dt.normalize()
    d = d.set_index("date").sort_index()

    # rebuild the outlook WETNESS signal (only temp was stored)
    merged, _ = build_merged_dataset(verbose=False)
    o = merged[merged.source == "4day"].copy()
    o["tr"] = o.tier.map(TIER_RANK)
    o["idate"] = pd.to_datetime(o.issued_at).dt.tz_localize(None).dt.normalize()
    wet = (o[o.lead_days.between(1, 4)].groupby("idate")["tr"].mean()
           .sort_index().rolling(5, min_periods=1).mean())
    d["out4_wet"] = wet.reindex(d.index)
    d["out4_wet_pct"] = d["out4_wet"].rank(pct=True)

    # forward-looking rain outcomes -- what an owner would want warning of
    mm = d["total_mm"]
    d["fwd7_max"] = mm[::-1].rolling(7, min_periods=1).max()[::-1].shift(-1)
    d["fwd7_sum"] = mm[::-1].rolling(7, min_periods=1).sum()[::-1].shift(-1)
    return d.dropna(subset=["out4_wet_pct"])


def main():
    d = build()
    n = len(d)
    print(f"{n} days, {d.index.min().date()} .. {d.index.max().date()}\n")

    outcomes = {
        "rain >=1mm today":        (d.total_mm >= 1).astype(int),
        "rain >=10mm today":       (d.total_mm >= 10).astype(int),
        "rain >=30mm today":       (d.total_mm >= 30).astype(int),
        "any >=30mm day next 7d":  (d.fwd7_max >= 30).astype(int),
        "next 7d total >=100mm":   (d.fwd7_sum >= 100).astype(int),
    }

    # ---------- 1. CALIBRATION: the weight, measured ----------
    print("=" * 104)
    print("1. CALIBRATION TABLE -- P(outcome | forecast tier). This IS the confidence weight.")
    print("=" * 104)
    for lab, y in outcomes.items():
        base = y.mean()
        print(f"\n  {lab}   (base rate {base:.3f})")
        print(f"  {'tier':>10} {'n':>6} {'%days':>7} {'P':>7} {'95% CI':>16} {'lift':>6}")
        for t in TIER_ORDER:
            m = d.tier == t
            if m.sum() < 10:
                continue
            k, nn = int(y[m].sum()), int(m.sum())
            lo, hi = wilson(k, nn)
            print(f"  {t:>10} {nn:>6} {100*m.mean():>6.1f}% {k/nn:>7.3f} "
                  f"[{lo:>5.3f},{hi:>5.3f}] {(k/nn)/base:>6.2f}")

    # ---------- 2. WEEK-AHEAD HEAVY RAIN ----------
    print("\n" + "=" * 104)
    print("2. WEEK-AHEAD HEAVY RAIN -- the owner's second use case, tested directly")
    print("=" * 104)
    for lab in ("any >=30mm day next 7d", "next 7d total >=100mm"):
        y = outcomes[lab]; base = y.mean()
        print(f"\n  {lab}  (base {base:.3f})")
        print(f"  {'outlook wetness':>22} {'n':>6} {'P':>7} {'95% CI':>16} {'lift':>6}")
        for q, qlab in ((0.80, "top 20% wettest"), (0.70, "top 30%"),
                        (0.50, "top 50%"), (0.20, "bottom 20% (quiet)")):
            m = (d.out4_wet_pct >= q) if q >= 0.5 else (d.out4_wet_pct <= q)
            k, nn = int(y[m].sum()), int(m.sum())
            lo, hi = wilson(k, nn)
            print(f"  {qlab:>22} {nn:>6} {k/nn:>7.3f} [{lo:>5.3f},{hi:>5.3f}] "
                  f"{(k/nn)/base:>6.2f}")

    # ---------- 3. THRESHOLD FRONTIER + IMPLIED COST RATIO ----------
    print("\n" + "=" * 104)
    print("3. THE FRONTIER -- and the cost ratio each threshold implies")
    print("=" * 104)
    print("   At an optimal threshold p*, acting is worth it when")
    print("   p*·C_miss = (1-p*)·C_falsealarm, so C_miss/C_FA = (1-p*)/p*.")
    print("   Read this backwards: pick the row whose cost ratio matches your judgement.\n")
    y = outcomes["rain >=10mm today"]
    base = y.mean()
    score = d.tier.map(TIER_RANK).astype(float)
    print(f"  {'fire when tier >=':>20} {'alerts/yr':>10} {'precision':>10} "
          f"{'recall':>8} {'lift':>6} {'implied C_miss:C_FA':>21}")
    yrs = n / 365.25
    for t in TIER_ORDER:
        m = score >= TIER_RANK[t]
        if m.sum() < 5:
            continue
        k, nn = int(y[m].sum()), int(m.sum())
        p = k / nn
        rec = k / y.sum()
        ratio = (1 - p) / p if p > 0 else np.inf
        print(f"  {t:>20} {nn/yrs:>10.0f} {p:>10.3f} {rec:>8.3f} "
              f"{p/base:>6.2f} {ratio:>19.1f}:1")

    # ---------- 4. THE SCALAR MULTIPLIER, ANSWERED ----------
    print("\n" + "=" * 104)
    print("4. DOES A FLAT SCALAR MULTIPLIER DO ANYTHING?")
    print("=" * 104)
    p_tier = d.tier.map(d.groupby("tier").apply(
        lambda g: (g.total_mm >= 10).mean())).astype(float)
    for mult in (1.0, 0.8, 0.6, 0.4):
        s = p_tier * mult
        fires = s >= 0.25
        if fires.sum() == 0:
            print(f"  x{mult:.1f} at fixed threshold 0.25 -> fires 0 days")
            continue
        print(f"  x{mult:.1f} at fixed threshold 0.25 -> fires {int(fires.sum()):>5} days, "
              f"precision {y[fires].mean():.3f}")
    print("\n  Multiplying every score by the same constant preserves their ORDER,")
    print("  so it cannot change which days rank above which. Against a fixed")
    print("  threshold it is exactly equivalent to raising that threshold, and it")
    print("  moves in discrete jumps as whole tiers cross the line. It is a")
    print("  threshold in disguise -- so tune the threshold, and read its cost")
    print("  ratio off the table above.")

    # ---------- 5. RELATIVE WEIGHT, FITTED OUT OF SAMPLE ----------
    print("\n" + "=" * 104)
    print("5. RELATIVE WEIGHT of rain vs heat signals (logistic, time-split)")
    print("=" * 104)
    z = lambda s: (s - s.mean()) / s.std()
    d["heat_sig"] = z(d.out4_temp.fillna(d.out4_temp.mean()))
    d["rain_sig"] = z(d.out4_wet_pct)
    d["stress_norain"] = (z(d.t_water) + z(d.evap_mm)) / 2
    targets = {
        "pond thermal stress (top 30%, no rain term)":
            (d.stress_norain >= d.stress_norain.quantile(0.70)).astype(int),
        "a >=10mm rain day":  outcomes["rain >=10mm today"],
    }
    mid = len(d) // 2
    for tlab, yv in targets.items():
        print(f"\n  Target: {tlab}")
        for feats, flab in (( ["heat_sig"], "heat only"),
                            ( ["rain_sig"], "rain only"),
                            ( ["heat_sig", "rain_sig"], "heat + rain")):
            X = d[feats].values
            Xtr, ytr = X[:mid], yv.values[:mid]
            Xte, yte = X[mid:], yv.values[mid:]
            # plain gradient-descent logistic; no sklearn dependency
            w = np.zeros(len(feats) + 1)
            A = np.c_[np.ones(len(Xtr)), Xtr]
            for _ in range(4000):
                pr = 1 / (1 + np.exp(-A @ w))
                w -= 0.5 * (A.T @ (pr - ytr)) / len(ytr)
            B = np.c_[np.ones(len(Xte)), Xte]
            pte = 1 / (1 + np.exp(-B @ w))
            # held-out AUC
            r = pd.Series(pte).rank().values
            n1 = yte.sum(); n0 = len(yte) - n1
            auc = (r[yte == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
            coefs = "  ".join(f"{f}={c:+.3f}" for f, c in zip(feats, w[1:]))
            print(f"    {flab:>12}: held-out AUC {auc:.3f}   {coefs}")

    d.to_csv("rain_weighting_daily.csv")
    print("\nWrote rain_weighting_daily.csv")


if __name__ == "__main__":
    main()
