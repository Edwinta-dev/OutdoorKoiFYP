from robust import *
fz=json.load(open('frozen.json')); FT=fz['frozen_numeric_threshold_median']
rows=[]; keep={}
P,D,allf,core=setup(3.0); ponds=sorted(D); T=targets(P,D)
for seed in [20261022,20261023,20261024,20261025]:
    folds=folds_for(ponds,seed)
    raw=fit_scores(D,ponds,folds,T['low4'],allf,'boost')
    for gname,g,mode in [('22-06 reselect (frozen rule)',GATE,'reselect'),('22-06 fixed thr (frozen number)',GATE,'fixed'),('no gate reselect',None,'reselect')]:
        R,th=evaluate(D,ponds,folds,raw,g,mode,FT); d=summ(R,gname); d.update(seed=seed,label=3.0,thr=[round(x,3) for x in th]); rows.append(d)
        R.to_csv(f'{OUT}/perpond_s{seed}_{gname[:5].strip().replace(" ","_")}_{mode}.csv',index=False)
        if seed==20261022 and mode=='reselect' and g: keep['raw']=raw; keep['folds']=folds; keep['thr']=th
pd.DataFrame(rows).to_csv(OUT+'/step2_seeds.csv',index=False); print(pd.DataFrame(rows).drop(columns=['model']).to_string())
pickle.dump(keep,open(OUT+'/seed22_gated_scores.pkl','wb'))
# ---- step 3 severity (labels only; seed 22; same procedure) ----
rows=[]
for lab in [3.0,2.0]:
    P,D,allf,core=setup(lab); T=targets(P,D); folds=folds_for(ponds,20261022)
    nev=sum(len(D[p]['onsets']) for p in ponds)
    for nm,tf,mdl,g in [('Onset logistic, no gate',T['onset'],'logit',None),('Low@+60 booster, 22-06 gate',T['low4'],'boost',GATE)]:
        raw=fit_scores(D,ponds,folds,tf,allf,mdl); R,th=evaluate(D,ponds,folds,raw,g,'reselect')
        d=summ(R,nm); d.update(label=lab); rows.append(d)
    # night clock rule 18-06 and legacy engine
    for nm,fn in [('Night clock rule 18-06',lambda F,e:np.where(e,F.night.values,np.nan)),
                  ('Legacy hypoxia.py (night & T>=30)',lambda F,e:np.where(e,(F.night.values==1)&(F.temp.values>=30),np.nan).astype(float))]:
        R=pd.DataFrame([dict(op_metrics({p:fn(D[p]['F'],D[p]['elig'])},0.5,{p:D[p]}),pond=p) for p in ponds])
        d=summ(R,nm); d.update(label=lab); rows.append(d)
    # prevalence context
    print('label',lab,'scored onsets',nev,'low-bin prevalence',np.nanmean(np.concatenate([(P[p].do<lab).where(P[p].do.notna()).values for p in ponds])))
S=pd.DataFrame(rows); S.to_csv(OUT+'/step3_severity.csv',index=False); print(S.drop(columns=['pond_days']).to_string())
