"""
possible_trades.py -- the Trades tab of the Alberta dashboard (docs/index.html).

Every morning, after the model is rebuilt, prices the contracts your broker lists
from the latest ICE settles (Warehouse daily codes XDT flat / XDQ on-peak / XDP
off-peak), runs every backtested rule against the model's fair value, and writes
the result into the dashboard (the line 'const T = ...;'). On the page you type
the broker's bid / offer and the rules re-run on that price.

Contracts (as the broker lists them):
  same day        today - information only (model = settled hours + forecast for the rest)
  next day        Mon-Thu for tomorrow                                   10 MW (20 on 3 of 3)
  single days     every weekday after tomorrow through Friday (d2-d4)   5 MW (10 on 3 of 3)
                  Saturday / Sunday singles: information only
  current week    Monday: Tue-Thu   Tuesday: Wed-Thu   (Mon and Fri drop out)   5 MW (10 on 3 of 3)
  weekend         Sat-Sun package, Thursday (or Friday if Thursday did not qualify)  5/10 MW
  next week       Mon-Fri                                                5 MW
  never sold      Friday for Monday, Sat-Mon

Rules (unchanged): sell when price - model > $10 ($25 for Jul-Nov delivery), price >=
last 7 days' average pool (weekday products), not (calm AND extreme temperature);
current-week strip and next week also need price >= $35. Conviction 3 points: gap
> $20 / no weather warning / gas forecast >= last 7 days (near) or wind >= normal (next week).
One sale per delivery day across next-day and single-day contracts: the first that qualifies.

Backtest on the live model replayed every morning, Oct 2024 - Sep 2026, with these contracts:
  179 trades, 87% won, +$937k, worst drawdown -$22k, worst trade -$22k, never more than 35 MW short on a day.

Also reads verify/trade_log.csv (written by the "Log trade" button on the Trades tab),
scores every logged trade once it has settled, and writes verify/trade_log_scored.csv.
Appends every product, every morning, to verify/possible_trades.csv.

    python possible_trades.py
    python possible_trades.py --as-of 2026-10-08     # test: writes docs/index_test_Thu.html, logs nothing
"""
import json, re
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
LOG = HERE / 'verify' / 'possible_trades.csv'
TLOG = HERE / 'verify' / 'trade_log.csv'
FILL = 2.5; STRESS = 100.0
SIZES = {'next': (10, 20), 'daily': (5, 10), 'bow': (5, 10), 'week': (5, 5), 'wkend': (5, 10)}
WORST = {'next': 'worst backtest trade -$15k (Mar 25 2026, 10 MW)',
         'daily': 'worst backtest trade -$7k (Thu Jan 2 2025 sold 3 days out, 5 MW)',
         'bow': 'worst backtest trade -$13k (Wed-Thu Nov 5-6 2025, 10 MW)',
         'wkend': 'worst backtest trade -$22k (weekend of Jun 7 2025, 10 MW)',
         'week': 'worst backtest trade -$16k (week of Mar 23 2026, 5 MW)'}
BT_DAILY = {2: 'Single days 2 out (weekdays): 50 days passed, 90% won, +$24.8/MWh, worst -$63/MWh',
            3: 'Single days 3 out (weekdays): 43 days passed, 91% won, +$21.3/MWh, worst -$57/MWh',
            4: 'Single days 4 out (weekdays): 20 days passed, all won, +$20.9/MWh'}
BT = {'next': 'Backtest Oct 2024-Sep 2026: 66 days passed, 80% won, +$12.9/MWh (+$271k at 10/20 MW on its own)',
      'bow_mon': 'Backtest (Mondays, Tue-Thu): 14 passed, 79% won, +$15.7/MWh, worst -$9/MWh',
      'bow_tue': 'Backtest (Tuesdays, Wed-Thu): 12 passed, 83% won, +$13.5/MWh, worst -$27/MWh',
      'week': 'Backtest Oct 2024-Sep 2026: 23 trades, 91% won, +$337k',
      'wkend': 'Backtest Oct 2024-Sep 2026: 22 weekends, 86% won, +$81k at 5/10 MW',
      'wkday1': 'Single Saturday / Sunday days: 82-94% won but worst -$107 to -$137/MWh - information only (the weekend package covers them).',
      'today': 'Same day: not backtested - shown so you can see where today is heading (settled hours + model for the rest).',
      'mon': 'Backtest (72 Fridays): selling Monday lost - blind -$7.6/MWh, with every filter -$5.1/MWh, worst -$76k at 10 MW. Not sold.',
      'satmon': 'Backtest (70 Fridays): selling Sat-Mon lost - with the filters -$1.6/MWh, worst -$81k at 10 MW. Not sold.',
      'sat': 'Backtest (71 Fridays): 13 passed the gap check and all 13 won (+$11.3/MWh) - too few to size yet. Information only.',
      'sun': 'Backtest (70 Fridays): about break-even (-$0.7/MWh with the filters). Information only.',
      'week2': 'Beyond the backtested horizon.'}
