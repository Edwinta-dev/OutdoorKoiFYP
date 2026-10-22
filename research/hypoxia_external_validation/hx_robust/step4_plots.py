from robust import *
import harness as H
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as md
P,D,allf,core=setup(3.0); S,TH=pickle.load(open(OUT+'/seed22_test_scores.pkl','rb'))
E=pd.read_csv(OUT+'/events_seed22.csv',parse_dates=['time'])
def pick(p,kind,n=0):
    e=E[(E.pond==p)&(E.kind.astype(str)==kind)].sort_values('time'); return e.iloc[n].time
cases=[('A','ara2_0677080b',pd.Timestamp('2025-12-15 00:00'),'ara2_0677080b 14-16 Dec: first-day warm-up gap, then flat pH'),
 ('B','ara2_0f143c64',None,'ara2_0f143c64: false-alert cluster'),
 ('C','ara2_3eab831e',None,'ara2_3eab831e: onsets never warned'),
 ('D','ara2_858b914c',None,'ara2_858b914c: a warned onset')]
sel={'B':('FA',3),'C':('False',5),'D':('True',2)}
out=[]
for cid,p,t0,title in cases:
    if t0 is None:
        kind,n=sel[cid]; t0=pick(p,kind,n).normalize()
    g=P[p]; F=D[p]['F']; s=pd.Series(S[p],index=g.index); th=TH[p]
    a,b=t0-pd.Timedelta('12h'),t0+pd.Timedelta('2D')-pd.Timedelta('12h')+pd.Timedelta('12h')
    w=g[a:b]; sw=s[a:b]
    pos=np.where(np.isnan(S[p]),0,(S[p]>=th).astype(int)); st,act=H.alerts(pos); st=g.index[st]
    st=[x for x in st if a<=x<=b]; ons=[g.index[o] for o in D[p]['onsets'] if a<=g.index[o]<=b]
    fig,ax=plt.subplots(4,1,figsize=(10,8),sharex=True)
    ax[0].plot(w.index,w.do,'k'); ax[0].axhline(3,color='r',ls='--',lw=.8); ax[0].set_ylabel('DO mg/L (label only)')
    ax[1].plot(w.index,w.ph,'tab:green'); ax[1].set_ylabel('pH')
    ax[2].plot(w.index,w.temp,'tab:orange'); ax[2].set_ylabel('temp C')
    ax[3].plot(sw.index,sw.values,'tab:blue',drawstyle='steps-post'); ax[3].axhline(th,color='gray',ls=':'); ax[3].set_ylabel('alert score (gated)')
    for x in ax:
        for d in pd.date_range(a.normalize()-pd.Timedelta('1D'),b.normalize(),freq='D'):
            x.axvspan(d-pd.Timedelta('2h'),d+pd.Timedelta('6h'),color='navy',alpha=.07,lw=0)  # gate 22-06
        for o in ons: x.axvline(o,color='r',alpha=.6)
        for q in st: x.axvline(q,color='tab:purple',ls='--',alpha=.8)
    ax[3].xaxis.set_major_formatter(md.DateFormatter('%d %b %H:%M')); ax[0].set_xlim(a,b)
    fig.suptitle(title+'\nred = scored onset, purple dashed = alert start, shaded = 22-06 gate, thr=%.2f'%th,fontsize=9)
    fig.tight_layout(); fn=f'{OUT}/failure_{cid}_{p[-8:]}.png'; fig.savefig(fn,dpi=110); plt.close(fig)
    out.append((cid,p,str(t0),len(ons),len(st),[str(o) for o in ons],[str(q) for q in st]))
for o in out: print(o)
