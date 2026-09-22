"""
pull_fwd2.py -- pull the DAILY and BALANCE-OF-MONTH forward curves.

ForwardPrices carries both granularities (MonthlyDaily = 'M' / 'D') and
balance-of-month sits under ExchangeCode 'XDT'. The monthly XCU pull we
already have is too coarse to show a weather reaction; these are not.

Reuses db.json and update.py's own conn_str(), so nothing is duplicated and
no credentials live in this file.

Run from the repo folder:
    python pull_fwd2.py                 # inventory, then pull
    python pull_fwd2.py --inventory     # just show what's in there, pull nothing
    python pull_fwd2.py --days 1200     # deeper history (default 800)

Writes: cache/fwd_inventory.csv, cache/fwd_daily.csv, cache/fwd_bom.csv
"""
import argparse, json, time
from pathlib import Path
import pandas as pd, pyodbc

import update as u                       # reuse conn_str, no duplication

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'cache'; CACHE.mkdir(exist_ok=True)


def connect():
    cfg = json.loads((HERE / 'db.json').read_text())
    return pyodbc.connect(u.conn_str(cfg), timeout=120)


def q(cn, sql, label):
    t0 = time.time()
    df = pd.read_sql(sql, cn)
    print(f'  {label}: {len(df):,} rows in {time.time() - t0:.0f}s')
    return df


INVENTORY = """
SELECT   ExchangeCode, CommodityName, MonthlyDaily, NodeName,
         COUNT(*)              AS rows_,
         COUNT(DISTINCT Strip) AS strips,
         MIN(EffectiveDate)    AS first_date,
         MAX(EffectiveDate)    AS last_date,
         MIN(Strip)            AS first_strip,
         MAX(Strip)            AS last_strip
FROM     Warehouse.dbo.ForwardPrices WITH (NOLOCK)
WHERE    EffectiveDate >= DATEADD(day, -{days}, GETDATE())
  AND    Price IS NOT NULL
GROUP BY ExchangeCode, CommodityName, MonthlyDaily, NodeName
ORDER BY rows_ DESC
"""

PULL = """
SELECT   EffectiveDate, Strip, ExchangeCode, CommodityName,
         MonthlyDaily, NodeName, Price
FROM     Warehouse.dbo.ForwardPrices WITH (NOLOCK)
WHERE    EffectiveDate >= DATEADD(day, -{days}, GETDATE())
  AND    Price IS NOT NULL
  AND    {where}
ORDER BY EffectiveDate, Strip
"""

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=800)
    ap.add_argument('--inventory', action='store_true')
    a = ap.parse_args()

    cn = connect()
    print(f'inventory over the last {a.days} days (19 GB table, give it a minute)...')
    inv = q(cn, INVENTORY.format(days=a.days), 'inventory')
    inv.to_csv(CACHE / 'fwd_inventory.csv', index=False)

    pd.set_option('display.width', 220, 'display.max_rows', 80)
    print('\n' + '=' * 100)
    print('WHAT IS IN THE TABLE  (also saved to cache/fwd_inventory.csv)')
    print('=' * 100)
    print(inv.head(60).to_string(index=False))

    print('\nBy granularity:')
    print(inv.groupby('MonthlyDaily').rows_.sum().to_string())
    print('\nBy exchange:')
    print(inv.groupby('ExchangeCode').agg(rows_=('rows_', 'sum'),
                                          nodes=('NodeName', 'nunique')).to_string())

    if a.inventory:
        cn.close(); raise SystemExit('\ninventory only - nothing pulled.')

    print('\npulling DAILY strips (MonthlyDaily = D)...')
    d = q(cn, PULL.format(days=a.days, where="MonthlyDaily = 'D'"), 'daily')
    d.to_csv(CACHE / 'fwd_daily.csv', index=False)

    print("pulling BALANCE-OF-MONTH (ExchangeCode = 'XDT')...")
    b = q(cn, PULL.format(days=a.days, where="ExchangeCode = 'XDT'"), 'bal-mo')
    b.to_csv(CACHE / 'fwd_bom.csv', index=False)

    cn.close()
    for nm, df in [('fwd_daily.csv', d), ('fwd_bom.csv', b)]:
        if len(df):
            print(f'\n{nm}: {len(df):,} rows, {df.EffectiveDate.min()} -> '
                  f'{df.EffectiveDate.max()}, {df.NodeName.nunique()} node(s)')
            print(df.head(3).to_string(index=False))
        else:
            print(f'\n{nm}: EMPTY - check the inventory table above for the right filter')
