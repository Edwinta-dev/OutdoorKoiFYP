import json,datetime,collections,statistics as S,sys
r=json.load(open(sys.argv[1]))[2:]
cols='A B C D E F G H I J K L M N'.split()
names=['date','t','air','rh','wind','pa_mmhg','pa_kpa','rain','lbp','sbp','swp','tw_l','tw_s','tw_sw']
base=datetime.date(1899,12,30)
def f(v):
    try: return float(v)
    except: return None
days=collections.OrderedDict();bad=collections.Counter()
for x in r:
    d=base+datetime.timedelta(days=int(float(x['A'])))
    row={nm:f(x.get(c)) for c,nm in zip(cols,names)}
    for c,nm in zip(cols,names):
        if c not in x: bad[nm+':absent']+=1
        elif row[nm] is None: bad[nm+':'+x[c]]+=1
    days.setdefault(d,[]).append(row)
print(len(r),'rows',len(days),'days',dict(bad))
ds=list(days);blk=[];s=p=ds[0]
for d in ds[1:]:
    if (d-p).days!=1: blk.append((str(s),str(p)));s=d
    p=d
blk.append((str(s),str(p)));print(blk)
print(collections.Counter(len(v) for v in days.values()))
for nm in names[2:]:
    v=[q[nm] for l in days.values() for q in l if q[nm] is not None]
    print(nm,min(v),max(v),round(S.mean(v),2))
print('date n rain air_mean wind | drop(first-last) lbp,sbp,swp | steps>2mm | range')
for d,l in days.items():
    rain=sum(q['rain'] or 0 for q in l)
    def g(k):
        v=[q[k] for q in l if q[k] is not None]; return v
    ch=[round(g(k)[0]-g(k)[-1],2) if g(k) else None for k in ('lbp','sbp','swp')]
    nj=[sum(1 for a,b in zip(g(k),g(k)[1:]) if abs(b-a)>2) for k in ('lbp','sbp','swp')]
    rng=[(round(min(g(k))),round(max(g(k)))) for k in ('lbp','sbp','swp')]
    tw=g('tw_l'); 
    print(d,len(l),round(rain,1),round(S.mean(g('air')),1),round(S.mean(g('wind')),1),ch,nj,rng,'tw',round(min(tw),1),round(max(tw),1),'air',round(min(g('air')),1),round(max(g('air')),1))
