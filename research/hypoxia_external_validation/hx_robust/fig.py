from harness import *
import pickle, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
D=prepare(); S=pickle.load(open('/home/user/hx/results/onset_scores_strict.pkl','rb'))
fig,ax=plt.subplots(figsize=(7,4.5))
for name,s in S.items():
    allv=np.concatenate([v[~np.isnan(v)] for v in s.values()])
    c=pd.DataFrame([op_metrics(s,q,D) for q in np.quantile(allv,np.linspace(0.5,0.9995,150))]).sort_values('fa_per_day')
    c['best']=c.warned30.cummax(); ax.step(c.fa_per_day,100*c.best/c.events,where='post',label=name)
ax.scatter([0.177],[4.5],marker='x',c='k',s=60,label='Prior report: booster 14/310 @ 0.177',zorder=5)
ax.scatter([1.086],[12.8],marker='o',c='grey',label='Night-only rule',zorder=5)
ax.set_xlim(0,1.2); ax.set_xlabel('False confirmed alerts per observed pond-day'); ax.set_ylabel('% onsets warned ≥30 min ahead')
ax.set_title('FWI unseen ponds: warning yield vs alarm burden (pooled OOF sweep)'); ax.legend(fontsize=7); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig('/home/user/hx/results/fig_warning_vs_fa.png',dpi=150)
