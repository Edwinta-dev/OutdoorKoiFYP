from harness import *
import pickle
D=prepare()
core=D[next(iter(D))]['core']; allf=list(D[next(iter(D))]['F'].columns)
out={}
for name,f in [('core',core),('all',allf)]:
  for m in MODELS:
    t,R,S,T=run(D,f,m); out[(name,m)]=S
pickle.dump((out),open('oof.pkl','wb'))
def curve(S):
    allv=np.concatenate([v[~np.isnan(v)] for v in S.values()]); rows=[]
    for q in np.quantile(allv,np.linspace(0.7,0.999,120)):
        rows.append(op_metrics(S,q,D))
    return pd.DataFrame(rows)
for k,S in out.items():
    c=curve(S)
    for fa in [0.1,0.177,0.25,0.5]:
        r=c[c.fa_per_day<=fa].sort_values('warned30').iloc[-1]
        print(k,fa,int(r.warned30),int(r.events),round(r.warned30/r.events*100,1),round(r.fa_per_day,3),round(r.occupancy,3))
# temps
print('max temp',max(D[p]['F'].temp.max() for p in D), 'frac>=30',np.mean(np.concatenate([D[p]['F'].temp.dropna().values>=30 for p in D])))