NEVER = ('mon', 'satmon'); INFO = ('today', 'sat', 'sun', 'wkday1', 'week2')
SINGLE = ('next', 'daily')          # one sale per delivery day across these
CARDS = {}
def tgroup(he): return 0 if he <= 6 else 1 if he <= 10 else 2 if he <= 16 else 3 if he <= 21 else 4


def load_model():
    s = (HERE / 'docs' / 'index.html').read_text(encoding='utf-8')
    D = json.loads(re.search(r'^const D = (\{.*\});\s*$', s, re.M).group(1))
    global CARDS; CARDS = D.get('cards') or {}
    G = {}
    for r in D['grid']: G.setdefault((r['g'], r.get('L', 0) or 0), []).append(r)
    for k in G: G[k].sort(key=lambda r: r['c'])
    def look(c, g, L):
        a = G.get((g, L)) or G[(g, 0)]
        return float(np.interp(c, [r['c'] for r in a], [r['mean'] for r in a]))
    A = pd.DataFrame(D['hours']); A['t'] = pd.to_datetime(A.dt); A['d'] = A.t.dt.normalize()
    A['gas'] = A.cc + A.sc + A.cogen + A.gfs
    A['cush'] = A.gas + A.bio + A.wind + A.solar - (A.load - A.bcmatl - A.sask)
    A['ev'] = [look(c, tgroup(he), int(lb or 0)) for c, he, lb in zip(A.cush, A.he, A.get('lb', 0))]
    return A[A.status == 'FORECAST'].copy(), A, D['meta']


def products(today):
    w = today.dayofweek; P = []; D1 = pd.Timedelta(days=1); d = lambda k: today + k * D1
    add = lambda key, name, days, tested=True, note='', bt=None: P.append(dict(key=key, name=name, days=days, tested=tested, note=note, bt=bt))
    add('today', f"Same day {today:%a %b %d}", [today], False)
    if w <= 3: add('next', f"Next day {d(1):%a %b %d}", [d(1)])
    for k in range(2, 7 - w):                       # single days after tomorrow, through Sunday
        day = d(k)
        if day.dayofweek <= 4: add('daily', f"{day:%a %b %d} (single day, {k} out)", [day], bt=BT_DAILY.get(k))
        elif w <= 3: add('wkday1', f"{day:%a %b %d} (single day, {k} out)", [day])
    if w == 0: add('bow', f"Bal week Tue-Thu {d(1):%b %d}-{d(3):%d}", [d(1), d(2), d(3)], bt=BT['bow_mon'])
    if w == 1: add('bow', f"Bal week Wed-Thu {d(1):%b %d}-{d(2):%d}", [d(1), d(2)], bt=BT['bow_tue'])
    if w in (3, 4):
        sat = d(5 - w)
        add('wkend', f"Weekend {sat:%a %b %d}-{sat+D1:%a %d}", [sat, sat + D1],
            note='decided Thursday' if w == 3 else 'decided Friday (sell here only if Thursday did not qualify)')
    if w == 4:
        add('mon', f"Monday {d(3):%b %d} (Friday decision)", [d(3)])
        add('satmon', f"Sat-Mon {d(1):%b %d}-{d(3):%d}", [d(1), d(2), d(3)])
        add('sat', f"Saturday {d(1):%b %d}", [d(1)]); add('sun', f"Sunday {d(2):%b %d}", [d(2)])
    nm = d(7 - w)
    add('week', f"Wk {nm:%b %d}-{nm+4*D1:%d} (Mon-Fri)", [nm + k * D1 for k in range(5)])
    nm2 = nm + 7 * D1
    add('week2', f"Wk {nm2:%b %d}-{nm2+4*D1:%d} (Mon-Fri)", [nm2 + k * D1 for k in range(5)], False,
        'the week after next - beyond the backtested horizon')
    return P


