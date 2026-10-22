"""WP3b exploratory evaporation alternatives.
python -X utf8 wp3b/run_wp3b.py src/pan/rows.json wp3b
Engine imported read-only. Seed 20261022. Leave-one-run-out; run-level bootstrap."""
import json, sys, csv, itertools, datetime as dt, collections, statistics as S
import numpy as np
sys.path.insert(0, r'C:\Users\AgenticDev\OutdoorKoiFYP_research\Backend')
from koi.models import evaporation_engine as E
SEED = 20261022
NB = 5000
rows_path, out = sys.argv[1], sys.argv[2]
raw = json.load(open(rows_path))[2:]
base = dt.datetime(1899, 12, 30)


def f(v):
    try:
        return float(v)
    except Exception:
        return None


rec = [dict(t=base + dt.timedelta(minutes=round((float(x['A']) + float(x['B'])) * 1440)), air=f(x.get('C')),
            rh=f(x.get('D')), u=f(x.get('E')), swp=f(x.get('K')), tw=f(x.get('N'))) for x in raw]
byday = collections.OrderedDict()
for q in rec:
    byday.setdefault(q['t'].date(), []).append(q)
days = list(byday)
runs = []
run_of = {}
for d in days:
    if runs and (d - runs[-1][-1]).days == 1:
        runs[-1].append(d)
    else:
        runs.append([d])
for i, r in enumerate(runs, 1):
    for d in r:
        run_of[d] = i
assert len(runs) == 12
es = E.saturation_vapour_pressure_mmhg
D = {}
for d in days:
    l = byday[d]
    n = len(l)
    ok = n == 144 and all(q[k] is not None for q in l for k in ('air', 'rh', 'u', 'tw', 'swp'))
    drop = (l[0]['swp'] - l[-1]['swp']) if all(q['swp'] is not None for q in l) and n == 144 else None
    r = dict(date=d, run=run_of[d], drop=drop, ok=ok)
    if ok:
        dep = lambda q: es(q['tw']) - es(q['air']) * q['rh'] / 100
        r['dep'] = S.mean(dep(q) for q in l)  # mmHg water-surface deficit
        r['vpd'] = S.mean(es(q['air']) * (1 - q['rh'] / 100) for q in l)
        r['u'] = S.mean(q['u'] for q in l)
        r['ud'] = S.mean(q['u'] * dep(q) for q in l)
        r['tw'] = S.mean(q['tw'] for q in l)
        r['ta'] = S.mean(q['air'] for q in l)
        r['sf1'] = S.mean(E.evaporation_mm_per_day(q['tw'], q['air'], q['rh'], q['u'], wind_measured_at_10m=False, shelter_factor=1.0) for q in l)
        r['sf6'] = S.mean(E.evaporation_mm_per_day(q['tw'], q['air'], q['rh'], q['u'], wind_measured_at_10m=False, shelter_factor=0.6) for q in l)
        # Penman open-water aerodynamic term: 0.35(1+0.0098*u[miles/day]) (e in mmHg); 0.0098*86400/1609.344 = 0.5261
        r['fix'] = S.mean(max(0.35 * (1 + 0.5261 * q['u']) * dep(q), 0) for q in l)
        r['fix6'] = S.mean(max(0.35 * (1 + 0.5261 * 0.6 * q['u']) * dep(q), 0) for q in l)
    D[d] = r
sc = [r for r in D.values() if r['ok'] and r['drop'] is not None]
assert len(sc) == 35 and len({r['run'] for r in sc}) == 10, len(sc)
for r in sc:
    r['mean'] = S.mean(x['drop'] for x in sc if x['run'] != r['run'])
    p = D.get(r['date'] - dt.timedelta(days=1))
    r['persist'] = p['drop'] if p and p['run'] == r['run'] and p['drop'] is not None else None


def obs(rs):
    return np.array([r['drop'] for r in rs])


def ols(X, y):
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    return b


params = collections.defaultdict(list)
chosen = []
PRED = ['vpd', 'u', 'tw', 'ta', 'dep']


def fitpred(tr, te, kind):
    y = obs(tr)
    if kind == 'k':
        x = np.array([r['sf1'] for r in tr])
        k = float(x @ y / (x @ x))
        params['k'].append(k)
        return [k * r['sf1'] for r in te]
    if kind == 'dalton':
        X = np.array([[r['dep'], r['ud']] for r in tr])
        b = ols(X, y)  # E = a*D + b*u*D
        params['dalton_a'].append(b[0])
        params['dalton_b'].append(b[1])
        params['dalton_b_over_a'].append(b[1] / b[0])
        return [b[0] * r['dep'] + b[1] * r['ud'] for r in te]
    best = None
    runsT = sorted({r['run'] for r in tr})
    for m in (1, 2, 3):
        for sub in itertools.combinations(PRED, m):
            e = []
            for h in runsT:
                a = [r for r in tr if r['run'] != h]
                c = [r for r in tr if r['run'] == h]
                mu = np.array([[r[s] for s in sub] for r in a])
                mm, sd = mu.mean(0), mu.std(0) + 1e-9
                b = ols(np.c_[np.ones(len(a)), (mu - mm) / sd], obs(a))
                Xc = np.c_[np.ones(len(c)), (np.array([[r[s] for s in sub] for r in c]) - mm) / sd]
                e += list(abs(Xc @ b - obs(c)))
            s_ = np.mean(e)
            if best is None or s_ < best[0] - 1e-12:
                best = (s_, sub)
    sub = best[1]
    chosen.append(sub)
    mu = np.array([[r[s] for s in sub] for r in tr])
    mm, sd = mu.mean(0), mu.std(0) + 1e-9
    b = ols(np.c_[np.ones(len(tr)), (mu - mm) / sd], y)
    return [float(np.r_[1, (np.array([r[s] for s in sub]) - mm) / sd] @ b) for r in te]


