"""
find_contracts.py -- READ-ONLY look through the Warehouse (SQL Server) and
CANPOWER (PostgreSQL) databases for anything that defines the Alberta power
contracts you can trade (product / contract / instrument / broker tables,
exchange-code descriptions). Writes what it finds to cache/contract_discovery.txt.
Changes nothing. Uses db.json and update.py's connection helpers; prints no credentials.

    python find_contracts.py
"""
import json, sys
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
import update as u

PAT = ['product','contract','instrument','exchange','code','forward','broker','quote',
       'trade','deal','curve','strip','mapping','lookup','ice','ngx','settle','term','period']
AB_CODES = "('XDP','XDQ','XDT','XDU','XDV','XDW','XCU','XCV','XCW','XCX','XCY','XCZ')"
out = []
def w(s=''): print(s, flush=True); out.append(str(s))

cfg = json.loads((HERE / 'db.json').read_text())
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 400); pd.set_option('display.max_columns', 40)

# ---------- SQL Server (Warehouse) ----------
try:
    cn, _, _ = u._connect(cfg, 'forwards')
    with cn:
        like = ' OR '.join(f"LOWER(TABLE_NAME) LIKE '%{p}%'" for p in PAT)
        t = pd.read_sql(f"SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM Warehouse.INFORMATION_SCHEMA.TABLES WHERE {like} ORDER BY TABLE_NAME", cn)
        w('=== Warehouse tables/views whose names suggest products or contracts ==='); w(t.to_string(index=False))
        c = pd.read_sql("SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME FROM Warehouse.INFORMATION_SCHEMA.COLUMNS "
                        "WHERE LOWER(COLUMN_NAME) IN ('exchangecode','productcode','contractcode','productname','contractname','description','instrument','productid','contractid') ORDER BY TABLE_NAME", cn)
        w('\n=== Warehouse columns that look like product keys / names ==='); w(c.to_string(index=False))
        w('\n=== ForwardPrices: Alberta codes ===')
        w(pd.read_sql("SELECT ExchangeCode, CommodityName, MonthlyDaily, NodeName, COUNT(*) AS n, MIN(EffectiveDate) AS first_settle, MAX(EffectiveDate) AS last_settle "
                      "FROM Warehouse.dbo.ForwardPrices WITH (NOLOCK) WHERE NodeName LIKE '%AESO%' OR NodeName LIKE '%Alberta%' "
                      "GROUP BY ExchangeCode, CommodityName, MonthlyDaily, NodeName ORDER BY ExchangeCode", cn).to_string(index=False))
        cols = pd.read_sql("SELECT TOP 0 * FROM Warehouse.dbo.ForwardPrices", cn).columns.tolist()
        w('\nForwardPrices columns: ' + ', '.join(cols))
        for _, r in c[c.COLUMN_NAME.str.lower() == 'exchangecode'].iterrows():
            if r.TABLE_NAME.lower() == 'forwardprices': continue
            try:
                s = pd.read_sql(f"SELECT TOP 40 * FROM Warehouse.{r.TABLE_SCHEMA}.{r.TABLE_NAME} WITH (NOLOCK) WHERE ExchangeCode IN {AB_CODES}", cn)
                w(f'\n=== {r.TABLE_SCHEMA}.{r.TABLE_NAME}: rows for the Alberta codes ==='); w(s.to_string(index=False))
            except Exception as e:
                w(f'  {r.TABLE_NAME}: could not read ({str(e).splitlines()[0][:120]})')
except Exception as e:
    w(f'Warehouse search FAILED: {str(e).splitlines()[0][:160]}')

# ---------- PostgreSQL (CANPOWER) ----------
try:
    cc = u.comp_cfg(cfg); cn, _, _ = u._connect(cc, 'composition')
    with cn:
        like = ' OR '.join(f"table_name ILIKE '%{p}%'" for p in PAT)
        t = pd.read_sql(f"SELECT table_schema, table_name FROM information_schema.tables WHERE ({like}) "
                        "AND table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1,2", cn)
        w('\n=== CANPOWER tables whose names suggest products, trades or quotes ==='); w(t.to_string(index=False))
except Exception as e:
    w(f'CANPOWER search FAILED: {str(e).splitlines()[0][:160]}')

(HERE / 'cache' / 'contract_discovery.txt').write_text('\n'.join(out), encoding='utf-8')
print('\nwritten to cache/contract_discovery.txt')
