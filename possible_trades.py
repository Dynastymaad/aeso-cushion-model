"""
possible_trades.py -- the "Possible Trades" page: docs/trades.html

Every morning, after the model is rebuilt, prices the contracts you can actually
trade from the latest ICE settles (Warehouse daily codes XDT flat / XDQ on-peak /
XDP off-peak) and runs every backtested rule against our model's fair value.

Rules (backtested on the live-model replay, Apr 2025 - Sep 2026, honest thermal):
  enter (sell)   gap = forward - model > $10   ($25 when delivery starts Jul-Nov)
                 forward >= last 7 days' average pool price
                 NOT (calm wind AND extreme temperature) in the delivery days
                 week / balance-of-week only: forward >= $35  (a short earns at most
                 the forward; below $35 every-week selling since 2024 lost money)
  conviction     three points: gap > $20 / no weather warning at all /
                 next day + balance of week: gas forecast >= last 7 days' gas
                 next week:                  wind forecast >= normal for the month
  size           next day 10 MW (20 MW on 3 of 3)   balance of week 5 MW (10 on 3 of 3)
                 next week Mon-Fri 5 MW
  backtest       70 trades, 81% won, +$429k, worst drawdown -$31k, worst trade -$17k
  weekend        Sat-Sun package, decided Thursday (or Friday if Thursday did not
                 qualify): gap over the bar and the weather check only. Last week's
                 7-day pool and the $35 floor are weekday yardsticks and did not fit
                 weekends (weekend forwards sit near $30). 5 MW, 10 MW on 3 of 3.
                 16 weekends, 69% won, +$47k, worst -$11k; book drawdown unchanged.
  not sold       Friday for Monday (72 Fridays: selling lost -$7.6/MWh blind, -$5.1
                 with the filters) and the Sat-Mon package (-$1.6, worst -$81k).
                 Saturday alone (13 qualifying, all won) and Sunday alone (about
                 break-even) are shown for information only - too few to size.

Appends every product, every morning, to verify/possible_trades.csv and fills in
the settle and the P&L of selling at (forward - $2.50) once a product has settled.

    python possible_trades.py
"""
import json, re, html as H
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
LOG = HERE / 'verify' / 'possible_trades.csv'
OUT = HERE / 'docs' / 'trades.html'
FILL = 2.5; STRESS = 100.0
SIZES = {'next': (10, 20), 'bow': (5, 10), 'week': (5, 5), 'wkend': (5, 10)}
WORST = {'next': 'worst backtest trade -$17k (May 6 2026, 10 MW)', 'bow': 'worst backtest trade -$1k',
         'wkend': 'worst backtest trade -$11k (weekend of Jun 7 2025, 5 MW)',
         'week': 'worst backtest trade -$16k (week of Mar 23 2026, 5 MW)'}
BT = {'next': 'Backtest: 47 trades, 79% won, +$135k',
      'bow': 'Backtest: 6 trades, 83% won, +$53k',
      'week': 'Backtest: 17 trades, 88% won, +$240k',
      'wkend': 'Backtest: 16 weekends, 69% won, +$47k at 5/10 MW',
      'mon': 'Backtest (72 Fridays): selling Monday lost - blind -$7.6/MWh, with every filter -$5.1/MWh, worst -$76k at 10 MW. Not sold.',
      'satmon': 'Backtest (70 Fridays): selling Sat-Mon lost - with the filters -$1.6/MWh, worst -$81k at 10 MW. Not sold.',
      'sat': 'Backtest (71 Fridays): 13 passed the gap check and all 13 won (+$11.3/MWh) - too few to size yet. Information only.',
      'sun': 'Backtest (70 Fridays): about break-even (-$0.7/MWh with the filters). Information only.',
      'week2': 'Beyond the backtested horizon.'}
NEVER = ('mon', 'satmon'); INFO = ('sat', 'sun', 'week2')
def tgroup(he): return 0 if he <= 6 else 1 if he <= 10 else 2 if he <= 16 else 3 if he <= 21 else 4