def score_log(pool, today):
    """verify/trade_log.csv (from the page) -> scored rows for the page and verify/trade_log_scored.csv."""
    if not TLOG.exists(): return []
    try: L = pd.read_csv(TLOG, dtype=str, keep_default_na=False)
    except Exception as e: print(f"  WARNING could not read {TLOG.name}: {e}"); return []
    voided = set(L.loc[L.get('type', pd.Series(dtype=str)) == 'void', 'ref']) if 'ref' in L else set()
    out = []
    for _, r in L.iterrows():
        x = {k: r.get(k, '') for k in L.columns}
        if x.get('type', 'trade') != 'trade' or x.get('id') in voided: continue
        try:
            ds = pd.date_range(x['first'], x['last']); px = float(x['price']); mw = float(x['mw'])
            done = all(dd in pool.index for dd in ds) and ds[-1] < today
            if done:
                s = float(pool.loc[ds].mean()); sign = 1 if x['side'] == 'SELL' else -1
                x['settle'] = round(s, 2); x['pnl_mwh'] = round(sign * (px - s), 2)
                x['pnl_usd'] = round(sign * (px - s) * mw * 24 * len(ds))
            else: x['settle'] = x['pnl_mwh'] = x['pnl_usd'] = ''
        except Exception: x['settle'] = x['pnl_mwh'] = x['pnl_usd'] = ''
        out.append(x)
    if out:
        tmp = HERE / 'verify' / 'trade_log_scored.csv.tmp'; pd.DataFrame(out).to_csv(tmp, index=False)
        tmp.replace(HERE / 'verify' / 'trade_log_scored.csv')
    return out


def engine_odds():
    """Risk engine (cache/risk_today.json): chance a short wins at a price, same maths as the page."""
    try: P = json.loads((HERE / 'cache' / 'risk_today.json').read_text(encoding='utf-8'))
    except Exception: return None
    rng = np.random.default_rng(5)
    def f(days, px):
        Qs = [P['days'].get(str(d.date()), {}).get('q') for d in days]
        if px is None or any(q is None for q in Qs): return None
        Qs = np.array(Qs); thr = px - FILL
        como = float((Qs.mean(axis=0) < thr).mean())
        raw = como if len(days) == 1 else 0.5 * (como + float((Qs[:, rng.integers(0, Qs.shape[1], (len(days), 3000))].mean(axis=0) < thr).mean()))
        return float(np.interp(raw, P['pw_x'], P['pw_y']))
    return f


