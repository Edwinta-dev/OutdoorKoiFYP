import pandas as pd, numpy as np, glob, os
import os
SRC=os.environ.get('FWI_SRC','/home/user/hx/src/Data-Campaign-Data/csv')
def load_fwi(strict=True):
    out={}
    for f in sorted(glob.glob(SRC+'/ara2_*.csv')):
        pid=os.path.basename(f)[:-4]
        d=pd.read_csv(f); d.columns=['t','do','ph','temp','qt','qdo','qph']
        d['t']=pd.to_datetime(d.t)
        for c in ['do','ph','temp']:
            d.loc[(d[c]<=0)|d[c].isna(),c]=np.nan
        d.loc[(d.ph<4)|(d.ph>11),'ph']=np.nan
        d.loc[(d.temp<10)|(d.temp>40),'temp']=np.nan
        if strict:
            d.loc[d.qdo.notna(),'do']=np.nan
            d.loc[d.qph.notna(),'ph']=np.nan
            cal = (d.t.dt.date==pd.Timestamp('2026-01-08').date())
            d.loc[cal,['do','ph','temp']]=np.nan
            if pid=='ara2_29660d32': d.loc[d.t>=pd.Timestamp('2025-12-29'),'ph']=np.nan
        d['bin']=d.t.dt.floor('15min')
        g=d.groupby('bin')[['do','ph','temp']].mean()
        idx=pd.date_range(g.index.min(),g.index.max(),freq='15min')
        out[pid]=g.reindex(idx)
    return out
