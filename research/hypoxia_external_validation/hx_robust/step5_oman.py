# Oman: false-alarm stress ONLY. Frozen model trained on all 17 FWI ponds (no Oman data used for fitting). DO not read. No recall.
from robust import *
import harness as H
OM=os.environ['OMAN_SRC']
fz=json.load(open('frozen.json')); FT=fz['frozen_numeric_threshold_median']
P,D,allf,core=setup(3.0); T=targets(P,D); ponds=sorted(D)
X=[];Y=[]
for p in ponds:
    y=T['low4'](p); m=D[p]['elig']&~np.isnan(y); X.append(D[p]['F'][allf].values[m]); Y.append(y[m])
clf=MODELS['boost']().fit(np.vstack(X),np.concatenate(Y))
rows=[]
for nm,f in [('oman_open_pond','/raw/offline/raw_readings.csv'),('oman_live_tank','/raw/live/raw_readings_new.csv')]:
    d=pd.read_csv(OM+f,usecols=['timestamp','temp_c','ph'],parse_dates=['timestamp']).rename(columns={'timestamp':'t','temp_c':'temp'})
    d.loc[(d.ph<4)|(d.ph>11),'ph']=np.nan; d.loc[(d.temp<10)|(d.temp>40),'temp']=np.nan
    g=d.groupby(d.t.dt.floor('15min'))[['ph','temp']].mean(); g=g.reindex(pd.date_range(g.index.min(),g.index.max(),freq='15min'))
    F,_,el=features(g) if False else H.features(g)[0:3]
    # H.features needs g.do? it only uses ph,temp
    s=np.full(len(g),np.nan); m=el.values; s[m]=clf.predict_proba(F[allf].values[m])[:,1]
    for gn,gg in [('22-06 gate',GATE),('no gate',None)]:
        sc=gate_fn(F,s,gg); pos=np.where(np.isnan(sc),0,(sc>=FT).astype(int)); st,act=H.alerts(pos)
        days=(~np.isnan(sc)).sum()/96
        idx=F.index[np.array(st,int)] if st else []
        rows.append(dict(dataset=nm,gate=gn,span=f'{g.index.min():%Y-%m-%d}..{g.index.max():%Y-%m-%d}',bins_total=len(g),bins_ph_temp_present=int(g[['ph','temp']].notna().all(1).sum()),
            eligible_bins=int((~np.isnan(s)).sum()),monitored_days=round(days,2),alerts=len(st),alerts_per_day=round(len(st)/days,3) if days else None,
            occupancy=round(float(act[~np.isnan(sc)].mean()),3) if days else None,alert_hours=sorted(set(int(i.hour) for i in idx)),alert_dates=sorted(set(str(i.date()) for i in idx))[:12]))
R=pd.DataFrame(rows); R.to_csv(OUT+'/step5_oman.csv',index=False); print(R.to_string())
print('FWI reference (frozen thr, gated, seed22 test): FA/pond-day from step2 = see step2_seeds.csv row 1; all-alert rate not defined here')