def main():
    import sys
    now = pd.Timestamp.now(tz='America/Edmonton').tz_localize(None)
    test = len(sys.argv) > 2 and sys.argv[1] == '--as-of'
    if test: now = pd.Timestamp(sys.argv[2]) + pd.Timedelta(hours=8)   # test only: another weekday, nothing logged
    today = now.normalize()
    Hh, Hall, meta = load_model()
    f = pd.read_csv(HERE / 'cache' / 'fwd_aeso_daily.csv', parse_dates=['EffectiveDate', 'Strip'])
    f = f[f.EffectiveDate <= today]; sd = f.EffectiveDate.max(); F = f[f.EffectiveDate == sd].pivot_table(index='Strip', columns='ExchangeCode', values='Price')
    ph = pd.read_csv(HERE / 'model' / 'price_history.csv', index_col=0, parse_dates=True).iloc[:, 0]
    pool = ph.groupby(ph.index.normalize()).mean()
    last7 = float(pool[today - pd.Timedelta(days=7):today - pd.Timedelta(days=1)].mean())
    C = pd.read_csv(HERE / 'cache' / 'composition.csv', parse_dates=['datetime_begin'])
    if 'lead_bucket' in C: C = C[C.lead_bucket == -1]
    C = C.set_index('datetime_begin').sort_index(); C = C[~C.index.duplicated(keep='last')]
    C['gas'] = C[['cc', 'sc', 'cogen', 'gfs']].sum(axis=1)
    gas7 = float(C.gas[today - pd.Timedelta(days=7):today - pd.Timedelta(minutes=1)].mean())
    wyr = float(C.wind[today - pd.Timedelta(days=365):today].mean())
    mf = (C.wind.groupby(C.index.month).mean() / C.wind.mean())
    wx = None
    try:
        wx = pd.read_csv(HERE / 'cache' / 'wx_fcst.csv', parse_dates=['t']); wx['d'] = wx.t.dt.normalize()
        wx = wx.groupby('d').temp.agg(['min', 'max'])
    except Exception: pass
    th = {}
    try: th = json.loads((HERE / 'cache' / 'thermal_status.json').read_text())
    except Exception: pass
    th_ok = bool(th) and (now - pd.Timestamp(th.get('built', '2000-01-01'))).total_seconds() / 3600 < 36
    odds = engine_odds()
    rows = []
    for p in products(today):
        key = p['key']; days = p['days']
        r = dict(run=str(now)[:16], product=p['name'], key=key, first=str(days[0].date()), last=str(days[-1].date()),
                 hours=24 * len(days), tested=p['tested'], note=p['note'], settle_date=str(sd.date()))
        have = [d for d in days if d in F.index]
        r['fwd'] = round(float(F.loc[days, 'XDT'].mean()), 2) if len(have) == len(days) else None
        r['fwd_on'] = round(float(F.loc[days, 'XDQ'].mean()), 2) if r['fwd'] is not None and 'XDQ' in F else None
        r['fwd_off'] = round(float(F.loc[days, 'XDP'].mean()), 2) if r['fwd'] is not None and 'XDP' in F else None
        if key == 'today':    # settled hours at their price, the rest at the model
            a = Hall[Hall.d == days[0]]; v = np.where(a.price.notna(), a.price, a.ev)
            r['model_days'] = 1; full = len(a) >= 20
            r['model'] = round(float(np.mean(v)), 2) if full else None
            on = (a.he >= 8) & (a.he <= 23); r['model_on'] = round(float(np.mean(v[on.values])), 2) if full else None
            h = a
        else:
            h = Hh[Hh.d.isin(days)]
            r['model_days'] = int(h.d.nunique())
            full = r['model_days'] == len(days) and h.groupby('d').size().min() >= 20
            r['model'] = round(float(h.ev.mean()), 2) if full else None
            r['model_on'] = round(float(h[(h.he >= 8) & (h.he <= 23)].ev.mean()), 2) if full else None
        r['last7'] = round(last7, 2)
        r['gap'] = None if r['fwd'] is None or r['model'] is None else round(r['fwd'] - r['model'], 2)
        bar = 25.0 if days[0].month in (7, 8, 9, 10, 11) else 10.0; r['bar'] = bar
        floor = 35.0 if key in ('bow', 'week', 'week2') else None; r['floor'] = floor
        weekday = all(d.dayofweek <= 4 for d in days)
        r['gas_fc'] = round(float(h.gas.mean())) if len(h) else None; r['gas7'] = round(gas7)
        r['wind_fc'] = round(float(h.wind.mean())) if len(h) else None
        r['wind_norm'] = round(wyr * float(np.mean([mf.get(d.month, 1.0) for d in days])))
        r['calm'] = bool(r['wind_fc'] is not None and r['wind_fc'] / wyr < 0.7)
        tmin = tmax = None
        if wx is not None and all(d in wx.index for d in days):
            tmin = float(wx.loc[days, 'min'].min()); tmax = float(wx.loc[days, 'max'].max())
        r['tmin'] = tmin; r['tmax'] = tmax
        r['extreme'] = bool(tmin is not None and (tmax >= 28 or tmin <= -20))
        r['temp_known'] = tmin is not None
        g = r['gap']
        ok1 = g is not None and g > bar
        ok2 = True if not weekday else (r['fwd'] is not None and r['fwd'] >= last7)
        ok3 = not (r['calm'] and r['extreme'])
        ok4 = floor is None or (r['fwd'] is not None and r['fwd'] >= floor)
        near = key != 'week' and key != 'week2'
        pts = [g is not None and g > 20, (not r['calm']) and (not r['extreme']) and r['temp_known'],
               (r['gas_fc'] is not None and r['gas_fc'] >= gas7) if near else (r['wind_fc'] is not None and r['wind_fc'] >= r['wind_norm'])]
        r['points'] = int(sum(pts))
        lo, hi = SIZES.get(key, (0, 0))
        r['size_mw'] = (hi if r['points'] >= 3 else lo) if key in SIZES else 0
        r['backtest'] = p['bt'] or BT.get(key, '')
        passes = ok1 and ok2 and ok3 and ok4
        if key in NEVER: status = 'DO NOT SELL'
        elif key in INFO: status = 'NO READ YET' if r['model'] is None else 'INFO ONLY'
        elif passes and p['tested'] and r['size_mw']: status = 'SELL'
        elif passes: status = 'POSSIBLE - NOT BACKTESTED'
        elif g is not None and g > bar - 5: status = 'WATCH'
        else: status = 'PASS'
        r['status'] = status
        lvl = [x for x in ((r['model'] + bar) if r['model'] is not None else None, last7 if weekday else None, floor) if x is not None]
        r['offer_at'] = round(max(lvl), 2) if lvl else None
        r['worst_case_usd'] = round(r['size_mw'] * r['hours'] * STRESS) if r['size_mw'] else None
        pe = odds(days, r['fwd']) if (odds and key not in INFO and key not in NEVER) else None
        r['p_win_engine'] = None if pe is None else round(pe, 3)
        r['size_engine'] = (hi if pe >= 0.9 else lo) if (pe is not None and key in SIZES) else None   # logged alongside, not used yet
        cal = lambda d, k: ((CARDS.get(str(d.date())) or {}).get('calc') or {}).get(k)
        r['_x'] = dict(key=key, id=(('D:' + str(days[0].date())) if len(days) == 1 else ('R:' + str(days[0].date()) + '..' + str(days[-1].date()))),
                       name=p['name'], days=[str(d.date()) for d in days], hours=r['hours'], tested=bool(p['tested']), note=p['note'],
                       fwd=r['fwd'], fwd_on=r['fwd_on'], fwd_off=r['fwd_off'], model=r['model'], model_on=r['model_on'], last7=r['last7'],
                       bar=bar, floor=floor, weekday=weekday, gas_fc=r['gas_fc'], gas7=r['gas7'], wind_fc=r['wind_fc'], wind_norm=r['wind_norm'],
                       calm=r['calm'], extreme=r['extreme'], temp_known=r['temp_known'], tmin=tmin, tmax=tmax, near=near,
                       size=list(SIZES.get(key, (0, 0))), sized=key in SIZES, never=key in NEVER, info=key in INFO, single=key in SINGLE,
                       lead=int((days[0] - today).days), p_win_engine=r['p_win_engine'], size_engine=r['size_engine'], backtest=r['backtest'], worst=WORST.get(key, ''), status=status,
                       cautions=[f"{d:%a} " + ' and '.join(x for x, k in (('tight day (cushion under 800 MW)', 'veto_t'), ('evening wind under half of normal', 'veto_w')) if cal(d, k))
                                 for d in days if cal(d, 'veto_t') or cal(d, 'veto_w')])
        rows.append(r)
    T = dict(built=str(now)[:16], today=str(today.date()), settle_date=str(sd.date()), model_built=meta.get('bundle_built', '?'),
             thermal=th.get('source', 'unknown'), thermal_vintage=th.get('vintage', ''), thermal_ok=th_ok,
             last7=round(last7, 2), fill=FILL, stress=STRESS, products=[r['_x'] for r in rows], log=score_log(pool, today))
    to_dashboard(T, (HERE / 'docs' / f'index_test_{today:%a}.html') if test else None)
    if not test: log(rows, pool)
    for r in rows:
        print(f"  {r['product']:<36} {r['status']:<26} fwd {r['fwd']}  model {r['model']}  gap {r['gap']}  size {r['size_mw']} MW")
    print(f"  logged trades read: {len(T['log'])}   on the dashboard: docs/index.html -> Trades tab")


