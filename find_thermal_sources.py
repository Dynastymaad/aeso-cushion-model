"""
find_thermal_sources.py -- READ-ONLY. Looks for any table that holds FORWARD
generation capability / outages (what thermal will be available on days 1-14),
since canpower.aeso_fundamentals_snapshots only reaches ~1-2 hours ahead.
For each candidate it reports row count, date range and - if it has a target
date and a publish date - how far ahead it reaches. Writes cache/thermal_sources.txt.

    python find_thermal_sources.py
"""
import json, time, traceback
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
import update as u
out = []
def w(s=''): print(s, flush=True); out.append(str(s))
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 300); pd.set_option('display.max_columns', 30)
PAT = ['outage', 'capab', 'gencap', 'avail', 'supply', 'derate', 'generation', 'unit', 'asset', 'mcr', 'adequacy', 'fundamental', 'forecast']
cfg = json.loads((HERE / 'db.json').read_text())

def scan(cn, label, tables_sql, cols_sql, qual):
    t = pd.read_sql(tables_sql, cn); t.columns = [c.lower() for c in t.columns]
    w(f'\n=== {label}: {len(t)} candidate tables ==='); w(t.to_string(index=False))
    cols = pd.read_sql(cols_sql, cn); cols.columns = [c.lower() for c in cols.columns]
    for _, r in t.iterrows():
        name = qual(r); c = cols[(cols.table_schema == r.table_schema) & (cols.table_name == r.table_name)]
        tcols = c[c.data_type.str.contains('date|time', case=False)].column_name.tolist()
        w(f'\n--- {name}'); w('   columns: ' + ', '.join(c.column_name.tolist()[:40]))
        try:
            n = pd.read_sql(f'SELECT COUNT(*) AS n FROM {name}', cn).iloc[0, 0]; w(f'   rows: {n:,}')
            for tc in tcols[:4]:
                q = f'SELECT MIN({tc}) AS lo, MAX({tc}) AS hi FROM {name}'
                v = pd.read_sql(q, cn).iloc[0]; w(f'   {tc}: {v.lo} -> {v.hi}')
        except Exception as e:
            w(f'   could not read: {str(e).splitlines()[0][:150]}')

try:
    cn, _, _ = u._connect(u.comp_cfg(cfg), 'composition')
    with cn:
        like = ' OR '.join(f"table_name ILIKE '%{p}%'" for p in PAT) + " OR table_name ILIKE 'aeso%'"
        scan(cn, 'CANPOWER (PostgreSQL)',
             f"SELECT table_schema, table_name FROM information_schema.tables WHERE ({like}) AND table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1,2",
             "SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog','information_schema')",
             lambda r: f'{r.table_schema}.{r.table_name}')
except BaseException:
    w('CANPOWER scan FAILED'); w(traceback.format_exc())
try:
    cn, _, _ = u._connect(cfg, 'forwards')
    with cn:
        like = ' OR '.join(f"LOWER(TABLE_NAME) LIKE '%{p}%'" for p in PAT)
        scan(cn, 'Warehouse (SQL Server)',
             f"SELECT TABLE_SCHEMA AS table_schema, TABLE_NAME AS table_name FROM Warehouse.INFORMATION_SCHEMA.TABLES WHERE {like} ORDER BY 2",
             "SELECT TABLE_SCHEMA AS table_schema, TABLE_NAME AS table_name, COLUMN_NAME AS column_name, DATA_TYPE AS data_type FROM Warehouse.INFORMATION_SCHEMA.COLUMNS",
             lambda r: f'Warehouse.{r.table_schema}.{r.table_name}')
except BaseException:
    w('Warehouse scan FAILED'); w(traceback.format_exc())
(HERE / 'cache' / 'thermal_sources.txt').write_text('\n'.join(out), encoding='utf-8')
print('\nwritten to cache/thermal_sources.txt')
