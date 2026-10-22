from harness import *
from sklearn.metrics import roc_auc_score, average_precision_score
P=load_fwi(); D=prepare()
rows=[]
for p,d in D.items():
    F=d['F']; do=P[p].do
    for day in pd.date_range(F.index[0].normalize(),F.index[-1].normalize()):
        t0=day+pd.Timedelta('19h')
        nt=do[t0+pd.Timedelta('15min'):day+pd.Timedelta('1D 9h')]
        if t0 not in F.index or nt.notna().mean()<0.8: continue
        x=F.loc[t0]
        if x[['ph','temp','ph_m2h']].isna().any(): continue
        lowmask=(nt<3).rolling(2).sum()>=2
        first=lowmask.idxmax() if lowmask.any() else None
        r=dict(p=p,day=day,y=int(lowmask.any()),y2=int(((nt<2).rolling(2).sum()>=2).any()),
               lead_h=(first-t0).total_seconds()/3600 if first is not None else np.nan)
        r.update(x.to_dict()); rows.append(r)
N=pd.DataFrame(rows); N.to_csv('night_table.csv',index=False)
feats=[c for c in D[p]['F'].columns if c not in ('hs','hc','night','h2dawn','ph_proj_dawn')]
core=['ph','temp','dph30','dph120','dtp30','dtp120','ph_m2h','ph_s2h','tp_m2h','tp_s2h']
print('nights',len(N),'base',N.y.mean().round(3),'median first-low hours after 19:00',N.lead_h.median())
ponds=sorted(N.p.unique()); perm=np.random.RandomState(SEED).permutation(ponds)
for tgt in ['y','y2']:
  for name,f in [('core',core),('pondrel',feats)]:
    oof=np.full(len(N),np.nan)
    for k in range(5):
        te=perm[k::5]; tr=N.p.isin(te)==False
        clf=MODELS['logit']().fit(N.loc[tr,f],N.loc[tr,tgt])
        m=N.p.isin(te); oof[m]=clf.predict_proba(N.loc[m,f])[:,1]
    N['s_'+name+tgt]=oof
    pa=[roc_auc_score(g[tgt],g['s_'+name+tgt]) for _,g in N.groupby('p') if g[tgt].nunique()==2]
    print(tgt,name,'pooledAUC',round(roc_auc_score(N[tgt],oof),3),'AP',round(average_precision_score(N[tgt],oof),3),'base',round(N[tgt].mean(),3),'median within-pond AUC',round(np.median(pa),3),len(pa))
N.to_csv('night_table.csv',index=False)
