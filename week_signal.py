"""
week_signal.py -- the days 8-13 weekly signal, logged every morning AFTER the
page is rebuilt.

  block    the six delivery days 8..13 ahead (Calgary date)
  forward  average of the XDT (flat) daily settles for those six days, latest
           settle on file (cache/fwd_aeso_daily.csv, refreshed by
           pull_daily_inputs.py). These days are normally marked off one
           next-week / balance-of-month price, so this is in practice that
           product's price.
  model    the page's fair value (EV) for the same hours, averaged
  gap      forward - model.  > $10 = SELL signal (backtest Jan-Sep 2026: 18 of 32
           weeks, +$13.40/MWh after a $2.50 fill cost, 89% won);  > $20 = strong.
           < -$10 = the market is BELOW the model: do not sell that week.

Appends one row per morning to verify/week_signal.csv and, once a logged block
has fully settled, fills in what selling it at (forward - $2.50) would have made.
That log is how the thresholds get re-checked on the live model.

    python week_signal.py
"""
import json, re, sys
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
LOG = HERE / 'verify' / 'week_signal.csv'
FILL_COST = 2.5

def tgroup(he): return 0 if he <= 6 else 1 if he <= 10 else 2 if he <= 16 else 3 if he <= 21 else 4

def main():
    today = pd.Timestamp.now(tz='America/Edmonton').normalize().tz_localize(None)
    block = [today + pd.Timedelta(days=k) for k in range(8, 14)]
    row = {'run': str(pd.Timestamp.now())[:16], 'block_start': str(block[0].date()), 'block_end': str(block[-1].date())}
    # ---- model fair value from the page
    html = (HERE / 'docs' / 'index.html').read_text(encoding='utf-8')
    D = json.loads(re.search(r'^const D = (\{.*\});\s*$', html, re.M).group(1))
    G = {}
    for r in D['grid']: G.setdefault((r['g'], r.get('L', 0) or 0), []).append(r)
    for k in G: G[k].sort(key=lambda r: r['c'])
    def look(c, g, L, key):
        a = G.get((g, L)) or G[(g, 0)]; xs = [r['c'] for r in a]; ys = [r[key] for r in a]
        return float(np.interp(c, xs, ys))
    H = pd.DataFrame(D['hours']); H['d'] = pd.to_datetime(H.dt.str[:10])
    H = H[H.d.isin(block) & (H.status == 'FORECAST')].copy()
    if len(H):
        H['cush'] = H.cc + H.sc + H.cogen + H.gfs + H.bio + H.wind + H.solar - (H.load - H.bcmatl - H.sask)
        H['ev'] = [look(c, tgroup(he), int(lb or 0), 'mean') for c, he, lb in zip(H.cush, H.he, H.get('lb', 0))]
        H['p50'] = [look(c, tgroup(he), int(lb or 0), 'q50') for c, he, lb in zip(H.cush, H.he, H.get('lb', 0))]
        dd = H.groupby('d').agg(n=('ev', 'size'), ev=('ev', 'mean'), p50=('p50', 'mean'))
        dd = dd[dd.n >= 20]
    else:
        dd = pd.DataFrame()
    row['model_days'] = len(dd); row['page_built'] = D['meta'].get('bundle_built')
    row['model_ev'] = round(float(dd.ev.mean()), 2) if len(dd) else None
    row['model_p50'] = round(float(dd.p50.mean()), 2) if len(dd) else None
    # ---- forward
    fp = HERE / 'cache' / 'fwd_aeso_daily.csv'
    f = pd.read_csv(fp, parse_dates=['EffectiveDate', 'Strip']); f = f[f.ExchangeCode == 'XDT']
    f = f[f.EffectiveDate <= today]
    last = f.EffectiveDate.max(); g = f[(f.EffectiveDate == last) & f.Strip.isin(block)]
    row['fwd_date'] = str(last.date()); row['fwd_days'] = int(g.Strip.nunique())
    row['fwd'] = round(float(g.Price.mean()), 2) if len(g) else None
    row['fwd_distinct_prices'] = int(g.Price.nunique()) if len(g) else 0
    row['fwd_age_days'] = int((today - last).days)
    # ---- signal
    gap = None if row['fwd'] is None or row['model_ev'] is None else round(row['fwd'] - row['model_ev'], 2)
    row['gap'] = gap
    if gap is None: sig = 'NO DATA'
    elif row['fwd_age_days'] > 4: sig = 'STALE FORWARD'
    elif row['model_days'] < 5: sig = 'MODEL SHORT'
    elif gap > 20: sig = 'SELL (strong)'
    elif gap > 10: sig = 'SELL'
    elif gap < -10: sig = 'DO NOT SELL'
    else: sig = 'no trade'
    row['signal'] = sig
    # ---- append, then settle older rows
    LOG.parent.mkdir(exist_ok=True)
    L = pd.concat([pd.read_csv(LOG), pd.DataFrame([row])], ignore_index=True) if LOG.exists() else pd.DataFrame([row])
    L = L.drop_duplicates(['run'], keep='last')
    ph = HERE / 'model' / 'price_history.csv'
    if ph.exists():
        px = pd.read_csv(ph, index_col=0, parse_dates=True).iloc[:, 0]
        for i, r in L.iterrows():
            if pd.notna(r.get('settle', np.nan)) or pd.isna(r.get('fwd', np.nan)): continue
            s, e = pd.Timestamp(r.block_start), pd.Timestamp(r.block_end) + pd.Timedelta(hours=23)
            v = px[s:e]
            if len(v) >= 6 * 24 - 4:
                L.at[i, 'settle'] = round(float(v.mean()), 2)
                L.at[i, 'pnl_if_sold'] = round(float(r.fwd) - FILL_COST - float(v.mean()), 2)
    tmp = LOG.with_suffix('.csv.tmp'); L.to_csv(tmp, index=False); tmp.replace(LOG)
    print(f"  days 8-13 ({row['block_start']} to {row['block_end']}): forward ${row['fwd']} (settle {row['fwd_date']}), "
          f"model fair value ${row['model_ev']} over {row['model_days']} days, gap {gap}  ->  {sig}")
    done = L.dropna(subset=['pnl_if_sold']) if 'pnl_if_sold' in L else pd.DataFrame()
    if len(done):
        s = done[done.signal.astype(str).str.startswith('SELL')]
        print(f"  log: {len(L)} mornings, {len(done)} settled; signalled sells {len(s)}, avg {s.pnl_if_sold.mean() if len(s) else float('nan'):+.2f}/MWh after ${FILL_COST} cost")
    return 0

if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as e:
        print(f"  week signal failed ({type(e).__name__}: {e})"); sys.exit(1)
