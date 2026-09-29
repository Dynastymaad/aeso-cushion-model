"""
probe_forward_supply.py — does the Postgres fundamentals table hold a FORWARD
view of thermal availability, and is it any good?

    python probe_forward_supply.py            10-second answer
    python probe_forward_supply.py --pull     also write cache/forward_supply.csv

Reuses update.py's connection code and db.json (composition_db block). Prints:
  1. how far forward the snapshots reach (rows per lead-day);
  2. at each lead, the forward thermal figure against the lead_bucket=-1 actual
     for the same day: MAE, bias, and the same for just-carry-today;
  3. whether the forward rows are back-filled copies of the actual (useless)
     or a genuine earlier view (useful).
If (2) shows a consistent positive bias that grows with lead, that is the
+294 MW gas-availability bias the forecast journal measured, and it is the
haircut multiday.py needs.
"""
import argparse, json, sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import update as U          # connection helpers only; nothing runs on import

SQL_COVER = """
SELECT FLOOR(EXTRACT(EPOCH FROM (datetime_begin - render_time))/86400) AS lead_days,
       COUNT(*) AS rows_, MIN(render_time) AS first_render, MAX(render_time) AS last_render
FROM canpower.aeso_fundamentals_snapshots
WHERE datetime_begin >= NOW() - INTERVAL '400 days'
GROUP BY 1 ORDER BY 1;
"""

SQL_DAILY = """
WITH s AS (
  SELECT date_trunc('day', render_time)    AS render_day,
         date_trunc('day', datetime_begin) AS target_day,
         datetime_begin, render_time,
         COALESCE(sc,0)+COALESCE(cogen,0)+COALESCE(cc,0)+COALESCE(gfs,0) AS thermal,
         ail, wind, net_imports
  FROM canpower.aeso_fundamentals_snapshots
  WHERE datetime_begin >= NOW() - INTERVAL '400 days'
    AND render_time <= datetime_begin + INTERVAL '2 hours'
),
lastsnap AS (
  SELECT DISTINCT ON (render_day, datetime_begin) *
  FROM s ORDER BY render_day, datetime_begin, render_time DESC
)
SELECT render_day, target_day,
       FLOOR(EXTRACT(EPOCH FROM (MIN(datetime_begin) - MAX(render_time)))/86400) AS lead_days,
       COUNT(*) AS hours, AVG(thermal) AS thermal, AVG(ail) AS ail, AVG(wind) AS wind,
       AVG(net_imports) AS net_imports
FROM lastsnap GROUP BY render_day, target_day HAVING COUNT(*) >= 20
ORDER BY render_day, target_day;
"""


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--pull', action='store_true'); a = ap.parse_args()
    cfg = U.comp_cfg(json.loads((ROOT/'db.json').read_text()))
    cn = U._connect(cfg, 'composition')
    cov = pd.read_sql(SQL_COVER, cn)
    print('\n1. rows per lead-day (how far forward the table looks):')
    print(cov.to_string(index=False))
    if cov.lead_days.max() < 1:
        print('\n   -> no forward rows at all. The table only has the post-hour snapshot. Stop here.')
        return
    D = pd.read_sql(SQL_DAILY, cn)
    D['render_day'] = pd.to_datetime(D.render_day); D['target_day'] = pd.to_datetime(D.target_day)
    act = D[D.lead_days <= 0].groupby('target_day').thermal.last()
    print(f'\n2. forward thermal availability vs the same day\'s actual ({len(act)} actual days):')
    print(f"   {'lead':>5}{'n':>6}{'MAE':>8}{'bias':>8}{'carry-today MAE':>17}")
    for L, g in D[D.lead_days >= 1].groupby('lead_days'):
        g = g.merge(act.rename('act'), left_on='target_day', right_index=True)
        today = act.reindex(g.render_day - pd.Timedelta(days=1)).values
        g['persist'] = today
        g = g.dropna(subset=['act'])
        if len(g) < 20 or L > 15: continue
        print(f"   {int(L):>5}{len(g):>6}{(g.thermal-g.act).abs().mean():>8.0f}{(g.thermal-g.act).mean():>+8.0f}"
              f"{(g.persist-g.act).abs().mean():>17.0f}")
    g1 = D[D.lead_days == 1].merge(act.rename('act'), left_on='target_day', right_index=True)
    same = (g1.thermal - g1.act).abs().lt(1).mean() if len(g1) else float('nan')
    print(f'\n3. share of lead-1 rows identical to the actual: {same:.0%}  '
          f'({"back-filled, not a forecast" if same > 0.8 else "a genuine earlier view"})')
    if a.pull:
        out = ROOT/'cache'/'forward_supply.csv'; D.to_csv(out, index=False); print(f'\nwrote {out}')


if __name__ == '__main__':
    main()
