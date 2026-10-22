"""hx_robust core: frozen-gate robustness runs. DO used only for labels. Only fold seed / label threshold vary."""
import os, json, pickle
import numpy as np, pandas as pd
import harness as H
from harness import load_fwi, prepare, MODELS, pick_thr, op_metrics
from final import summarise
OUT=os.environ['HX_OUT']; os.makedirs(OUT,exist_ok=True)
GATE=(22,6)   # 22:00 <= t < 06:00
FA_MAX=0.177; OCC_MAX=0.25
def gate_fn(F,s,g=GATE):
    if g is None: return s
    h=F.index.hour; keep=(h>=g[0])|(h<g[1]); s=s.copy(); s[(~keep)&~np.isnan(s)]=0.0; return s  # keep NaN (unmonitored) as NaN
def setup(thr_label=3.0):
    H.THR=thr_label
    P=load_fwi(True); D=prepare(True)
    allf=list(D[next(iter(D))]['F'].columns); core=D[next(iter(D))]['core']
    return P,D,allf,core
def targets(P,D):
    low4=lambda p:(P[p].do.shift(-4)<H.THR).astype(float).where(P[p].do.shift(-4).notna()).values
    return {'onset':lambda p:D[p]['y'],'low4':low4}
def folds_for(ponds,seed):
    perm=np.random.RandomState(seed).permutation(ponds); return [list(perm[i::5]) for i in range(5)]
def fit_scores(D,ponds,folds,tf,f,mdl):
    """raw (ungated) out-of-fold scores for test+val ponds of each fold."""
    raw={}
    for k in range(5):
        te=folds[k]; va=folds[(k+1)%5]; tr=[p for p in ponds if p not in te+va]
        X=[];Y=[]
        for p in tr:
            y=tf(p); m=D[p]['elig']&~np.isnan(y); X.append(D[p]['F'][f].values[m]); Y.append(y[m])
        clf=MODELS[mdl]().fit(np.vstack(X),np.concatenate(Y))
        for p in set(te)|set(va):
            s=np.full(len(D[p]['y']),np.nan); m=D[p]['elig']; s[m]=clf.predict_proba(D[p]['F'][f].values[m])[:,1]; raw[(k,p)]=s
    return raw
def evaluate(D,ponds,folds,raw,g,thr_mode,frozen_thr=None):
    """thr_mode 'reselect': choose on validation ponds (round-2 rule); 'fixed': use frozen numeric threshold."""
    rows=[];thrs=[]
    for k in range(5):
        te=folds[k]; va=folds[(k+1)%5]
        G=lambda p:gate_fn(D[p]['F'],raw[(k,p)],g)
        if thr_mode=='reselect':
            thr=pick_thr({p:G(p) for p in va},{p:D[p] for p in va},fa_max=FA_MAX,occ_max=OCC_MAX)
        else: thr=frozen_thr
        thrs.append(float(thr))
        for p in te:
            m=op_metrics({p:G(p)},thr,{p:D[p]}); m['pond']=p; m['fold']=k; rows.append(m)
    return pd.DataFrame(rows),thrs
def summ(R,name):
    R=R.copy(); R['model']=name; return summarise(R).iloc[0].to_dict()