for kind in ('k', 'dalton', 'reg'):
    for h in sorted({r['run'] for r in sc}):
        tr = [r for r in sc if r['run'] != h]
        te = [r for r in sc if r['run'] == h]
        for r, p in zip(te, fitpred(tr, te, kind)):
            r[kind] = p
cands = [('a engine sf=1.0 (wp3 headline)', 'sf1'), ('a engine sf=0.6 (production default)', 'sf6'),
         ('b engine x single k (LORO)', 'k'), ('c Dalton (a+b*u)*(es(Tw)-ea) (LORO)', 'dalton'),
         ('d regression <=3 of vpd,u,Tw,Ta,dep (nested LORO)', 'reg'),
         ('f physics fix: Penman 0.35(1+0.526u), mmHg, no fit, sf=1', 'fix'),
         ('f physics fix, wind x0.6', 'fix6'), ('baseline LORO training mean', 'mean')]
rng = np.random.default_rng(SEED)


def mae(rs, k):
    return np.mean([abs(r[k] - r['drop']) for r in rs])


def stats(rs, k):
    e = np.array([r[k] - r['drop'] for r in rs])
    p = np.array([r[k] for r in rs])
    return np.mean(abs(e)), np.mean(e), np.sqrt(np.mean(e ** 2)), (np.corrcoef(p, obs(rs))[0, 1] if p.std() > 0 else float('nan'))


pr = [r for r in sc if r['persist'] is not None]
runids = sorted({r['run'] for r in sc})
grp = {k: [r for r in sc if r['run'] == k] for k in runids}
picks = [rng.choice(runids, len(runids)) for _ in range(NB)]
pg = {kk: [r for r in pr if r['run'] == kk] for kk in sorted({r['run'] for r in pr})}
ppicks = [rng.choice(sorted(pg), len(pg)) for _ in range(NB)]


def bci(fn, pk=picks, g=grp):
    v = [fn([r for k in p for r in g[k]]) for p in pk]
    return np.percentile(v, [2.5, 97.5])


out_rows = []
for lab, k in cands:
    m, b, rm, rr = stats(sc, k)
    lo, hi = bci(lambda s: mae(s, k))
    row = [lab, len(sc), round(m, 3), f'[{lo:.2f},{hi:.2f}]', round(b, 3), round(rm, 3), round(rr, 3) if rr == rr else 'nan']
    if k != 'mean':
        d = mae(sc, k) - mae(sc, 'mean')
        l2, h2 = bci(lambda s: mae(s, k) - mae(s, 'mean'))
        d2 = mae(pr, k) - mae(pr, 'persist')
        l3, h3 = bci(lambda s: mae(s, k) - mae(s, 'persist'), ppicks, pg)
        row += [round(d, 3), f'[{l2:.2f},{h2:.2f}]', round(d2, 3), f'[{l3:.2f},{h3:.2f}]']
    else:
        row += ['', '', '', '']
    out_rows.append(row)
out_rows.append(['baseline persistence (25 d, 9 runs)', len(pr), round(mae(pr, 'persist'), 3)] + [''] * 8)
with open(f'{out}/metrics_wp3b.csv', 'w', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(['candidate', 'n', 'MAE', 'MAE_CI', 'bias', 'RMSE', 'r', 'dMAE_vs_mean', 'CI', 'dMAE_vs_persist(25d)', 'CI'])
    w.writerows(out_rows)
with open(f'{out}/fold_params.csv', 'w', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(['param', 'min', 'mean', 'max', 'sd'])
    for k, v in params.items():
        w.writerow([k, min(v), np.mean(v), max(v), np.std(v)])
    for s, c in collections.Counter(chosen).most_common():
        w.writerow(['reg_subset:' + '+'.join(s), c, 'of 10 folds', '', ''])
with open(f'{out}/daily_wp3b.csv', 'w', newline='') as fh:
    w = csv.writer(fh)
    ks = ['sf1', 'sf6', 'k', 'dalton', 'reg', 'fix', 'fix6', 'mean', 'persist']
    w.writerow(['date', 'run', 'obs'] + ks)
    for r in sc:
        w.writerow([r['date'], r['run'], r['drop']] + [r[k] for k in ks])
print('obs mean', obs(sc).mean(), 'sum ratio fix/obs', sum(r['fix'] for r in sc) / obs(sc).sum(),
      'sf1/obs', sum(r['sf1'] for r in sc) / obs(sc).sum(), 'mean u', np.mean([r['u'] for r in sc]))
print('full-sample dalton (descriptive)', ols(np.array([[r['dep'], r['ud']] for r in sc]), obs(sc)))
for r in out_rows:
    print(r)
print(open(f'{out}/fold_params.csv').read())