def load_model():
    s = (HERE / 'docs' / 'index.html').read_text(encoding='utf-8')
    D = json.loads(re.search(r'^const D = (\{.*\});\s*$', s, re.M).group(1))
    G = {}
    for r in D['grid']: G.setdefault((r['g'], r.get('L', 0) or 0), []).append(r)
    for k in G: G[k].sort(key=lambda r: r['c'])
    def look(c, g, L):
        a = G.get((g, L)) or G[(g, 0)]
        return float(np.interp(c, [r['c'] for r in a], [r['mean'] for r in a]))
    Hh = pd.DataFrame(D['hours']); Hh['t'] = pd.to_datetime(Hh.dt); Hh['d'] = Hh.t.dt.normalize()
    Hh = Hh[Hh.status == 'FORECAST'].copy()
    Hh['gas'] = Hh.cc + Hh.sc + Hh.cogen + Hh.gfs
    Hh['cush'] = Hh.gas + Hh.bio + Hh.wind + Hh.solar - (Hh.load - Hh.bcmatl - Hh.sask)
    Hh['ev'] = [look(c, tgroup(he), int(lb or 0)) for c, he, lb in zip(Hh.cush, Hh.he, Hh.get('lb', 0))]
    return Hh, D['meta']

def products(today):
    w = today.dayofweek; P = []
    tm = today + pd.Timedelta(days=1); D1 = pd.Timedelta(days=1)
    if w <= 3:
        P.append(dict(key='next', name=f"Next day {tm:%a %b %d}", days=[tm], tested=True, note=''))
    if w in (3, 4):
        sat = today + pd.Timedelta(days=5 - w)
        P.append(dict(key='wkend', name=f"Weekend {sat:%a %b %d}-{sat+D1:%a %d}", days=[sat, sat + D1], tested=True,
                      note='decided Thursday' if w == 3 else 'decided Friday (sell here only if Thursday did not qualify)'))
    if w == 4:
        sat = tm
        P.append(dict(key='mon', name=f"Monday {sat+2*D1:%b %d} (Friday decision)", days=[sat + 2 * D1], tested=True, note=''))
        P.append(dict(key='satmon', name=f"Sat-Mon {sat:%b %d}-{sat+2*D1:%d}", days=[sat, sat + D1, sat + 2 * D1], tested=True, note=''))
        P.append(dict(key='sat', name=f"Saturday {sat:%b %d}", days=[sat], tested=True, note=''))
        P.append(dict(key='sun', name=f"Sunday {sat+D1:%b %d}", days=[sat + D1], tested=True, note=''))
    if w >= 5:
        pass
    if w <= 2:
        bd = [today + pd.Timedelta(days=k) for k in range(1, 5 - w)]
        P.append(dict(key='bow', name=f"Balance of week {bd[0]:%a %b %d}-{bd[-1]:%a %d}", days=bd, tested=(w == 0),
                      note='' if w == 0 else 'only the Monday (Tue-Fri) balance of week was backtested'))
    nm = today + pd.Timedelta(days=7 - w)
    P.append(dict(key='week', name=f"Wk {nm:%b %d}-{nm+pd.Timedelta(days=4):%d} (Mon-Fri)", days=[nm + pd.Timedelta(days=k) for k in range(5)], tested=True, note=''))
    nm2 = nm + pd.Timedelta(days=7)
    P.append(dict(key='week2', name=f"Wk {nm2:%b %d}-{nm2+pd.Timedelta(days=4):%d} (Mon-Fri)", days=[nm2 + pd.Timedelta(days=k) for k in range(5)], tested=False,
                  note='the week after next - beyond the backtested horizon'))
    return P

