from harness import *
P=load_fwi(); D=prepare()
allf=list(D[next(iter(D))]['F'].columns); core=D[next(iter(D))]['core']
ponds=sorted(D); perm=np.random.RandomState(SEED).permutation(ponds)
def oof(target_fn,f,mdl):
    S={}
    for k in range(5):
        te=list(perm[k::5]); tr=[p for p in ponds if p not in te]
        X=[];Y=[]
        for p in tr:
            y=target_fn(p); m=D[p]['elig']&~np.isnan(y); X.append(D[p]['F'][f].values[m]); Y.append(y[m])
        clf=MODELS[mdl]().fit(np.vstack(X),np.concatenate(Y))
        for p in te:
            s=np.full(len(D[p]['y']),np.nan); m=D[p]['elig']; s[m]=clf.predict_proba(D[p]['F'][f].values[m])[:,1]; S[p]=s
    return S
def low_at(h): return lambda p: (P[p].do.shift(-h)<THR).astype(float).where(P[p].do.shift(-h).notna()).values
onset=lambda p: D[p]['y']
def curve(S,fas=(0.1,0.177,0.25)):
    allv=np.concatenate([v[~np.isnan(v)] for v in S.values()]); rows=[op_metrics(S,q,D) for q in np.quantile(allv,np.linspace(0.5,0.9995,160))]
    c=pd.DataFrame(rows); return [ (int(c[c.fa_per_day<=fa].warned30.max())) for fa in fas]
V={}
V['onset core boost']=oof(onset,core,'boost')
V['onset all logit']=oof(onset,allf,'logit')
V['onset all boost']=oof(onset,allf,'boost')
V['low@+60 all boost']=oof(low_at(4),allf,'boost')
V['low@+120 all boost']=oof(low_at(8),allf,'boost')
a=oof(low_at(8),allf,'boost'); b=oof(low_at(0),allf,'boost')
V['rise: P(+120)-P(now)']={p:a[p]-b[p] for p in a}
V['P(+120)*(1-P(now))']={p:a[p]*(1-b[p]) for p in a}
import pickle; pickle.dump(V,open('variants.pkl','wb'))
for k,S in V.items(): print(k, 'warned30 of 374 at FA<=0.1/0.177/0.25:',curve(S))
