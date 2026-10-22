"""WP3 PAN evaporation benchmark (frozen engine). Usage:
python -X utf8 -I wp3/run_wp3.py <rows.json> <outdir>
Engine is imported read-only; nothing is fitted for evaporation_mm_per_day."""
import json, sys, csv, math, datetime as dt, collections, statistics as S
import numpy as np
sys.path.insert(0, r'C:\Users\AgenticDev\OutdoorKoiFYP_research\Backend')
from koi.models import evaporation_engine as E
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

SEED = 20261022
NBOOT = 5000
rows_path, out = sys.argv[1], sys.argv[2]
raw = json.load(open(rows_path))[2:]
base = dt.datetime(1899, 12, 30)


def f(v):
    try:
        return float(v)
    except Exception:
        return None


rec = []
for x in raw:
    t = base + dt.timedelta(minutes=round((float(x['A']) + float(x['B'])) * 1440))
    rec.append(dict(t=t, air=f(x.get('C')), rh=f(x.get('D')), u=f(x.get('E')), swp=f(x.get('K')), tw=f(x.get('N'))))
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
assert len(runs) == 12, len(runs)


def wcsv(name, header, rows):
    with open(f'{out}/{name}', 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


# ---- daily table + exclusions
VARIANTS = (('sf1.0', dict(shelter_factor=1.0, wind_measured_at_10m=False)),
            ('sf0.6', dict(shelter_factor=0.6, wind_measured_at_10m=False)),
            ('sens10m_sf1.0', dict(shelter_factor=1.0, wind_measured_at_10m=True)),
            ('sens10m_sf0.6', dict(shelter_factor=0.6, wind_measured_at_10m=True)))
daily = []
excl = []
for d in days:
    l = byday[d]
    n = len(l)
    miss = {k: sum(q[k] is None for q in l) for k in ('air', 'rh', 'u', 'tw', 'swp')}
    drop = (l[0]['swp'] - l[-1]['swp']) if (miss['swp'] == 0 and n == 144) else None
    reasons = []
    if n != 144:
        reasons.append(f'{n} records not 144')
    for k, nm in (('air', 'air temp'), ('rh', 'RH'), ('u', 'wind'), ('tw', 'water temp'), ('swp', 'SWP level')):
        if miss[k]:
            reasons.append(f'{miss[k]} rows missing {nm}')
    if reasons:
        excl.append([str(d), run_of[d], '; '.join(reasons),
                     'drop still usable as persistence input' if drop is not None else 'no drop'])
    row = dict(date=d, run=run_of[d], drop=drop, ok=not reasons)
    if not reasons:
        for nm, kw in VARIANTS:
            row[nm] = S.mean(E.evaporation_mm_per_day(q['tw'], q['air'], q['rh'], q['u'], **kw) for q in l)
        row['wind'] = S.mean(q['u'] for q in l)
        row['vpd'] = S.mean(E.saturation_vapour_pressure_kpa(q['air']) * (1 - min(max(q['rh'], 0), 100) / 100) for q in l)
        row['airmean'] = S.mean(q['air'] for q in l)
    daily.append(row)
wcsv('exclusions.csv', ['date', 'run', 'reason', 'note'], excl)
dmap = {r['date']: r for r in daily}
scored = [r for r in daily if r['ok']]
wcsv('runs.csv', ['run', 'start', 'end', 'days', 'scored_days'],
     [[i, r[0], r[-1], len(r), sum(dmap[d]['ok'] for d in r)] for i, r in enumerate(runs, 1)])

# ---- baselines (leave-one-run-out)
for r in scored:
    other = [x['drop'] for x in scored if x['run'] != r['run']]
    r['train_mean'] = S.mean(other)
    prev = dmap.get(r['date'] - dt.timedelta(days=1))
    r['persist'] = prev['drop'] if (prev is not None and prev['run'] == r['run'] and prev['drop'] is not None) else None

# post-hoc LORO multiplicative factor (labelled)
for nm in ('sf1.0', 'sf0.6'):
    ks = {}
    for r in scored:
        tr = [x for x in scored if x['run'] != r['run']]
        k = sum(x['drop'] * x[nm] for x in tr) / sum(x[nm] ** 2 for x in tr)
        r['ph_' + nm] = k * r[nm]
        ks[r['run']] = k
    wcsv(f'posthoc_factors_{nm}.csv', ['held_out_run', 'k'], sorted(ks.items()))
wcsv('daily.csv', ['date', 'run', 'obs_drop_mm', 'sf1.0', 'sf0.6', 'sens10m_sf1.0', 'sens10m_sf0.6', 'train_mean_LORO',
                   'persistence', 'posthoc_sf1.0', 'posthoc_sf0.6', 'wind_mean', 'vpd_kpa', 'air_mean'],
     [[r['date'], r['run'], r['drop']] + [r[k] for k in ('sf1.0', 'sf0.6', 'sens10m_sf1.0', 'sens10m_sf0.6', 'train_mean',
                                                        'persist', 'ph_sf1.0', 'ph_sf0.6', 'wind', 'vpd', 'airmean')] for r in scored])


def met(pred, obs):
    e = np.array(pred) - np.array(obs)
    o = np.array(obs)
    p = np.array(pred)
    r = float(np.corrcoef(p, o)[0, 1]) if len(e) > 2 and np.std(p) > 0 and np.std(o) > 0 else float('nan')
    return dict(n=len(e), MAE=float(np.mean(abs(e))), bias=float(np.mean(e)), RMSE=float(np.sqrt(np.mean(e ** 2))), r=r)


MK = ('MAE', 'bias', 'RMSE', 'r')
rng = np.random.default_rng(SEED)


def boot(rows, key, key2=None):
    rs = sorted({r['run'] for r in rows})
    grp = {k: [r for r in rows if r['run'] == k] for k in rs}
    res = {m: [] for m in MK}
    diff = []
    for _ in range(NBOOT):
        pick = rng.choice(rs, len(rs))
        sel = [r for k in pick for r in grp[k]]
        m = met([r[key] for r in sel], [r['drop'] for r in sel])
        for k in MK:
            res[k].append(m[k])
        if key2:
            diff.append(np.mean([abs(r[key] - r['drop']) - abs(r[key2] - r['drop']) for r in sel]))
    ci = {k: tuple(np.nanpercentile(res[k], [2.5, 97.5])) for k in MK}
    return ci, (tuple(np.percentile(diff, [2.5, 97.5])) if key2 else None)


metrics = []


def add(label, rows, key, key2=None, tag=''):
    rows = [r for r in rows if r.get(key) is not None]
    m = met([r[key] for r in rows], [r['drop'] for r in rows])
    ci, dci = boot(rows, key, key2)
    d = dict(label=label, tag=tag, n_days=m['n'], n_runs=len({r['run'] for r in rows}), vs=key2)
    for k in MK:
        d[k] = m[k]
        d[k + '_lo'], d[k + '_hi'] = ci[k]
    if key2:
        d['dMAE'] = float(np.mean([abs(r[key] - r['drop']) - abs(r[key2] - r['drop']) for r in rows]))
        d['dMAE_lo'], d['dMAE_hi'] = dci
    metrics.append(d)


persist_rows = [r for r in scored if r['persist'] is not None]
for nm, lab in (('sf1.0', 'engine sf=1.0'), ('sf0.6', 'engine sf=0.6 (default)')):
    add(lab, scored, nm, 'train_mean', 'H1 all scored days')
add('LORO training-run mean', scored, 'train_mean', None, 'H1 all scored days')
for nm, lab in (('sf1.0', 'engine sf=1.0'), ('sf0.6', 'engine sf=0.6 (default)')):
    add(lab, persist_rows, nm, 'persist', 'H2 persistence subset')
add('LORO training-run mean', persist_rows, 'train_mean', 'persist', 'H2 persistence subset')
add('persistence (previous-day drop)', persist_rows, 'persist', None, 'H2 persistence subset')
for nm in ('sens10m_sf1.0', 'sens10m_sf0.6'):
    add(nm + ' (wind_measured_at_10m=True)', scored, nm, 'train_mean', 'SENSITIVITY')
for nm in ('sf1.0', 'sf0.6'):
    add('LORO scale factor on ' + nm, scored, 'ph_' + nm, 'train_mean', 'POST-HOC')
keys = ['label', 'tag', 'n_days', 'n_runs'] + [x for k in MK for x in (k, k + '_lo', k + '_hi')] + ['vs', 'dMAE', 'dMAE_lo', 'dMAE_hi']
wcsv('metrics.csv', keys, [[d.get(k) for k in keys] for d in metrics])

# ---- regimes
reg = []
for var in ('wind', 'vpd'):
    v = np.array([r[var] for r in scored])
    q1, q2 = np.percentile(v, [100 / 3, 200 / 3])
    for r in scored:
        r['t_' + var] = 'low' if r[var] <= q1 else ('mid' if r[var] <= q2 else 'high')
    for t in ('low', 'mid', 'high'):
        s = [r for r in scored if r['t_' + var] == t]
        row = [var, t, f'{min(r[var] for r in s):.3f}-{max(r[var] for r in s):.3f}', len(s), len({r['run'] for r in s})]
        for nm in ('sf1.0', 'sf0.6', 'train_mean'):
            m = met([r[nm] for r in s], [r['drop'] for r in s])
            row += [m['MAE'], m['bias']]
        row.append(float(np.mean([r['drop'] for r in s])))
        reg.append(row)
wcsv('regimes.csv', ['variable', 'tercile', 'range', 'n_days', 'n_runs', 'sf1.0_MAE', 'sf1.0_bias', 'sf0.6_MAE', 'sf0.6_bias',
                     'trainmean_MAE', 'trainmean_bias', 'obs_mean'], reg)

# ---- cumulative drift
cum = []
fin = []
for i, run in enumerate(runs, 1):
    s = [r for r in scored if r['run'] == i]
    if not s:
        continue
    co = cp1 = cp6 = 0.0
    for r in s:
        co += r['drop']
        cp1 += r['sf1.0']
        cp6 += r['sf0.6']
        cum.append([i, r['date'], co, cp1, cp6, cp1 - co, cp6 - co])
    fin.append([i, len(s), co, cp1, cp6, cp1 - co, cp6 - co, (cp1 - co) / co * 100, (cp6 - co) / co * 100])
wcsv('cumulative_daily.csv', ['run', 'date', 'cum_obs', 'cum_sf1.0', 'cum_sf0.6', 'drift_sf1.0', 'drift_sf0.6'], cum)
wcsv('cumulative_final.csv', ['run', 'scored_days', 'obs_total_mm', 'pred_sf1.0_mm', 'pred_sf0.6_mm', 'drift_sf1.0_mm',
                              'drift_sf0.6_mm', 'drift_sf1.0_pct', 'drift_sf0.6_pct'], fin)


# ---- water-air temperature (hourly, per run, engine as-is)
def hourly(run_days, key):
    acc = collections.defaultdict(list)
    for d in run_days:
        for q in byday[d]:
            if q[key] is not None:
                acc[q['t'].replace(minute=0)].append(q[key])
    return {h: S.mean(v) for h, v in acc.items() if len(v) >= 5}


tfit = []
for i, run in enumerate(runs, 1):
    hrs = len(run) * 24
    res = E.fit_water_air_temperature(hourly(run, 'tw'), hourly(run, 'air'))
    if res['status'] != 'fitted':
        det = res['details']
        tfit.append(dict(run=i, hours=hrs, status='Blocked', reason=f"{det.get('reason')} (usable_hours={res['sample_count']}, {', '.join(f'{k}={v}' for k, v in det.items() if k != 'reason')})"))
        continue
    er = res['error']
    d = res['details']
    tfit.append(dict(run=i, hours=hrs, status='fitted', lag=res['lag_hours'], offset=res['value'], n_eval=d['evaluation_hours'],
                     rmse=er['evaluation_rmse'], bias=er['evaluation_mean_error'], persist=er['persistence_rmse'],
                     default=er['default_rmse'], train_from=str(res['training_from']), eval_from=str(res['evaluation_from']),
                     eval_to=str(res['evaluation_to'])))
fitted = [t for t in tfit if t['status'] == 'fitted']


def pooled(key, idx):
    return math.sqrt(sum(t[key] ** 2 * t['n_eval'] for t in idx) / sum(t['n_eval'] for t in idx))


summ = None
if fitted:
    summ = {k: pooled(k, fitted) for k in ('rmse', 'persist', 'default')}
    bs = {k: [] for k in ('rmse', 'persist', 'default')}
    for _ in range(NBOOT):
        idx = [fitted[j] for j in rng.integers(0, len(fitted), len(fitted))]
        for k in bs:
            bs[k].append(pooled(k, idx))
    summ['ci'] = {k: tuple(float(x) for x in np.percentile(bs[k], [2.5, 97.5])) for k in bs}
    summ['n_runs'] = len(fitted)
    summ['n_eval'] = sum(t['n_eval'] for t in fitted)
    summ['beats_persist_runs'] = sum(t['rmse'] < t['persist'] for t in fitted)
    summ['beats_default_runs'] = sum(t['rmse'] < t['default'] for t in fitted)
keys2 = ['run', 'hours', 'status', 'reason', 'lag', 'offset', 'n_eval', 'rmse', 'bias', 'persist', 'default', 'train_from', 'eval_from', 'eval_to']
wcsv('temp_fit.csv', keys2, [[t.get(k) for k in keys2] for t in tfit])

# ---- figure
fig, ax = plt.subplots(1, 3, figsize=(15, 4.6))
obs = [r['drop'] for r in scored]
for nm, c in (('sf1.0', 'tab:blue'), ('sf0.6', 'tab:red')):
    ax[0].scatter(obs, [r[nm] for r in scored], s=22, c=c, label=nm, alpha=.8)
lim = [0, max(obs) * 1.05]
ax[0].plot(lim, lim, 'k--', lw=1)
ax[0].set(xlabel='observed SWP daily drop (mm/day)', ylabel='engine prediction (mm/day)', title=f'Predicted vs observed (n={len(scored)} dry days)')
ax[0].legend()
ax[1].scatter(obs, [r['train_mean'] for r in scored], s=22, c='tab:green', label='LORO run-mean baseline', alpha=.8)
ax[1].plot(lim, lim, 'k--', lw=1)
ax[1].set(xlabel='observed (mm/day)', ylabel='baseline (mm/day)', title='Baseline (leave-one-run-out)')
ax[1].legend()
for i in sorted({c[0] for c in cum}):
    s = [c for c in cum if c[0] == i]
    x = list(range(1, len(s) + 1))
    ax[2].plot(x, [c[5] for c in s], 'b-o', ms=3, lw=1, alpha=.6)
    ax[2].plot(x, [c[6] for c in s], 'r-o', ms=3, lw=1, alpha=.6)
ax[2].axhline(0, c='k', lw=.8)
ax[2].set(xlabel='scored day within run', ylabel='cumulative pred - obs (mm)', title='Cumulative drift per run (blue sf1.0, red sf0.6)')
plt.tight_layout()
plt.savefig(f'{out}/fig_pred_vs_obs.png', dpi=130)

json.dump(dict(seed=SEED, nboot=NBOOT, n_scored=len(scored), n_excluded=len(excl), runs=[[str(x) for x in r] for r in runs],
               metrics=metrics, temp=tfit, temp_pooled=summ), open(f'{out}/results.json', 'w'), indent=1, default=str)
print('scored', len(scored), 'excluded', len(excl), 'persist subset', len(persist_rows))
