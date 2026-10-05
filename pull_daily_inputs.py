"""
pull_daily_inputs.py -- the two feeds the 14-day view and the weekly signal need,
pulled every morning BEFORE the model rebuilds. Each part is independent: if one
fails it says so (and writes the full error to logs/pull_daily_inputs.log) and
the model falls back exactly as before.

 1. Forward thermal = AESO's 24-month supply/demand outlook, CORRECTED.
      Source  canpower.aeso_24m_supply_demand_forecast (AESO re-publishes it
              every morning; CANPOWER has kept every vintage since 2025-11-03).
              Each row is one day's peak hour: total expected internal supply
              excluding wind and solar.
      Why     canpower.aeso_fundamentals_snapshots - the source this used to read -
              only reaches 1-2 hours ahead, so it can never supply days 1-14.
              (Checked 2026-10-05 with diag_thermal.py.)
      Fix     AESO's outlook overstates supply by ~290 MW tomorrow rising to ~960 MW
              at day 13, but tracks outages coming and going well. So each lead is
              corrected by its own average miss over the last 60 days (prior days
              only), and the corrected CHANGE vs the last 7 days is added to the
              last 7 days' gas, split across cogen / CC / gas steam / simple cycle
              by their recent hourly profile.
      Tested  Nov 2025-Oct 2026: corrected error 267 MW (day 1) to 395 MW (day 13)
              vs 351-585 MW carrying last week forward. Sep 20-Oct 2 2026, all
              leads: corrected 162 MW vs AESO gencap + haircut 248 MW.
      Writes  cache/supply_vintages_24m.csv  every vintage (appended daily)
              cache/fwd_thermal.csv          hourly cogen/cc/gfs/sc for every hour ahead
              cache/thermal_status.json      what was used, how old, the corrections

 2. Alberta daily forwards (Warehouse.dbo.ForwardPrices, node AESO SMP, daily
    codes XDP/XDQ/XDT/XDU/XDV/XDW) appended to cache/fwd_aeso_daily.csv.

    python pull_daily_inputs.py
    python pull_daily_inputs.py --offline    # rebuild fwd_thermal.csv from the cached vintages (no database)
    python pull_daily_inputs.py --history-days 400   # one-off re-pull of every vintage
Reuses db.json and update.py's connection helpers; no credentials here.
"""
import json, sys, time, traceback
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'cache'; CACHE.mkdir(exist_ok=True)
LOGS = HERE / 'logs'; LOGS.mkdir(exist_ok=True)
LOGF = LOGS / 'pull_daily_inputs.log'

try:
    import update as u
except Exception:
    u = None

def say(m): print(f"  {m}", flush=True)
def log(m):
    with open(LOGF, 'a', encoding='utf-8') as fh:
        fh.write(f"[{pd.Timestamp.now():%Y-%m-%d %H:%M:%S}] {m}\n")

SUPPLY_TABLE = 'canpower.aeso_24m_supply_demand_forecast'
Q24 = """
SELECT DISTINCT ON (vday, report_date)
       vday, report_date, he_peak, total_expected_internal_supply_mw,
       total_expected_internal_supply_no_wind_solar_mw, import_capacity_bc_sk_matl_mw,
       ail_load_operating_reserves_mw, surplus_mw, surplus_no_wind_solar_mw, create_datetime
FROM (SELECT *, date_trunc('day', create_datetime) AS vday FROM {TABLE}
      WHERE create_datetime >= NOW() - INTERVAL '{DAYS} days') s
WHERE report_date >= vday AND report_date <= vday + INTERVAL '20 days'
ORDER BY vday, report_date, create_datetime DESC
"""
NONVRE = ['sc', 'cogen', 'cc', 'gfs', 'coal', 'dual_fuel', 'hydro', 'energy_storage', 'biomass_and_other']
GAS = ['cogen', 'cc', 'gfs', 'sc']
BIAS_DAYS = 60          # correction window, days of settled target dates
MIN_ROWS_PER_LEAD = 20  # below this a lead borrows the nearest lead's correction


