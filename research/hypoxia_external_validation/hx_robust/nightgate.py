from harness import *
from final import summarise
import os; OUT=os.environ['HX_OUT']
P=load_fwi(); D=prepare()
allf=list(D[next(iter(D))]['F'].columns); core=D[next(iter(D))]['core']
ponds=sorted(D); perm=np.random.RandomState(SEED).permutation(ponds); folds=[list(perm[i::5]) for i in range(5)]
onset=lambda p:D[p]['y']; low4=lambda p:(P[p].do.shift(-4)<THR).astype(float).where(P[p].do.shift(-4).notna()).values
specs={'Onset logistic, pond-relative':(onset,allf,'logit'),'Onset booster, pond-relative':(onset,allf,'boost'),
       'Low@+60 booster, pond-relative':(low4,allf,'boost')}
gates={'24h (no gate)':None,'night 18-07':(18,7),'night 20-07':(20,7),'night 22-07':(22,7)}
rows=[]
for name,(tf,f,mdl) in specs.items():
    raw={}
    for k in range(5):
        te=folds[k]; va=folds[(k+1)%5]; tr=[p for p in ponds if p not in te+va]
        X=[];Y=[]
        for p in tr:
            y=tf(p); m=D[p]['elig']&~np.isnan(y); X.append(D[p]['F'][f].values[m]); Y.append(y[m])
        clf=MODELS[mdl]().fit(np.vstack(X),np.concatenate(Y))
        for p in te+va if False else ponds:
            pass
        for p in set(te)|set(va):
            s=np.full(len(D[p]['y']),np.nan); m=D[p]['elig']; s[m]=clf.predict_proba(D[p]['F'][f].values[m])[:,1]; raw[(k,p)]=s
    for gname,g in gates.items():
        for k in range(5):
            te=folds[k]; va=folds[(k+1)%5]
            def gate(p,s):
                if g is None: return s
                h=D[p]['F'].index.hour; keep=(h>=g[0])|(h<g[1]); s=s.copy(); s[~keep]=0.0  # 0 = silent, still counted as monitored
                return s
            vs={p:gate(p,raw[(k,p)]) for p in va}; thr=pick_thr(vs,{p:D[p] for p in va},fa_max=0.177)
            for p in te:
                m=op_metrics({p:gate(p,raw[(k,p)])},thr,{p:D[p]}); m.update(model=f'{name} | {gname}',pond=p); rows.append(m)
R=pd.DataFrame(rows); T=summarise(R); T.to_csv(OUT+'/night_gate_summary.csv',index=False)
print(T[['model','warned30','pct_warned30','ci30','fa_per_pond_day','ci_fa','occupancy']].to_string())
# where do false alerts occur without gate?
