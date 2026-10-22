# derive the round-2 selected thresholds (seed 20261022, gate 22-06) and write frozen.json. Nothing else.
from robust import *
P,D,allf,core=setup(3.0); ponds=sorted(D); T=targets(P,D)
folds=folds_for(ponds,20261022)
raw=fit_scores(D,ponds,folds,T['low4'],allf,'boost')
R,thrs=evaluate(D,ponds,folds,raw,GATE,'reselect')
fz=dict(description="Frozen before robustness re-runs. Only the group-CV fold seed may change.",
 model="Low-DO@+60min (4 bins) HistGradientBoosting, features=all core+pond-relative (harness.features)",
 hyperparameters=dict(max_depth=3,learning_rate=0.05,max_iter=200,l2_regularization=1.0,min_samples_leaf=100,random_state=20261022),
 label_threshold_mgL=3.0, gate="22:00<=t<06:00 local hour (score set to 0 outside)", gate_hours=[22,6],
 selection_rule=dict(fa_max_per_pond_day=FA_MAX,occupancy_max=OCC_MAX,objective="max warned30 then min FA, on validation fold ponds"),
 alert_rule="2 consecutive positives confirm; first negative clears",
 round2_seed=20261022, round2_fold_thresholds=thrs, frozen_numeric_threshold_median=float(np.median(thrs)),
 round2_gated_result=summ(R,'frozen22-06 seed22'),
 note="nightgate.py gate zeroes NaN (ineligible) slots too, inflating pond-days (703-743 vs 596) and deflating FA/day; robust.py keeps NaN. nightgate.py gates are hour<7, not <6; the 11.8%/0.156 row is not produced by nightgate.py.")
json.dump(fz,open('frozen.json','w'),indent=1,default=float); print(json.dumps(fz,indent=1,default=float))