def main():
    import sys
    now = pd.Timestamp.now(tz='America/Edmonton').tz_localize(None)
    test = len(sys.argv) > 2 and sys.argv[1] == '--as-of'
    if test: now = pd.Timestamp(sys.argv[2]) + pd.Timedelta(hours=8)   # test only: page for another weekday, nothing logged
    today = now.normalize()
    Hh, meta = load_model()
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
    rows = []
    for p in products(today):
        r = dict(run=str(now)[:16], product=p['name'], key=p['key'], first=str(p['days'][0].date()), last=str(p['days'][-1].date()),
                 hours=24 * len(p['days']), tested=p['tested'], note=p['note'], settle_date=str(sd.date()))
        have = [d for d in p['days'] if d in F.index]
        r['fwd'] = round(float(F.loc[p['days'], 'XDT'].mean()), 2) if len(have) == len(p['days']) else None
        r['fwd_on'] = round(float(F.loc[p['days'], 'XDQ'].mean()), 2) if r['fwd'] is not None and 'XDQ' in F else None
        r['fwd_off'] = round(float(F.loc[p['days'], 'XDP'].mean()), 2) if r['fwd'] is not None and 'XDP' in F else None
        h = Hh[Hh.d.isin(p['days'])]
        r['model_days'] = int(h.d.nunique())
        full = r['model_days'] == len(p['days']) and h.groupby('d').size().min() >= 20
        r['model'] = round(float(h.ev.mean()), 2) if full else None
        r['model_on'] = round(float(h[(h.he >= 8) & (h.he <= 23)].ev.mean()), 2) if full else None
        r['last7'] = round(last7, 2)
        r['gap'] = None if r['fwd'] is None or r['model'] is None else round(r['fwd'] - r['model'], 2)
        bar = 25.0 if p['days'][0].month in (7, 8, 9, 10, 11) else 10.0; r['bar'] = bar
        floor = 35.0 if p['key'] in ('bow', 'week', 'week2') else None; r['floor'] = floor
        # fundamentals
        r['gas_fc'] = round(float(h.gas.mean())) if len(h) else None; r['gas7'] = round(gas7)
        r['wind_fc'] = round(float(h.wind.mean())) if len(h) else None
        r['wind_norm'] = round(wyr * float(np.mean([mf.get(d.month, 1.0) for d in p['days']])))
        r['calm'] = bool(r['wind_fc'] is not None and r['wind_fc'] / wyr < 0.7)
        tmin = tmax = None
        if wx is not None and all(d in wx.index for d in p['days']):
            tmin = float(wx.loc[p['days'], 'min'].min()); tmax = float(wx.loc[p['days'], 'max'].max())
        r['tmin'] = tmin; r['tmax'] = tmax
        r['extreme'] = bool(tmin is not None and (tmax >= 28 or tmin <= -20))
        r['temp_known'] = tmin is not None
        checks = []
        def ck(name, ok, detail): checks.append((name, ok, detail)); return ok
        g = r['gap']
        ok1 = ck('Gap over the bar', g is not None and g > bar, 'no price or no model for every day' if g is None else f"forward ${r['fwd']:.2f} - model ${r['model']:.2f} = {g:+.2f} vs bar ${bar:.0f}")
        if p['key'] == 'wkend':
            ok2 = ck("Forward at or above last week's pool", True, 'not used for weekends (weekday yardstick)')
        else:
            ok2 = ck("Forward at or above last week's pool", r['fwd'] is not None and r['fwd'] >= last7, f"${r['fwd'] if r['fwd'] is not None else float('nan'):.2f} vs ${last7:.2f}")
        ok3 = ck('Not calm AND extreme', not (r['calm'] and r['extreme']), f"wind {r['wind_fc']} MW ({'calm' if r['calm'] else 'not calm'}), temps {('%.0f' % tmin) if tmin is not None else '?'} to {('%.0f' % tmax) if tmax is not None else '?'} C")
        ok4 = ck('Forward at or above $35', floor is None or (r['fwd'] is not None and r['fwd'] >= floor), 'not required for this product' if floor is None else f"${r['fwd'] if r['fwd'] is not None else float('nan'):.2f}")
        near = p['key'] in ('next', 'bow', 'wkend', 'mon', 'satmon', 'sat', 'sun')
        pts = [('Gap over $20', g is not None and g > 20),
               ('No weather warning', (not r['calm']) and (not r['extreme']) and r['temp_known']),
               (('Gas forecast >= last 7 days' if near else 'Wind forecast >= normal'),
                (r['gas_fc'] is not None and r['gas_fc'] >= gas7) if near else (r['wind_fc'] is not None and r['wind_fc'] >= r['wind_norm']))]
        r['points'] = sum(1 for _, v in pts if v)
        lo, hi = SIZES.get(p['key'], (0, 0))
        r['size_mw'] = (hi if r['points'] >= 3 else lo) if p['key'] in SIZES else 0
        r['backtest'] = BT.get(p['key'], '')
        passes = ok1 and ok2 and ok3 and ok4
        if p['key'] in NEVER: status = 'DO NOT SELL'
        elif p['key'] in INFO: status = 'INFO ONLY' if p['key'] != 'week2' else 'NO READ YET' if r['model'] is None else 'INFO ONLY'
        elif passes and p['tested'] and r['size_mw']: status = 'SELL'
        elif passes: status = 'POSSIBLE - NOT BACKTESTED'
        elif g is not None and g > bar - 5: status = 'WATCH'
        else: status = 'PASS'
        r['status'] = status
        lvl = [x for x in ((r['model'] + bar) if r['model'] is not None else None, None if p['key'] == 'wkend' else last7, floor) if x is not None]
        r['offer_at'] = round(max(lvl), 2) if lvl else None
        r['worst_case_usd'] = round(r['size_mw'] * r['hours'] * STRESS) if r['size_mw'] else None
        r['_checks'] = checks; r['_pts'] = pts
        rows.append(r)
    global OUT
    if test: OUT = HERE / 'docs' / f'trades_test_{today:%a}.html'
    write_page(rows, now, sd, meta, th, th_ok, last7)
    if not test: log(rows, pool)
    for r in rows:
        print(f"  {r['product']:<34} {r['status']:<26} fwd {r['fwd']}  model {r['model']}  gap {r['gap']}  size {r['size_mw']} MW")
    print(f"  page: docs/trades.html   log: verify/possible_trades.csv")

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