def to_dashboard(T, out=None):
    """Put the trades into docs/index.html (the line 'const T = ...;'). out: write a copy there instead (test)."""
    src = HERE / 'docs' / 'index.html'
    s = src.read_text(encoding='utf-8')
    line = 'const T = ' + json.dumps(T, separators=(',', ':'), default=str) + ';'
    n = len(re.findall(r'^const T = .*;\s*$', s, flags=re.M))
    if n != 1: raise SystemExit(f"docs/index.html has {n} 'const T' lines (expected 1) - rebuild the page with update.py first")
    s = re.sub(r'^const T = .*;\s*$', lambda m: line, s, count=1, flags=re.M)
    dst = out or src
    tmp = dst.with_suffix('.tmp'); tmp.write_text(s, encoding='utf-8'); tmp.replace(dst)


def log(rows, pool):
    LOG.parent.mkdir(exist_ok=True)
    new = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith('_')} for r in rows])
    L = pd.concat([pd.read_csv(LOG), new], ignore_index=True) if LOG.exists() else new
    L = L.drop_duplicates(['run', 'product'], keep='last')
    for c in ('settle', 'pnl_if_sold'):
        if c not in L: L[c] = np.nan
    for i, r in L.iterrows():
        if pd.notna(r.settle) or pd.isna(r.fwd): continue
        ds = pd.date_range(r['first'], r['last'])
        if all(d in pool.index for d in ds) and pd.Timestamp(r['last']) < pd.Timestamp.now().normalize():
            s = float(pool.loc[ds].mean()); L.at[i, 'settle'] = round(s, 2); L.at[i, 'pnl_if_sold'] = round(float(r.fwd) - FILL - s, 2)
    tmp = LOG.with_suffix('.csv.tmp'); L.to_csv(tmp, index=False); tmp.replace(LOG)


if __name__ == '__main__':
    main()
