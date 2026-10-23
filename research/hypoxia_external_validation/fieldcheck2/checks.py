import pandas as pd, numpy as np, sys, os
R=sys.argv[1]
A=R+'/A_tyty/'
p=pd.read_csv(A+'Table5_TyTyCooperatorFarm_InOut_Pumping_Data_2022.csv',encoding='utf-8-sig')
p['Date']=pd.to_datetime(p['Date']); print('PUMP',len(p),p.Date.min(),p.Date.max(),p['Pump ID'].unique(),p.In_Out.unique())
print(p.groupby(['In_Out','Pump ID']).Amt_acin.agg(['count','sum',lambda s:(s>0).sum()]))
print('dups date/pump',p.duplicated(['Date','Pump ID']).sum(),'neg',(p.Amt_acin<0).sum(),'nulls',p.Amt_acin.isna().sum())
g=pd.read_csv(A+'Table7_TyTyCooperatorFarm_Staff_Gauge_Pressure_Readings_2021_2023.csv',encoding='utf-8-sig')
g.columns=['t','rec','lvl']; g['t']=pd.to_datetime(g.t); g['lvl']=pd.to_numeric(g.lvl,errors='coerce')
print('GAUGE rows',len(g),'nonnull',g.lvl.notna().sum(),g.t.min(),g.t.max())
v=g.dropna(); print('valid span',v.t.min(),v.t.max(),v.lvl.describe().round(2).to_dict())
d=v.set_index('t').lvl.resample('D').agg(['count','median']); full=pd.date_range(v.t.min().normalize(),v.t.max().normalize())
print('days valid',(d['count']>0).sum(),'of',len(full),'; days <200/288 samples',((d['count']>0)&(d['count']<200)).sum())
dt=v.t.diff().dt.total_seconds()/60; print('gaps>60min',(dt>60).sum(),'max gap h',round(dt.max()/60,1),'dups',v.t.duplicated().sum())
print('level jumps >0.3ft/5min',(v.lvl.diff().abs()>0.3).sum())
y=d['median'].dropna(); y22=y['2022']; print('2022 level days',len(y22),'range ft',y22.min(),y22.max())
# separability: night-time (00-05 local) level decline on days with no pumping and no rain
rain=pd.read_csv(A+'Table8_TyTyCooperatorFarm_Total_Precipitation_Rainfall_2022.csv')
rain['t']=pd.to_datetime(rain.year.astype(str)+'-01-01')+pd.to_timedelta(rain.DOY-1,unit='D'); rain['day']=rain.t
rd=rain.groupby('day').rainfall_inches.sum(); print('RAIN rows',len(rain),'days with rain',len(rd),'total in',round(rd.sum(),2),'max min-val',rain.rainfall_inches.max(),'HHMM bad',((rain.HHMM%100>59)|(rain.HHMM>2359)).sum())
pd_=p.groupby('Date').apply(lambda x:(x.Amt_acin*np.where(x.In_Out=='I',1,-1)).sum())
dd=pd.DataFrame({'lvl':y22}).join(rd.rename('rain_in')).join(pd_.rename('pump_net_acin')); dd['rain_in']=dd.rain_in.fillna(0); dd['pump_net_acin']=dd.pump_net_acin.fillna(0)
dd['dlvl_in']=dd.lvl.diff()*12
clean=dd[(dd.rain_in==0)&(dd.pump_net_acin==0)&dd.dlvl_in.notna()&(dd.index.to_series().diff().dt.days==1)]
print('2022 days',len(dd),'clean (no rain no pump, consecutive)',len(clean)); print('clean dlvl in/day: median',clean.dlvl_in.median().round(3),'q10,q90',clean.dlvl_in.quantile([.1,.9]).round(3).tolist())
print('by month clean median in/day',(clean.dlvl_in.groupby(clean.index.month).median().round(3)).to_dict())
dd.to_csv(R+'/../fieldcheck2/tyty_daily.csv')
# B
B=pd.read_csv(R+'/B_tn/pan.csv'); B.columns=['code','name','agency','state','dist','lat','lon','t','mm']
B['t']=pd.to_datetime(B.t,format='%d-%m-%Y %H:%M',errors='coerce'); B['mm']=pd.to_numeric(B.mm,errors='coerce')
print('PAN rows',len(B),'stations',B.code.nunique(),'t',B.t.min(),B.t.max(),'bad t',B.t.isna().sum(),'mm null',B.mm.isna().sum(),'neg',(B.mm<0).sum(),'>25',(B.mm>25).sum(),'dups',B.duplicated(['code','t']).sum())
print(B.mm.describe().round(2).to_dict())
B['yr']=B.t.dt.year; print(B.groupby('yr').agg(n=('mm','size'),st=('code','nunique')).T.to_string())
am=B[B.t.dt.hour<12]; print('AM obs/stn-day coverage: stations with >=2y of >300 daily AM', (am.groupby('code').t.apply(lambda s:s.dt.normalize().nunique())>700).sum())
print('zero share',(B.mm==0).mean().round(3),'PM zero share',(B[B.t.dt.hour>=12].mm==0).mean().round(3))
# C
C=pd.read_csv(R+'/C_fwi/sheet.csv'); C=C.loc[:,~C.columns.str.startswith('Unnamed')]
print('FWI shape',C.shape, 'sites',C['Site Name'].nunique(),'dates',C.iloc[:,0].min(),C.iloc[:,0].max())
for c in ['Feed data','ABW','Ammonia mg/l','Temp °C','TDS mg/L','Cond µS/cm','Rainfall']:
    s=C[c].replace('NA',np.nan); print(c,'nonnull',s.notna().sum(),'/',len(C),'uniq',s.nunique(), s.dropna().astype(str).unique()[:6].tolist())