def money(x): return '—' if x is None or (isinstance(x, float) and np.isnan(x)) else f'${x:,.2f}'
def write_page(rows, now, sd, meta, th, th_ok, last7):
    col = {'SELL': '#1a7f37', 'WATCH': '#9a6700', 'PASS': '#6e7781', 'POSSIBLE - NOT BACKTESTED': '#8250df', 'DO NOT SELL': '#cf222e', 'INFO ONLY': '#57606a', 'NO READ YET': '#57606a'}
    cards = []
    for r in rows:
        chk = ''.join(f"<li class='{'ok' if ok else 'no'}'>{'✓' if ok else '✗'} <b>{H.escape(n)}</b> <span>{H.escape(d)}</span></li>" for n, ok, d in r['_checks'])
        pts = ''.join(f"<li class='{'ok' if ok else 'no'}'>{'●' if ok else '○'} {H.escape(n)}</li>" for n, ok in r['_pts'])
        size = (f"{'' if r['status']=='SELL' else 'If it qualified: '}<b>{r['size_mw']} MW</b> suggested ({r['points']} of 3 conviction points) · stress loss at -$100/MWh: ${r['worst_case_usd']:,.0f} · {WORST.get(r['key'],'')}"
                if r['size_mw'] else ('not sold - see the backtest line above' if r['key'] in NEVER else 'no size - information only'))
        where = ''
        if r['status'] in ('SELL', 'WATCH') and r['offer_at'] is not None:
            if r['fwd'] is not None and r['fwd'] >= r['offer_at']:
                where = f"Last settle {money(r['fwd'])} is already above the required level {money(r['offer_at'])}: offer at or near the settle, no lower than {money(r['offer_at'])}."
            else:
                where = f"Rest an offer at {money(r['offer_at'])} or better (model + ${r['bar']:.0f}, last week's pool {money(last7)}{', $35 floor' if r['floor'] else ''}); last settle {money(r['fwd'])}."
        note = (f"<p class='note'>{H.escape(r['note'])}</p>" if r['note'] else '') + (f"<p class='bt'>{H.escape(r['backtest'])}</p>" if r['backtest'] else '')
        cards.append(f"""<section class='card'><div class='head'><h2>{H.escape(r['product'])}</h2><span class='st' style='background:{col.get(r['status'],'#555')}'>{r['status']}</span></div>
<div class='nums'><div><small>Forward (flat)</small><b>{money(r['fwd'])}</b><small>on {money(r['fwd_on'])} · off {money(r['fwd_off'])}</small></div>
<div><small>Model fair value</small><b>{money(r['model'])}</b><small>on-peak {money(r['model_on'])}</small></div>
<div><small>Gap</small><b>{'—' if r['gap'] is None else f"{r['gap']:+.2f}"}</b><small>bar ${r['bar']:.0f}</small></div>
<div><small>Last 7 days' pool</small><b>{money(r['last7'])}</b><small>{r['hours']} hours</small></div></div>
{note}<h3>Must pass</h3><ul>{chk}</ul><h3>Conviction</h3><ul class='pts'>{pts}</ul><p class='size'>{size}</p>{f"<p class='where'>{H.escape(where)}</p>" if where else ''}</section>""")
    warn = '' if th_ok else "<p class='warn'>Forward thermal is NOT from the corrected AESO outlook today (pull failed or stale) - the model is on the gencap fallback. Check logs/pull_daily_inputs.log.</p>"
    page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Possible Trades</title><style>
