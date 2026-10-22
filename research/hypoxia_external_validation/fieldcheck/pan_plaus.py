import json,sys,datetime as dt,collections,statistics as S
sys.path.insert(0,r'C:\Users\AgenticDev\OutdoorKoiFYP_research\Backend')
from koi.models.evaporation_engine import evaporation_mm_per_day, fit_water_air_temperature
r=json.load(open(sys.argv[1]))[2:]
base=dt.datetime(1899,12,30)
def f(v):
    try:return float(v)
    except:return None
rec=[]
for x in r:
    t=base+dt.timedelta(days=float(x['A'])+float(x['B']))
    t=t.replace(microsecond=0)
    t=min([t],key=lambda a:a)
    rec.append(dict(t=dt.datetime(t.year,t.month,t.day)+dt.timedelta(minutes=round((t-dt.datetime(t.year,t.month,t.day)).total_seconds()/600)*10),
        air=f(x.get('C')),rh=f(x.get('D')),u=f(x.get('E')),swp=f(x.get('K')),tw=f(x.get('N')),twl=f(x.get('L')),sbp=f(x.get('J')),lbp=f(x.get('I'))))
bad=collections.Counter()
for q in rec:
    if q['air'] is None: bad['air:'+str(q['t'].date())]+=1
    if q['rh'] is None: bad['rh:'+str(q['t'].date())]+=1
print(dict(bad))
# day-boundary continuity of SWP: gap between last of day and first of next consecutive day
byday=collections.OrderedDict()
for q in rec: byday.setdefault(q['t'].date(),[]).append(q)
ds=list(byday);
print('consecutive-day boundary SWP step (mm):',[ (str(a),round(byday[b][0]['swp']-byday[a][-1]['swp'],2)) for a,b in zip(ds,ds[1:]) if (b-a).days==1])
# SWP daily drop vs midnight-to-midnight
drops=[];pred=[];rows=[]
for d,l in byday.items():
    ok=[q for q in l if None not in (q['air'],q['rh'],q['u'],q['tw'])]
    if len(ok)<100: continue
    drop=l[0]['swp']-l[-1]['swp']
    # mean of 10-min instantaneous Penman
    for sh in (1.0,0.6):
        e=S.mean(evaporation_mm_per_day(q['tw'],q['air'],q['rh'],q['u'],wind_measured_at_10m=False,shelter_factor=sh) for q in ok)
        if sh==1.0:e1=e
        else:e06=e
    rows.append((d,drop,e1,e06))
print('date SWPdrop  Penman(sf1.0)  Penman(sf0.6)')
for d,a,b,c in rows:print(d,round(a,2),round(b,2),round(c,2))
dr=[a for _,a,_,_ in rows];p1=[b for _,_,b,_ in rows];p6=[c for *_,c in rows]
n=len(dr)
for nm,p in (('sf1.0',p1),('sf0.6',p6)):
    err=[x-y for x,y in zip(p,dr)]
    print(nm,'n',n,'bias',round(S.mean(err),2),'MAE',round(S.mean(abs(e) for e in err),2),'RMSE',round((S.mean(e*e for e in err))**.5,2),'corr',round(S.correlation(p,dr),2))
print('persistence(const mean) MAE',round(S.mean(abs(x-S.mean(dr)) for x in dr),2),'obs mean',round(S.mean(dr),2),'min',round(min(dr),2),'max',round(max(dr),2))
# diurnal: mean water temp by hour (SWP pan) and air
hr=collections.defaultdict(list);ha=collections.defaultdict(list)
for q in rec:
    if q['tw'] is not None: hr[q['t'].hour].append(q['tw'])
    if q['air'] is not None: ha[q['t'].hour].append(q['air'])
m={h:S.mean(v) for h,v in hr.items()};a={h:S.mean(v) for h,v in ha.items()}
pk=max(m,key=m.get);tr=min(m,key=m.get);pka=max(a,key=a.get)
print('water SWP hour mean: min h',tr,round(m[tr],1),'max h',pk,round(m[pk],1),'| air peak h',pka,round(a[pka],1),'min',round(min(a.values()),1))
# fit_water_air_temperature per block (hourly means)
def hourly(key):
    acc=collections.defaultdict(list)
    for q in rec:
        if q[key] is not None: acc[q['t'].replace(minute=0)].append(q[key])
    return {h:S.mean(v) for h,v in acc.items() if len(v)>=5}
for key in ('tw','twl'):
    W=hourly(key);A=hourly('air')
    res=fit_water_air_temperature(W,A)
    print(key,'fit all-40d (pooled, gaps):',res['status'],res['value'],res['lag_hours'],res['error'],res['details'])
