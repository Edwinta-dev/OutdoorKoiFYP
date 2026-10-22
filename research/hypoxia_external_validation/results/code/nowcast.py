from harness import *
from sklearn.metrics import roc_auc_score, average_precision_score
P=load_fwi(); D=prepare()
core=D[next(iter(D))]['core']; allf=list(D[next(iter(D))]['F'].columns)
ponds=sorted(D); perm=np.random.RandomState(SEED).permutation(ponds)
res={}
for H_ in [0,2,8]:
  for name,f in [('clock',['hs','hc','night']),('core',core),('all',allf)]:
   for mdl in ['logit','boost']:
    ys=[];ss=[];pp=[]
    for k in range(5):
        te=list(perm[k::5]); tr=[p for p in ponds if p not in te]
        def XY(ps):
            X=[];Y=[];G=[]
            for p in ps:
                d=D[p]; y=(P[p].do.shift(-H_)<THR).astype(float).where(P[p].do.shift(-H_).notna()).values
                m=d['elig']&~np.isnan(y); X.append(d['F'][f].values[m]); Y.append(y[m]); G+= [p]*m.sum()
            return np.vstack(X),np.concatenate(Y),G
        X,Y,_=XY(tr); clf=MODELS[mdl]().fit(X,Y)
        Xt,Yt,G=XY(te); ys.append(Yt); ss.append(clf.predict_proba(Xt)[:,1]); pp+=G
    y=np.concatenate(ys); s=np.concatenate(ss); G=np.array(pp)
    pa=[average_precision_score(y[G==p],s[G==p]) for p in ponds if 0<y[G==p].mean()<1]
    pr=[roc_auc_score(y[G==p],s[G==p]) for p in ponds if 0<y[G==p].mean()<1]
    print(f'h+{H_*15}min',name,mdl,'pooled AUC',round(roc_auc_score(y,s),3),'macro AP',round(np.mean(pa),3),'macro AUC',round(np.mean(pr),3),'prev',round(y.mean(),3))