:root{{--bg:#f6f8fa;--fg:#1f2328;--card:#fff;--mut:#59636e;--line:#d1d9e0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0d1117;--fg:#e6edf3;--card:#161b22;--mut:#9198a1;--line:#30363d}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,Segoe UI,Arial,sans-serif}}
main{{max-width:980px;margin:auto;padding:16px}} h1{{margin:.2em 0}} .meta{{color:var(--mut);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}}
.head{{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}} h2{{font-size:18px;margin:0}}
.st{{color:#fff;border-radius:6px;padding:3px 10px;font-size:13px;font-weight:600}}
.nums{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:12px 0}} .nums div{{display:flex;flex-direction:column}}
.nums b{{font-size:20px}} small{{color:var(--mut);font-size:12px}} h3{{font-size:13px;color:var(--mut);margin:10px 0 4px;text-transform:uppercase;letter-spacing:.04em}}
ul{{list-style:none;padding:0;margin:0}} li{{padding:2px 0}} li span{{color:var(--mut);font-size:13px}} li.ok{{color:#1a7f37}} li.no{{color:#cf222e}}
li.ok b,li.no b{{color:var(--fg);font-weight:600}} .pts li.no{{color:var(--mut)}} .size,.where{{margin:8px 0 0}} .note{{color:#8250df;font-size:13px}}
.bt{{color:var(--mut);font-size:13px;margin:4px 0}} .warn{{background:#cf222e;color:#fff;padding:8px 12px;border-radius:8px}} .rules{{color:var(--mut);font-size:13px}}
</style></head><body><main><h1>Possible Trades</h1>
<p class='meta'>Built {now:%Y-%m-%d %H:%M} · forward settles of {sd:%Y-%m-%d} (ICE daily XDT/XDQ/XDP, Warehouse) · model page built {meta.get('bundle_built','?')} · thermal: {H.escape(th.get('source','unknown'))} {('(vintage '+th.get('vintage','')+')') if th else ''}</p>{warn}
{''.join(cards)}
<p class='rules'>Rules, backtested on the live model replayed every morning Apr 2025 - Sep 2026: sell when the gap is over $10 ($25 for Jul-Nov delivery), the forward is at or above last week's average pool price, the delivery days are not both calm and extreme-temperature, and (week and balance of week) the forward is at least $35. Sizes: next day 10/20 MW, balance of week 5/10 MW, next week 5 MW; the larger size only on 3 of 3 conviction points. Result: 70 trades, 81% won, +$429k, worst drawdown -$31k. Weekend Sat-Sun (Thursday, or Friday if Thursday did not qualify): gap and weather only, 5/10 MW - 16 weekends, 69% won, +$47k, book drawdown unchanged. Friday-for-Monday and Sat-Mon lost in the backtest and are never sold. Fill assumed at the settle less $2.50; hold to settlement, no stop.</p>
</main></body></html>"""
    OUT.write_text(page, encoding='utf-8')

if __name__ == '__main__':
    main()