def build_thermal(V, comp, now=None):
    """Corrected AESO outlook -> hourly cogen/cc/gfs/sc for every hour ahead.
    V: every saved vintage (vday, report_date, he_peak, total_expected_internal_supply_no_wind_solar_mw).
    comp: composition actuals (lead_bucket -1). Uses only data before today."""
    now = now or pd.Timestamp.now(tz='America/Edmonton').tz_localize(None)
    today = now.normalize()
    C = comp.copy()
    C['datetime_begin'] = pd.to_datetime(C['datetime_begin'])
    if 'lead_bucket' in C: C = C[C.lead_bucket == -1]
    C = C.set_index('datetime_begin').sort_index(); C = C[~C.index.duplicated(keep='last')]
    for c in NONVRE:
        if c not in C: C[c] = 0.0
    C['nonvre'] = C[NONVRE].fillna(0).sum(axis=1); C['gas'] = C[GAS].sum(axis=1)
    last_act = C.index.max()
    if last_act < today - pd.Timedelta(days=3):
        raise RuntimeError(f'composition actuals end {last_act} - more than 3 days old, cannot correct the outlook')
    V = V.copy()
    for k in ('vday', 'report_date'): V[k] = pd.to_datetime(V[k]).dt.normalize()
    V['lead'] = (V.report_date - V.vday).dt.days
    V['f'] = pd.to_numeric(V.total_expected_internal_supply_no_wind_solar_mw, errors='coerce')
    V['t'] = V.report_date + pd.to_timedelta(V.he_peak.astype(int) - 1, unit='h')
    V['act'] = C.nonvre.reindex(V.t).values
    # latest vintage published on or before today
    vv = V[V.vday <= today]
    if not len(vv): raise RuntimeError('no AESO outlook vintage on file')
    vd = vv.vday.max(); cur = vv[vv.vday == vd].set_index('report_date').sort_index()
    if vd < today - pd.Timedelta(days=2): raise RuntimeError(f'latest AESO outlook is from {vd:%Y-%m-%d} - more than 2 days old')
    # correction per lead from the last 60 days of settled target dates
    past = V[(V.report_date < today) & (V.report_date >= today - pd.Timedelta(days=BIAS_DAYS))].dropna(subset=['act', 'f'])
    if len(past) < 200: raise RuntimeError(f'only {len(past)} settled outlook rows in the last {BIAS_DAYS} days - too few to correct')
    bstat = past.assign(e=past.f - past.act).groupby('lead').e.agg(['mean', 'size'])
    good = bstat[bstat['size'] >= MIN_ROWS_PER_LEAD]
    def bias(L):
        if L in good.index: return float(good.loc[L, 'mean'])
        return float(good.loc[good.index[np.argmin(np.abs(good.index.values - L))], 'mean'])
    # baseline: last 7 days of actual gas (hourly profile per fuel) and the matching peak-hour level
    rec = C[(C.index >= today - pd.Timedelta(days=7)) & (C.index < today)]
    if len(rec) < 24 * 4: raise RuntimeError(f'only {len(rec)} hours of actuals in the last 7 days')
    prof = rec.groupby(rec.index.hour)[GAS + ['biomass_and_other']].mean()
    share = rec[GAS].mean() / max(rec.gas.mean(), 1.0)
    pk = V[(V.lead == 0) & (V.report_date >= today - pd.Timedelta(days=7)) & (V.report_date < today)].dropna(subset=['act'])
    pk7 = float(pk.act.mean()) if len(pk) >= 3 else float(rec.nonvre[rec.index.hour == 17].mean())
    rows = []
    for d, r in cur[cur.index >= today].iterrows():
        L = int((d - vd).days); b = bias(L); fbc = float(r.f) - b; delta = fbc - pk7
        for h in range(24):
            t = d + pd.Timedelta(hours=h)
            if t <= now: continue
            row = {'datetime_begin': t, 'render_time': now, 'lead': L, 'vintage': vd.date(),
                   'outlook_raw': float(r.f), 'correction': round(b, 1), 'outlook_corrected': round(fbc, 1), 'delta_vs_last7': round(delta, 1)}
            for c in GAS: row[c] = max(float(prof.loc[h, c]) + delta * float(share[c]), 0.0)
            row['biomass_and_other'] = float(prof.loc[h, 'biomass_and_other'])
            rows.append(row)
    T = pd.DataFrame(rows)
    if len(T) < 24 * 5: raise RuntimeError(f'only {len(T)} forward hours built')
    status = {'source': 'AESO 24-month outlook, corrected', 'vintage': str(vd.date()), 'built': str(now)[:16],
              'hours': int(len(T)), 'first': str(T.datetime_begin.min()), 'last': str(T.datetime_begin.max()),
              'actuals_end': str(last_act), 'baseline_gas_7d': round(float(rec.gas.mean())), 'baseline_peak_7d': round(pk7),
              'correction_by_lead': {int(k): round(float(v), 1) for k, v in good['mean'].items() if k <= 16},
              'rows_used': int(len(past))}
    return T, status

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
    ap = argparse.ArgumentParser(); ap.add_argument('--history-days', type=int, default=None); ap.add_argument('--offline', action='store_true')
    a, _ = ap.parse_known_args()
    cfg = json.loads((HERE / 'db.json').read_text()) if (HERE / 'db.json').exists() else {}
    ok = True
    # ---- 1. forward thermal: AESO 24-month outlook, corrected (PostgreSQL) ----
    try:
        vp = CACHE / 'supply_vintages_24m.csv'
        if not a.offline:
            cn, _, _ = u._connect(u.comp_cfg(cfg), 'composition')
            with cn:
                t0 = time.time()
                days = a.history_days or (5 if vp.exists() else 400)
                df = pd.read_sql(Q24.replace('{TABLE}', SUPPLY_TABLE).replace('{DAYS}', str(days)), cn)
                df.columns = [c.lower() for c in df.columns]
                if not len(df): raise RuntimeError('AESO outlook query returned no rows')
                for k in ('vday', 'report_date'): df[k] = pd.to_datetime(df[k]).dt.strftime('%Y-%m-%d')
                n = merge_csv(vp, df, ['vday', 'report_date'])
                say(f"supply outlook  {len(df):>5} rows pulled, latest vintage {str(df.vday.max())[:10]} ({n:,} kept, {time.time()-t0:.0f}s)")
        V = pd.read_csv(vp)
        comp = pd.read_csv(CACHE / 'composition.csv')
        T, st = build_thermal(V, comp)
        tmp = CACHE / 'fwd_thermal.csv.tmp'; T.to_csv(tmp, index=False); tmp.replace(CACHE / 'fwd_thermal.csv')
        (CACHE / 'thermal_status.json').write_text(json.dumps(st, indent=1))
        cb = st['correction_by_lead']
        say(f"fwd_thermal   {st['hours']:>6} hours  {st['first'][:13]} to {st['last'][:13]}  (AESO outlook of {st['vintage']}, "
            f"corrected by {cb.get(1, float('nan')):+.0f} MW at day 1 to {cb.get(13, float('nan')):+.0f} MW at day 13)")
        log(f"OK fwd_thermal {st['hours']} hours, vintage {st['vintage']}")
    except Exception as e:
        ok = False
        msg = str(e).splitlines()[0][:160] if str(e) else type(e).__name__
        say(f"FORWARD THERMAL FAILED ({msg}) - the page will fall back to AESO gencap + haircut, which is LESS accurate. Full error: logs/pull_daily_inputs.log")
        log('FAILED forward thermal\n' + traceback.format_exc())
    # ---- 2. Alberta daily forwards (SQL Server) ----
    if a.offline: return 0 if ok else 1
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
        say(f"daily forwards FAILED ({str(e).splitlines()[0][:140]}) - the weekly signal will use the last settle on file. Full error: logs/pull_daily_inputs.log")
        log('FAILED daily forwards\n' + traceback.format_exc())
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main())
