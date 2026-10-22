from robust import *
import harness as H
P,D,allf,core=setup(3.0); K=pickle.load(open(OUT+'/seed22_gated_scores.pkl','rb'))
ponds=sorted(D); fold_of={p:k for k in range(5) for p in K['folds'][k]}
S={};TH={}
for p in ponds:
    k=fold_of[p]; S[p]=gate_fn(D[p]['F'],K['raw'][(k,p)],GATE); TH[p]=K['thr'][k]
pickle.dump((S,TH),open(OUT+'/seed22_test_scores.pkl','wb'))
rows=[]
for p in ponds:
    s=S[p]; pos=np.where(np.isnan(s),0,(s>=TH[p]).astype(int)); st,act=H.alerts(pos); st=np.array(st,int); idx=D[p]['F'].index
    for o in D[p]['onsets']:
        c=st[(st>=o-8)&(st<=o-2)]; w=len(c)>0 and not act[max(o-9,0)]
        rows.append((p,idx[o],w,float(np.nanmax(s[max(o-8,0):o+1])) if not np.isnan(s[max(o-8,0):o+1]).all() else np.nan))
    for a in st:
        if D[p]['lowfut'][a]==0: rows.append((p,idx[a],'FA',s[a]))
E=pd.DataFrame(rows,columns=['pond','time','kind','score']); E.to_csv(OUT+'/events_seed22.csv',index=False)
print(E[E.pond=='ara2_0677080b'].to_string())
print(E.groupby(['pond']).apply(lambda g: pd.Series(dict(onsets=(g.kind.isin([True,False])).sum(),warned=(g.kind==True).sum(),FA=(g.kind=='FA').sum()))).to_string())
