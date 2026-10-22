from harness import *
from sklearn.metrics import roc_auc_score, average_precision_score
import pickle, json
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
import os; OUT=os.environ.get('HX_OUT','/home/user/hx/results'); os.makedirs(OUT,exist_ok=True)
def evaluate(strict=True, fa_budget=0.177):
    P=load_fwi(strict); D=prepare(strict)
    allf=list(D[next(iter(D))]['F'].columns); core=D[next(iter(D))]['core']
    rel=[c for c in allf if c not in core]
    ponds=sorted(D); perm=np.random.RandomState(SEED).permutation(ponds)
    folds=[list(perm[i::5]) for i in range(5)]
    low_at=lambda h:(lambda p:(P[p].do.shift(-h)<THR).astype(float).where(P[p].do.shift(-h).notna()).values)
    onset=lambda p:D[p]['y']
    specs={'B5 tree booster, core features (prior-report style)':(onset,core,'boost'),
           'Onset logistic, core + pond-relative':(onset,allf,'logit'),
           'Onset booster, core + pond-relative':(onset,allf,'boost'),
           'Low-DO@+60min booster, core + pond-relative':(low_at(4),allf,'boost'),
           'Low-DO@+60min booster, clock + pond-relative only':(low_at(4),['hs','hc','night']+rel,'boost')}
    perpond=[]; S_all={}
    for name,(tf,f,mdl) in specs.items():
        S={}; TH={}
        for k in range(5):
            te=folds[k]; va=folds[(k+1)%5]; tr=[p for p in ponds if p not in te+va]
            X=[];Y=[]
            for p in tr:
                y=tf(p); m=D[p]['elig']&~np.isnan(y); X.append(D[p]['F'][f].values[m]); Y.append(y[m])
            clf=MODELS[mdl]().fit(np.vstack(X),np.concatenate(Y))
            def sc(p):
                s=np.full(len(D[p]['y']),np.nan); m=D[p]['elig']; s[m]=clf.predict_proba(D[p]['F'][f].values[m])[:,1]; return s
            vs={p:sc(p) for p in va}; thr=pick_thr(vs,{p:D[p] for p in va},fa_max=fa_budget)
            for p in te: S[p]=sc(p); TH[p]=thr
        S_all[name]=S
        for p in ponds:
            m=op_metrics({p:S[p]},TH[p],{p:D[p]}); m.update(model=name,pond=p); perpond.append(m)
    # rule baselines
    for name in ['Night-only clock rule (18:00-06:00)','Original hypoxia.py, clock-proxy lux']:
        for p in ponds:
            F=D[p]['F']; s=np.where(D[p]['elig'],F.night.values,np.nan)
            if 'Original' in name: s=np.where(D[p]['elig'],(F.night.values==1)&(F.temp.values>=30),np.nan).astype(float)
            m=op_metrics({p:s},0.5,{p:D[p]}); m.update(model=name,pond=p); perpond.append(m)
    R=pd.DataFrame(perpond)
    return R,S_all,D
def summarise(R,nb=2000):
    rng=np.random.RandomState(SEED); rows=[]
    for name,g in R.groupby('model',sort=False):
        g=g.reset_index(drop=True)
        def stat(gg):
            return gg.warned30.sum()/gg.events.sum(), (gg.fa_per_day*gg.pond_days).sum()/gg.pond_days.sum()
        w,f=stat(g); bs=[stat(g.iloc[rng.randint(0,len(g),len(g))]) for _ in range(nb)]; bs=np.array(bs)
        rows.append(dict(model=name,events=int(g.events.sum()),warned30=int(g.warned30.sum()),warned60=int(g.warned60.sum()),
            pct_warned30=round(100*w,1),ci30=f'{100*np.nanpercentile(bs[:,0],2.5):.1f}-{100*np.nanpercentile(bs[:,0],97.5):.1f}',
            fa_per_pond_day=round(f,3),ci_fa=f'{np.nanpercentile(bs[:,1],2.5):.3f}-{np.nanpercentile(bs[:,1],97.5):.3f}',
            occupancy=round((g.occupancy*g.pond_days).sum()/g.pond_days.sum(),3),pond_days=round(g.pond_days.sum(),1),
            ponds_with_any_warning=int((g.warned30>0).sum())))
    return pd.DataFrame(rows)
if __name__=='__main__':
    R,S,D=evaluate(True); R.to_csv(OUT+'/onset_per_pond_strict.csv',index=False)
    T=summarise(R); T.to_csv(OUT+'/onset_summary_strict.csv',index=False); print(T.to_string())
    pickle.dump(S,open(OUT+'/onset_scores_strict.pkl','wb'))
    # paired difference best-vs-prior style
    a='B5 tree booster, core features (prior-report style)'
    rng=np.random.RandomState(SEED); ps=sorted(R.pond.unique())
    for b in [m for m in R.model.unique() if 'pond-relative' in m]:
        A=R[R.model==a].set_index('pond'); B=R[R.model==b].set_index('pond'); d=[]
        for _ in range(5000):
            s=rng.choice(ps,len(ps)); d.append(B.loc[s].warned30.sum()/B.loc[s].events.sum()-A.loc[s].warned30.sum()/A.loc[s].events.sum())
        print('paired diff',b,'minus prior-style: %.1f pp (95%% CI %.1f to %.1f)'%(100*np.mean(d),*np.percentile(np.array(d)*100,[2.5,97.5])))
    R2,_,_=evaluate(False); T2=summarise(R2); T2.to_csv(OUT+'/onset_summary_relaxedQC.csv',index=False); print('RELAXED QC'); print(T2.to_string())
