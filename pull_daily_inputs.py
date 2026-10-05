"""
pull_daily_inputs.py -- the two feeds the 14-day view and the weekly signal need,
pulled every morning BEFORE the model rebuilds. Each part is independent: if one
fails it says so and the model falls back exactly as before.

 1. Forward thermal availability (CANPOWER / canpower.aeso_fundamentals_snapshots)
      cache/fwd_thermal.csv     the LATEST forward snapshot of cogen / CC / gas
                                steam / simple cycle for every hour still ahead.
                                The page uses it in place of AESO gencap, which
                                runs ~100 MW + ~75 MW per day ahead too high.
      cache/forward_supply.csv  one row per (render day, target day): the forward
                                view as it stood each day. Appended daily, so the
                                switch can keep being checked against outturn.

 2. Alberta daily forwards (Warehouse.dbo.ForwardPrices, node AESO SMP, daily
    codes XDP/XDQ/XDT/XDU/XDV/XDW) appended to cache/fwd_aeso_daily.csv. This file
    had stopped updating on 2026-09-21; the weekly signal reads it.

    python pull_daily_inputs.py
    python pull_daily_inputs.py --history-days 60   # one-off backfill of the forward-thermal history
Reuses db.json and update.py's connection helpers; no credentials here.
"""
import json, sys, time
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'cache'; CACHE.mkdir(exist_ok=True)
import update as u

def say(m): print(f"  {m}", flush=True)

FWD_THERMAL = """
SELECT DISTINCT ON (datetime_begin)
       datetime_begin, render_time, sc, cogen, cc, gfs, biomass_and_other
FROM {TABLE}
WHERE datetime_begin >= NOW() - INTERVAL '1 day'
  AND datetime_begin <  NOW() + INTERVAL '16 days'
  AND render_time    >= NOW() - INTERVAL '2 days'
  AND render_time    <  datetime_begin
ORDER BY datetime_begin, render_time DESC
"""

FWD_SUPPLY = """
WITH snaps AS (
    SELECT date_trunc('day', render_time) AS render_day,
           date_trunc('day', datetime_begin) AS target_day,
           date_trunc('hour', datetime_begin) AS hr,
           render_time, sc, cogen, cc, gfs
    FROM {TABLE}
    WHERE render_time >= NOW() - INTERVAL '{DAYS} days'
      AND render_time < datetime_begin
      AND datetime_begin < NOW() + INTERVAL '16 days'
), last_per_day AS (
    SELECT DISTINCT ON (render_day, hr) *
    FROM snaps ORDER BY render_day, hr, render_time DESC
)
SELECT render_day, target_day, COUNT(*) AS hours,
       AVG(sc) AS sc, AVG(cogen) AS cogen, AVG(cc) AS cc, AVG(gfs) AS gfs
FROM last_per_day GROUP BY render_day, target_day HAVING COUNT(*) >= 20
ORDER BY render_day, target_day
"""

FWD_PRICES = """
SELECT EffectiveDate, Strip, ExchangeCode, CommodityName, MonthlyDaily, NodeName, Price
FROM   Warehouse.dbo.ForwardPrices WITH (NOLOCK)
WHERE  EffectiveDate >= DATEADD(day, -{DAYS}, GETDATE())
  AND  Price IS NOT NULL
  AND  MonthlyDaily = 'D'
  AND  NodeName = 'AESO SMP'
  AND  ExchangeCode IN ('XDP','XDQ','XDT','XDU','XDV','XDW')
ORDER BY EffectiveDate, Strip
"""

def merge_csv(path, new, keys):
    if path.exists():
        old = pd.read_csv(path)
        for k in keys:
            old[k] = old[k].astype(str).str[:10] if 'Date' in k or 'day' in k or k == 'Strip' else old[k]
            new[k] = new[k].astype(str).str[:10] if 'Date' in k or 'day' in k or k == 'Strip' else new[k]
        out = pd.concat([old, new], ignore_index=True).drop_duplicates(keys, keep='last')
    else:
        out = new
    tmp = path.with_suffix('.csv.tmp'); out.to_csv(tmp, index=False); tmp.replace(path)
    return len(out)

def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument('--history-days', type=int, default=None)
    a, _ = ap.parse_known_args()
    cfg = json.loads((HERE / 'db.json').read_text())
    ok = True
    # ---- 1. forward thermal (PostgreSQL) ----
    try:
        cc = u.comp_cfg(cfg); tbl = cc.get('composition_table') or 'canpower.aeso_fundamentals_snapshots'
        cn, _, _ = u._connect(cc, 'composition')
        with cn:
            t0 = time.time()
            df = pd.read_sql(FWD_THERMAL.replace('{TABLE}', tbl), cn)
            df.columns = [c.lower() for c in df.columns]
            if len(df) < 24:
                raise RuntimeError(f'only {len(df)} forward hours returned')
            tmp = CACHE / 'fwd_thermal.csv.tmp'; df.to_csv(tmp, index=False); tmp.replace(CACHE / 'fwd_thermal.csv')
            say(f"fwd_thermal   {len(df):>6} hours  {df.datetime_begin.min()} to {df.datetime_begin.max()}, "
                f"latest render {df.render_time.max()}  ({time.time()-t0:.0f}s)")
            last = CACHE / 'forward_supply.csv'
            days = a.history_days or (3 if last.exists() else 60)
            h = pd.read_sql(FWD_SUPPLY.replace('{TABLE}', tbl).replace('{DAYS}', str(days)), cn)
            h.columns = [c.lower() for c in h.columns]
            n = merge_csv(last, h, ['render_day', 'target_day'])
            say(f"forward_supply {len(h):>5} new rows ({n:,} kept)")
    except Exception as e:
        ok = False
        say(f"forward thermal FAILED ({str(e).splitlines()[0][:140]}) - the page falls back to AESO gencap with the haircut")
    # ---- 2. Alberta daily forwards (SQL Server) ----
    try:
        p = CACHE / 'fwd_aeso_daily.csv'
        days = 30
        if p.exists():
            lastd = pd.to_datetime(pd.read_csv(p, usecols=['EffectiveDate']).EffectiveDate).max()
            days = max(10, int((pd.Timestamp.now() - lastd).days) + 5)
        cn, _, _ = u._connect(cfg, 'forwards')
        with cn:
            t0 = time.time()
            df = pd.read_sql(FWD_PRICES.replace('{DAYS}', str(days)), cn)
        if not len(df): raise RuntimeError('no rows returned')
        n = merge_csv(p, df, ['EffectiveDate', 'Strip', 'ExchangeCode'])
        say(f"fwd_aeso_daily {len(df):>6} rows pulled, latest settle {str(df.EffectiveDate.max())[:10]} ({n:,} kept, {time.time()-t0:.0f}s)")
    except Exception as e:
        ok = False
        say(f"daily forwards FAILED ({str(e).splitlines()[0][:140]}) - the weekly signal will use the last settle on file")
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main())
