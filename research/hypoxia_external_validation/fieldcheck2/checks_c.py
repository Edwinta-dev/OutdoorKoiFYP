import pandas as pd,numpy as np,sys
R=sys.argv[1]
C=pd.read_csv(R+'/C_fwi/sheet.csv'); C=C.loc[:,~C.columns.str.startswith('Unnamed')]
C.columns=[c.strip() for c in C.columns]
C['t']=pd.to_datetime(C.iloc[:,0]+' '+C.iloc[:,1],format='%m-%d-%y %H:%M:%S',errors='coerce')
print('bad t',C.t.isna().sum(),C.t.min(),C.t.max(),'dups',C.duplicated(['Site Name','t']).sum())
print(C['Site Name'].value_counts().to_dict())
for c in ['Feed data','ABW','Ammonia mg/l','TDS mg/L','Temp °C','pH','Cond µS/cm','SpCond µS/cm']:
    C[c]=pd.to_numeric(C[c].replace('NA',np.nan),errors='coerce')
C['d']=C.t.dt.normalize()
print('feed rows/ day-site', C[C['Feed data'].notna()].groupby(['Site Name','d']).size().describe().round(1).to_dict())
print('feed',C['Feed data'].describe().round(2).to_dict(),'zero',(C['Feed data']==0).sum())
print('NH3 days per site',C[C['Ammonia mg/l'].notna()].groupby('Site Name').d.nunique().to_dict(),'NH3 >5',(C['Ammonia mg/l']>5).sum(),'neg',(C['Ammonia mg/l']<0).sum())
print('TDS days per site',C[C['TDS mg/L'].notna()].groupby('Site Name').d.nunique().to_dict())
print('TDS',C['TDS mg/L'].describe().round(0).to_dict(),'TDS/SpCond ratio',(C['TDS mg/L']/C['SpCond µS/cm']).describe().round(3)[['min','50%','max']].to_dict())
print('temp range',C['Temp °C'].min(),C['Temp °C'].max(),'pH range',C.pH.min(),C.pH.max())
# per site per-day span
print(C.groupby('Site Name').d.agg(['min','max','nunique']))
# TDS step candidates: daily median diffs
dm=C.groupby(['Site Name','d'])['TDS mg/L'].median().dropna().reset_index()
dm['dd']=dm.groupby('Site Name')['TDS mg/L'].diff(); dm['gap']=dm.groupby('Site Name').d.diff().dt.days
x=dm[(dm.gap==1)]; print('consec-day TDS pairs',len(x),'drops<-100',(x.dd<-100).sum(),'rises>100',(x.dd>100).sum())
print(C.iloc[:,4:8].drop_duplicates().head(6).to_string())
print('col list',list(C.columns))
