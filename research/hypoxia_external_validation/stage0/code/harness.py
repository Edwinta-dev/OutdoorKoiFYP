"""DO-free hypoxia onset hindcast on FWI, unseen-pond CV. DO used only for labels."""
import numpy as np, pandas as pd, json, sys
from load import load_fwi
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
SEED=20261022; THR=3.0; H=8  # 120 min in 15-min bins

def roll(s,w,f,minp): return getattr(s.rolling(w,min_periods=minp),f)()

def features(g):
    F=pd.DataFrame(index=g.index); ph,tp=g.ph,g.temp
    h=g.index.hour+g.index.minute/60
    F['ph']=ph; F['temp']=tp
    F['hs']=np.sin(2*np.pi*h/24); F['hc']=np.cos(2*np.pi*h/24)
    F['night']=((h>=18)|(h<6)).astype(float)
    for k,n in [(2,'30'),(8,'120')]:
        F['dph'+n]=ph-ph.shift(k); F['dtp'+n]=tp-tp.shift(k)
    F['ph_m2h']=roll(ph,8,'mean',8); F['ph_s2h']=roll(ph,8,'std',8)
    F['tp_m2h']=roll(tp,8,'mean',8); F['tp_s2h']=roll(tp,8,'std',8)
    core=list(F.columns)
    # pond-relative (self-normalised) features: trailing only
    for w,n in [(96,'24h'),(288,'72h')]:
        mn,mx,md=roll(ph,w,'min',w//3),roll(ph,w,'max',w//3),roll(ph,w,'median',w//3)
        F['ph_rmin'+n]=ph-mn; F['ph_rmed'+n]=ph-md
        F['ph_pos'+n]=(ph-mn)/(mx-mn).clip(lower=0.02)
        F['tp_rmed'+n]=tp-roll(tp,w,'median',w//3)
    F['ph_q10_72h']=ph-ph.rolling(288,min_periods=96).quantile(0.1)
    F['ph_drop12h']=ph-roll(ph,48,'max',16)
    F['ph_lag24']=ph-ph.shift(96); F['tp_lag24']=tp-tp.shift(96)
    F['dph180']=ph-ph.shift(12); F['dph360']=ph-ph.shift(24)
    hd=(6.5-h)%24; F['h2dawn']=np.where(hd<=12.5,hd,np.nan)
    F['ph_proj_dawn']=F.ph_rmin72h+(F.dph180/3)*F.h2dawn.fillna(0)
    F['amp24']=(roll(ph,96,'max',32)-roll(ph,96,'min',32))
    F['tamp24']=(roll(tp,96,'max',32)-roll(tp,96,'min',32))
    elig=F[['ph','temp','dph120','dtp120','ph_m2h','tp_m2h']].notna().all(1)
    return F,core,elig

def episodes(do):
    """confirmed low episodes: >=2 consecutive low bins; merge if <=2 normal bins between."""
    low=(do<THR); obs=do.notna(); n=len(do); eps=[]
    i=0; L=low.values; O=obs.values
    while i<n-1:
        if L[i] and L[i+1] and O[i] and O[i+1]:
            j=i
            while j+1<n and (L[j+1] or (not O[j+1])): j+=1
            eps.append([i,j]); i=j+1
        else: i+=1
    m=[]
    for e in eps:
        if m and e[0]-m[-1][1]-1<=2: m[-1][1]=e[1]
        else: m.append(e)
    return m

def onset_labels(g):
    do=g.do.values; n=len(do); eps=episodes(g.do)
    on=np.zeros(n,bool); ok_onsets=[]
    for s,e in eps:
        # censor: need observed normal just before and complete warning window [s-H, s-1] observed
        if s-H<0: continue
        w=do[s-H:s]
        if np.isnan(w).any() or (w<THR).any(): continue
        ok_onsets.append(s)
    # row target: onset (of any episode) within (t, t+H]
    starts=np.array([s for s,_ in eps])
    y=np.full(n,np.nan)
    lowany=np.zeros(n)
    dl=np.where(np.isnan(do),np.nan,(do<THR).astype(float))
    for t in range(n-H-1):
        fut=do[t+1:t+H+2]
        if np.isnan(fut).any(): continue
        y[t]=float(((starts>t)&(starts<=t+H)).any())
    # 'low within horizon incl now' for false alert definition
    lowfut=np.full(n,np.nan)
    for t in range(n):
        seg=dl[t:t+H+1]
        if len(seg)==0: continue
        lowfut[t]=1.0 if np.nansum(seg)>0 else (np.nan if np.isnan(seg).any() else 0.0)
    return y,np.array(ok_onsets,int),lowfut

def alerts(pos):
    """2 consecutive positives confirm; first neg/missing clears. return start idx list and active mask"""
    starts=[]; act=np.zeros(len(pos),bool); on=False
    for i in range(1,len(pos)):
        if on:
            if pos[i]==1: act[i]=True
            else: on=False
        elif pos[i]==1 and pos[i-1]==1:
            on=True; act[i]=True; starts.append(i)
    return starts,act

def op_metrics(scores,thr,ponds):
    ev=w30=w60=fa=0; slots=0; occ=0; leads=[]
    for p,d in ponds.items():
        s=scores[p]; pos=np.where(np.isnan(s),0,(s>=thr).astype(int))
        st,act=alerts(pos)
        st=np.array(st,int)
        for o in d['onsets']:
            ev+=1
            # new alert start in [o-H, o-2] and not already active at o-H-1
            c=st[(st>=o-H)&(st<=o-2)]
            if len(c) and not act[max(o-H-1,0)]:
                lead=(o-c.min())*15; leads.append(lead)
                w30+=1; w60+=lead>=60
        for a in st:
            lf=d['lowfut'][a]
            if lf==0: fa+=1
        el=~np.isnan(s); slots+=el.sum(); occ+=act[el].sum()
    days=slots/96
    return dict(events=ev,warned30=w30,warned60=w60,fa_per_day=fa/days if days else np.nan,
                occupancy=occ/slots if slots else np.nan,pond_days=days,median_lead=float(np.median(leads)) if leads else None)

def pick_thr(scores,ponds,fa_max=0.25,occ_max=0.25):
    allv=np.concatenate([v[~np.isnan(v)] for v in scores.values()])
    best=None
    for q in np.unique(np.quantile(allv,np.linspace(0.5,0.999,200))):
        m=op_metrics(scores,q,ponds)
        if m['fa_per_day']<=fa_max and m['occupancy']<=occ_max:
            key=(m['warned30'],-m['fa_per_day'])
            if best is None or key>best[0]: best=(key,q)
    return best[1] if best else np.inf

def prepare(strict=True):
    P=load_fwi(strict); D={}
    for p,g in P.items():
        F,core,elig=features(g); y,on,lf=onset_labels(g)
        D[p]=dict(F=F,core=core,elig=elig.values,y=y,onsets=on,lowfut=lf)
    return D

MODELS={
 'logit':lambda: make_pipeline(SimpleImputer(),StandardScaler(),LogisticRegression(C=0.1,max_iter=2000)),
 'boost':lambda: HistGradientBoostingClassifier(max_depth=3,learning_rate=0.05,max_iter=200,l2_regularization=1.0,min_samples_leaf=100,random_state=SEED),
}

def run(D,feats,model,nfold=5):
    ponds=sorted(D); rng=np.random.RandomState(SEED); perm=rng.permutation(ponds)
    folds=[list(perm[i::nfold]) for i in range(nfold)]
    test_scores={}; thr_used={}
    for k in range(nfold):
        te=folds[k]; va=folds[(k+1)%nfold]; tr=[p for p in ponds if p not in te+va]
        def XY(ps):
            X=[];Y=[]
            for p in ps:
                d=D[p]; m=d['elig']&~np.isnan(d['y'])
                X.append(d['F'][feats].values[m]); Y.append(d['y'][m])
            return np.vstack(X),np.concatenate(Y)
        def score(ps,clf):
            out={}
            for p in ps:
                d=D[p]; s=np.full(len(d['y']),np.nan); m=d['elig']
                s[m]=clf.predict_proba(d['F'][feats].values[m])[:,1]; out[p]=s
            return out
        X,Y=XY(tr); clf=MODELS[model]().fit(X,Y)
        vs=score(va,clf); thr=pick_thr(vs,{p:D[p] for p in va})
        # refit on train+val? keep train-only to match protocol
        ts=score(te,clf)
        for p in te: test_scores[p]=ts[p]; thr_used[p]=thr
    # apply per-fold threshold: evaluate per pond with its fold thr
    agg=None
    res=[]
    for p in ponds:
        m=op_metrics({p:test_scores[p]},thr_used[p],{p:D[p]}); m['pond']=p; res.append(m)
    R=pd.DataFrame(res)
    tot=dict(events=R.events.sum(),warned30=R.warned30.sum(),warned60=R.warned60.sum(),
             fa_per_day=(R.fa_per_day*R.pond_days).sum()/R.pond_days.sum(),
             occupancy=(R.occupancy*R.pond_days).sum()/R.pond_days.sum())
    return tot,R,test_scores,thr_used
