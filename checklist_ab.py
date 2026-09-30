"""
checklist_ab.py — build Checklist_Deviations_AB.xlsx, the Alberta version of the
Ontario pre-model checklist. Reads the model folder's caches and refresh files.
"""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter as CL
from openpyxl.chart import LineChart, BarChart, ScatterChart, Reference, Series
from openpyxl.formatting.rule import CellIsRule, FormulaRule

U = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/mnt/user-data/uploads/aeso-cushion model')
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('/mnt/user-data/outputs/Checklist_Deviations_AB.xlsx')
TOM = pd.Timestamp(sys.argv[3]) if len(sys.argv) > 3 else (pd.Timestamp.now(tz='America/Edmonton').normalize().tz_localize(None) + pd.Timedelta(days=1))
NH, NF = 75, 29                            # 75 days of history; tomorrow + 29 forward (14 forecast, then outlook to day 30)
CF_CALM = 0.16                              # calm day = daily-mean wind below 16% of capacity (capacity = rolling 365-day max hourly wind)
PK = lambda he: he.between(8, 23)          # Alberta peak block HE8-23
TIGHT = 1200

# ----------------------------------------------------------------- inputs ---
C = pd.read_csv(U/'cache/composition.csv', parse_dates=['datetime_begin']); C = C[C.lead_bucket == -1]
C = C.set_index('datetime_begin').sort_index(); C = C[~C.index.duplicated(keep='last')]
ph = pd.read_csv(U/'model/price_history.csv', index_col=0, parse_dates=True); C['price'] = ph.price.reindex(C.index)
C['thermal'] = C.cogen + C.cc + C.gfs; C['need'] = C.ail - C.wind - C.solar - C.net_imports_actual_scheduled - C.hydro - C.energy_storage - C.biomass_and_other
C['rem'] = C.thermal - C.need
C['cush'] = C.thermal + C.sc + C.biomass_and_other + C.wind + C.solar - (C.ail - C.net_imports_actual_scheduled)
C['d'] = C.index.normalize(); C['he'] = C.index.hour + 1
C['wcap'] = C.wind.rolling(24*365, min_periods=24*30).max().bfill()
WCAP_NOW = float(C.wcap.iloc[-1])
wx = pd.read_csv(U/'cache/wx_hourly.csv', parse_dates=[0]); wx = wx.rename(columns={wx.columns[0]: 'ts'}); wx['d'] = wx.ts.dt.normalize(); wx['he'] = wx.ts.dt.hour + 1
wf = None
if (U/'cache/wx_fcst.csv').exists():
    wf = pd.read_csv(U/'cache/wx_fcst.csv', parse_dates=['t']); wf['d'] = wf.t.dt.normalize(); wf['he'] = wf.t.dt.hour + 1
# weather climatology by day of year (+/- 7 days, every year on file) for days past the forecast
_wd = wx.groupby('d').agg(t_max=('temp', 'max'), t_avg=('temp', 'mean')); _wd['w100'] = wx[wx.he.between(8, 23)].groupby('d').wind.mean()
_wd['doy'] = _wd.index.dayofyear
def wx_clim(d):
    k = d.dayofyear; m = (np.abs(((_wd.doy - k + 182) % 365) - 182) <= 7)
    g = _wd[m]; return (float(g.t_max.mean()), float(g.t_avg.mean()), float(g.w100.mean())) if len(g) else (None, None, None)

def vint(name, val, lo, hi, src):
    d = pd.read_csv(U/f'cache/{name}.csv'); ts = [c for c in d.columns if c.lower() == 'timestamp'][0]
    d['iss'] = pd.to_datetime(d[ts], errors='coerce'); d['tgt'] = pd.to_datetime(d.EffectiveDateTime, errors='coerce')
    d['lead'] = (d.tgt - d.iss).dt.total_seconds()/3600
    d = d[(d.lead >= lo) & (d.lead <= hi) & (d.DataSourceName == src)].dropna(subset=['tgt', val]).sort_values('iss')
    s = d.groupby('tgt')[val].last(); s.index = s.index.tz_localize(None) if getattr(s.index, 'tz', None) else s.index
    return s
lfc = vint('load_fc', 'Load', 12, 40, 'AESO'); wfc = vint('wind_fc', 'Value', 16, 40, 'AESO')
lfc = pd.DataFrame({'v': lfc}); lfc['d'] = lfc.index.normalize(); lfc['he'] = lfc.index.hour + 1
wfc = pd.DataFrame({'v': wfc}); wfc['d'] = wfc.index.normalize(); wfc['he'] = wfc.index.hour + 1

# wind MW from the 100 m wind speed: capacity factor by 5 km/h bin (HE8-23 mean speed), last 365 days
_pk = C[(C.d >= TOM - pd.Timedelta(days=365)) & (C.d < TOM) & PK(C.he)].groupby('d').agg(w=('wind', 'mean'), cap=('wcap', 'mean'))
_pk['ws'] = wx[PK(wx.he)].groupby('d').wind.mean().reindex(_pk.index); _pk = _pk.dropna(); _pk['bin'] = (_pk.ws // 5) * 5
CF_BY_WS = (_pk.w / _pk.cap).groupby(_pk.bin).mean()
def wind_from_ws(ws_):
    if ws_ is None or np.isnan(ws_) or not len(CF_BY_WS): return None
    b = min(max((ws_ // 5) * 5, CF_BY_WS.index.min()), CF_BY_WS.index.max())
    cf = CF_BY_WS.get(b, np.nan)
    if np.isnan(cf): cf = CF_BY_WS.iloc[(CF_BY_WS.index - b).abs().argmin()]
    return float(cf * WCAP_NOW)
# refresh (forward)
L = json.load(open(U/'refresh/load.json', encoding='utf-8-sig'))['return']['Actual Forecast Report']
ld = pd.DataFrame([{'t': pd.Timestamp(r['begin_datetime_mpt']), 'act': pd.to_numeric(r.get('alberta_internal_load'), errors='coerce'),
                    'fc': pd.to_numeric(r.get('forecast_alberta_internal_load'), errors='coerce')} for r in L]).set_index('t')
ld['d'] = ld.index.normalize(); ld['he'] = ld.index.hour + 1
def ws_(n):
    x = pd.read_csv(U/f'refresh/{n}.csv'); x['t'] = pd.to_datetime(x['Forecast Transaction Date']); x = x.set_index('t')
    x['d'] = x.index.normalize(); x['he'] = x.index.hour + 1; x['ml'] = pd.to_numeric(x['Most Likely'], errors='coerce'); return x
W, S = ws_('wind_fc'), ws_('solar_fc')
G = json.load(open(U/'refresh/gencap.json', encoding='utf-8-sig'))['return']; rows = {}
for b in G:
    for h in b['Hours']:
        rows.setdefault(pd.Timestamp(h['begin_datetime_mpt']), {})[b['sub_fuel_type']] = h['outage_grouping'].get('AC')
gc = pd.DataFrame.from_dict(rows, orient='index').sort_index(); gc['d'] = gc.index.normalize(); gc['he'] = gc.index.hour + 1
gc['thermal'] = gc[['COGENERATION', 'COMBINED_CYCLE', 'GAS_FIRED_STEAM']].sum(axis=1); gc['sc'] = gc['SIMPLE_CYCLE']
gc['other'] = gc[['HYDRO', 'ENERGY STORAGE', 'OTHER']].sum(axis=1)
I = json.load(open(U/'refresh/intertie.json', encoding='utf-8-sig'))['return']; ri = {}
for g_, al in I.items():
    if not isinstance(al, dict) or 'Allocations' not in al or g_ in ('BcMatlFlowgate', 'SystemlFlowgate'): continue
    for a in al['Allocations']:
        t = pd.Timestamp(a['date']) + pd.Timedelta(hours=int(a['he'])-1); r = ri.setdefault(t, 0.0)
        if isinstance(a.get('import'), dict): ri[t] += float(a['import'].get('atc') or 0)
atc = pd.Series(ri).sort_index(); atc = atc.groupby(atc.index.normalize()).mean()
# outage report
lines = open(U/'refresh/outage_90d.csv', encoding='utf-8', errors='ignore').read().splitlines()
k0 = [k for k, l in enumerate(lines) if l.strip().startswith('"   Date')][0]
import io; orep = pd.read_csv(io.StringIO('\n'.join(lines[k0:])), skipinitialspace=True); orep.columns = [c.strip() for c in orep.columns]
orep = orep[orep.Date.astype(str).str.match(r'\d\d-\w\w\w-\d{4}')].copy(); orep['Date'] = pd.to_datetime(orep.Date, format='%d-%b-%Y')
for c in ['SC', 'Cogen', 'CC', 'GFS']: orep[c] = pd.to_numeric(orep[c], errors='coerce')
orep = orep.set_index('Date'); orep['thermal_out'] = orep[['SC', 'Cogen', 'CC', 'GFS']].sum(axis=1)
ol = pd.read_csv(U/'cache/outlook.csv', parse_dates=['date']).set_index('date')
# forwards
f = pd.read_csv(U/'cache/fwd_aeso_daily.csv', parse_dates=['EffectiveDate', 'Strip']); f['lead'] = (f.Strip - f.EffectiveDate).dt.days
da = f[(f.lead >= 1)].sort_values('lead').groupby(['Strip', 'ExchangeCode']).Price.first().unstack()
fx = Path('/home/claude/bt') / 'fwd_extra_src.csv'
fe = pd.read_csv(U/'cache/fwd_extra.csv', parse_dates=['EffectiveDate', 'Strip']) if (U/'cache/fwd_extra.csv').exists() else None
if fe is not None:
    fe['lead'] = (fe.Strip - fe.EffectiveDate).dt.days
    nb = fe[fe.lead >= 0].sort_values('lead').groupby(['Strip', 'ExchangeCode']).Price.first().unstack()

# --------------------------------------------------------------- the table --
def hist(d): return d < TOM
def pkavg(df, col, d, mask=None):
    g = df[(df.d == d) & PK(df.he)];
    return float(g[col].mean()) if len(g) and g[col].notna().any() else None
def daymax(df, col, d):
    g = df[df.d == d]; return float(g[col].max()) if len(g) and g[col].notna().any() else None
def daymin_pk(df, col, d):
    g = df[(df.d == d) & PK(df.he)]; return float(g[col].min()) if len(g) and g[col].notna().any() else None
def dmean(df, col, d):
    g = df[df.d == d]; return float(g[col].mean()) if len(g) and g[col].notna().any() else None
ni30 = float(C[(C.d >= TOM - pd.Timedelta(days=30)) & (C.d < TOM)].net_imports_actual_scheduled.mean())
# beyond the 14-day feeds: Tesla load where it reaches, else same-weekday level of the last 4 weeks;
# wind from the ECMWF outlook if it is fresh, else the calendar-month normal; thermal = last gencap day
# moved by the 90-day report; solar at the last-14-day level. Marked Period = Outlook.
tesla_all = vint('load_fc', 'Load', 12, 800, 'Tesla'); tesla_all = pd.DataFrame({'v': tesla_all}); tesla_all['d'] = tesla_all.index.normalize(); tesla_all['he'] = tesla_all.index.hour + 1
GL = gc.d.max()
ol_fresh = (U/'cache/outlook.csv').exists() and (pd.Timestamp.now() - pd.Timestamp((U/'cache/outlook.csv').stat().st_mtime, unit='s')).days <= 3
hist28 = C[(C.d >= TOM - pd.Timedelta(days=28)) & (C.d < TOM)]
wnorm = C[(C.d < TOM)].copy(); wnorm['m'] = wnorm.d.dt.month; wnorm = wnorm[PK(wnorm.he)].groupby('m').wind.mean()
sol14 = float(C[(C.d >= TOM - pd.Timedelta(days=14)) & (C.d < TOM)].groupby('d').solar.max().mean())
oth_gl = float(gc[(gc.d == GL) & PK(gc.he)]['OTHER'].mean()) if (gc.d == GL).any() else 0.0
def build_rows(TOM_):
    dates = pd.date_range(TOM_ - pd.Timedelta(days=NH), TOM_ + pd.Timedelta(days=NF)); recs = []
    for d in dates:
        H = d < TOM; O = d > TOM_ + pd.Timedelta(days=13)
        r = {'Date': d, 'Period': 'History' if d < TOM_ else ('Tomorrow' if d == TOM_ else ('Forward' if not O else 'Outlook'))}
        # load
        if H:
            r['lf_pk'] = daymax(lfc, 'v', d); r['lf_avg'] = pkavg(lfc, 'v', d); r['la_pk'] = daymax(C, 'ail', d)
        elif (ld.d == d).any():
            r['lf_pk'] = daymax(ld, 'fc', d); r['lf_avg'] = pkavg(ld, 'fc', d); r['la_pk'] = None
        elif (tesla_all.d == d).sum() >= 16:
            r['lf_pk'] = daymax(tesla_all, 'v', d); r['lf_avg'] = pkavg(tesla_all, 'v', d); r['la_pk'] = None
        else:
            g = hist28[hist28.d.dt.dayofweek == d.dayofweek]
            r['lf_pk'] = float(g.groupby('d').ail.max().mean()) if len(g) else None; r['lf_avg'] = float(g[PK(g.he)].ail.mean()) if len(g) else None; r['la_pk'] = None
        r['l_miss'] = (r['la_pk'] - r['lf_pk']) if (r['la_pk'] is not None and r['lf_pk'] is not None) else None
        # thermal
        if H:
            r['th_av'] = pkavg(C, 'thermal', d); r['sc_av'] = pkavg(C, 'sc', d)
            g = C[C.d == d].thermal; r['trip'] = float(-(g.diff().min())) if len(g) > 1 else None
        elif (gc.d == d).any():
            r['th_av'] = pkavg(gc, 'thermal', d); r['sc_av'] = pkavg(gc, 'sc', d); r['trip'] = None
        else:
            th0, sc0 = pkavg(gc, 'thermal', GL), pkavg(gc, 'sc', GL)
            if d in orep.index and GL in orep.index:
                dth = float(orep.loc[d, ['Cogen', 'CC', 'GFS']].sum() - orep.loc[GL, ['Cogen', 'CC', 'GFS']].sum()); dsc = float(orep.loc[d, 'SC'] - orep.loc[GL, 'SC'])
            else: dth = dsc = 0.0
            r['th_av'] = (th0 - dth) if th0 is not None else None; r['sc_av'] = (sc0 - dsc) if sc0 is not None else None; r['trip'] = None
        r['plan_out'] = float(orep.thermal_out.get(d, np.nan)) if d in orep.index else None
        # renewables
        if H:
            r['w_fc'] = pkavg(wfc, 'v', d); r['w_act'] = pkavg(C, 'wind', d); r['sol_fc'] = daymax(C, 'solar', d)
        else:
            if (W.d == d).sum() >= 16: r['w_fc'] = pkavg(W, 'ml', d)
            elif ol_fresh and d in ol.index: r['w_fc'] = float(ol.wind.get(d))
            elif wf is not None and (wf.d == d).sum() >= 20: r['w_fc'] = wind_from_ws(float(wf[(wf.d == d) & PK(wf.he)].wind.mean()))
            else: r['w_fc'] = float(wnorm.get(d.month, np.nan))
            r['w_act'] = None; r['sol_fc'] = daymax(S, 'ml', d) if (S.d == d).any() else sol14
        r['w_miss'] = (r['w_act'] - r['w_fc']) if (r['w_act'] is not None and r['w_fc'] is not None) else None
        if H:
            wd = dmean(C, 'wind', d); r['w_cf'] = 100*wd/float(C[C.d == d].wcap.mean()) if wd is not None else None
        else:
            wd = dmean(W, 'ml', d) if (W.d == d).sum() >= 16 else r['w_fc']
            r['w_cf'] = 100*wd/WCAP_NOW if wd is not None and not np.isnan(wd) else None
        # supply
        if H:
            r['cush_min'] = daymin_pk(C, 'cush', d); r['cush_avg'] = pkavg(C, 'cush', d); r['rem_min'] = daymin_pk(C, 'rem', d)
        elif (gc.d == d).any() and (ld.d == d).any():
            # forward cushion from gencap + AESO forecasts, hour by hour
            gg = gc[(gc.d == d)].copy()
            gg['load'] = ld.fc.reindex(gg.index); gg['wind'] = W.ml.reindex(gg.index) if (W.d == d).sum() >= 16 else r['w_fc']
            gg['solar'] = S.ml.reindex(gg.index).fillna(0) if (S.d == d).any() else 0
            gg['cushf'] = gg.thermal + gg.sc + gg['OTHER'] + gg.wind + gg.solar - (gg.load - ni30)
            gg['remf'] = gg.thermal - (gg.load - gg.wind - gg.solar - ni30 - gg['HYDRO'] - gg['ENERGY STORAGE'] - gg['OTHER'])
            p = gg[PK(gg.he)]
            r['cush_min'] = float(p.cushf.min()) if p.cushf.notna().any() else None; r['cush_avg'] = float(p.cushf.mean()) if p.cushf.notna().any() else None
            r['rem_min'] = float(p.remf.min()) if p.remf.notna().any() else None
        else:
            # daily approximation: peak-hour levels, solar at half its daily max over the block
            ok = all(r[k] is not None and not (isinstance(r[k], float) and np.isnan(r[k])) for k in ('th_av', 'sc_av', 'w_fc', 'lf_pk', 'lf_avg', 'sol_fc'))
            if ok:
                r['cush_min'] = r['th_av'] + r['sc_av'] + oth_gl + r['w_fc'] + 0.5*r['sol_fc'] - (r['lf_pk'] - ni30)
                r['cush_avg'] = r['th_av'] + r['sc_av'] + oth_gl + r['w_fc'] + 0.5*r['sol_fc'] - (r['lf_avg'] - ni30)
                r['rem_min'] = r['th_av'] - (r['lf_pk'] - r['w_fc'] - 0.5*r['sol_fc'] - ni30 - oth_gl)
            else: r['cush_min'] = r['cush_avg'] = r['rem_min'] = None
        # interties / neighbours
        r['atc'] = float(atc.get(d, np.nan)) if d in atc.index else None
        r['ni'] = dmean(C, 'net_imports_actual_scheduled', d) if H else ni30
        r['midc'] = float(nb['MPD'].get(d, np.nan)) if fe is not None and d in nb.index else None
        r['gas'] = float(nb['XBG'].get(d, np.nan)) if fe is not None and d in nb.index else None
        # prices
        if H:
            g = C[C.d == d].price.dropna()
            if len(g) >= 20:
                r['px_flat'] = float(g.mean()); r['px_pk'] = pkavg(C, 'price', d); r['px_max'] = float(g.max()); r['n100'] = int((g > 100).sum())
            else: r['px_flat'] = r['px_pk'] = r['px_max'] = r['n100'] = None
        else: r['px_flat'] = r['px_pk'] = r['px_max'] = r['n100'] = None
        r['da_pk'] = float(da['XDQ'].get(d, np.nan)) if d in da.index else None
        r['da_flat'] = float(da['XDT'].get(d, np.nan)) if d in da.index else None
        r['pk_minus_da'] = (r['px_pk'] - r['da_pk']) if (r['px_pk'] is not None and r['da_pk'] is not None and not np.isnan(r['da_pk'])) else None
        # weather (province average of Calgary / Edmonton / Pincher Creek)
        if H and (wx.d == d).any():
            g = wx[wx.d == d]; r['t_max'] = float(g.temp.max()); r['t_avg'] = float(g.temp.mean())
            r['w100'] = float(g[PK(g.he)].wind.mean()); r['rad'] = float(g[g.he.between(11, 16)].rad.mean()); r['cloud'] = float(g[g.he.between(11, 16)].cloud.mean())
        elif (not H) and wf is not None and (wf.d == d).sum() >= 20:
            g = wf[wf.d == d]; r['t_max'] = float(g.temp.max()); r['t_avg'] = float(g.temp.mean())
            r['w100'] = float(g[PK(g.he)].wind.mean()); r['rad'] = float(g[g.he.between(11, 16)].rad.mean()); r['cloud'] = float(g[g.he.between(11, 16)].cloud.mean())
        elif not H:
            r['t_max'], r['t_avg'], r['w100'] = wx_clim(d); r['rad'] = r['cloud'] = None
        else: r['t_max'] = r['t_avg'] = r['w100'] = r['rad'] = r['cloud'] = None
        r['tight'] = TIGHT
        recs.append(r)
    return pd.DataFrame(recs)
def numeric(Dx):
    for c in Dx.columns:
        if c not in ('Date', 'Period'): Dx[c] = pd.to_numeric(Dx[c], errors='coerce')
    return Dx
D = numeric(build_rows(TOM))
D1 = numeric(build_rows(TOM - pd.DateOffset(years=1)))      # the same window a year ago (all actuals)
D2 = numeric(build_rows(TOM - pd.DateOffset(years=2)))      # and two years ago (the feed starts 2024-08-31)

ITEMS = [  # key, group, label, unit, source
 ('lf_pk', 'Load', 'AESO load forecast, daily peak', 'MW', 'AESO day-ahead vintage (history); AESO 14-day load forecast (forward)'),
 ('lf_avg', 'Load', 'AESO load forecast, HE8-23 avg', 'MW', 'same'),
 ('la_pk', 'Load', 'Load actual, daily peak', 'MW', 'CANPOWER composition'),
 ('l_miss', 'Load', 'Load miss: actual peak - day-ahead forecast peak', 'MW', 'derived'),
 ('th_av', 'Supply', 'Baseload gas available (cogen + CC + GFS), HE8-23 avg', 'MW', 'composition (history); AESO gencap AC (forward)'),
 ('sc_av', 'Supply', 'Simple cycle available, HE8-23 avg', 'MW', 'same'),
 ('plan_out', 'Outages', 'Thermal MW unavailable in the 90-day outage report (SC+cogen+CC+GFS)', 'MW', 'AESO daily outage report'),
 ('trip', 'Outages', 'Trips: largest hour-over-hour drop in baseload gas available', 'MW', 'composition'),
 ('w_fc', 'Renewables', 'Wind forecast, HE8-23 avg', 'MW', 'AESO day-ahead vintage (history); AESO 216-h forecast then ECMWF outlook (forward)'),
 ('w_act', 'Renewables', 'Wind actual, HE8-23 avg', 'MW', 'composition'),
 ('w_miss', 'Renewables', 'Wind miss: actual - day-ahead forecast, HE8-23 avg', 'MW', 'derived'),
 ('w_cf', 'Renewables', 'Wind capacity factor, daily mean (calm day = below 16)', '%', 'composition / forecast wind vs rolling 365-day max'),
 ('sol_fc', 'Renewables', 'Solar, daily max (actual history / forecast forward)', 'MW', 'composition; AESO solar forecast'),
 ('cush_min', 'Supply', 'Cushion (internal dispatchable supply - net demand), tightest HE8-23', 'MW', 'composition (history); gencap + AESO forecasts + 30-day mean imports (forward)'),
 ('cush_avg', 'Supply', 'Cushion, HE8-23 avg', 'MW', 'same'),
 ('rem_min', 'Supply', 'Thermal remaining (baseload gas spare before SC), tightest HE8-23', 'MW', 'same'),
 ('atc', 'Interties', 'Import ATC, all paths, daily avg', 'MW', 'AESO ITC'),
 ('ni', 'Interties', 'Net imports actual scheduled, daily avg', 'MW', 'composition'),
 ('midc', 'Neighbours', 'Mid-C daily price', 'USD/MWh', 'forward curve file'),
 ('gas', 'Neighbours', 'AB-NIT gas, daily', '$/GJ', 'forward curve file'),
 ('px_flat', 'Prices', 'Pool price, 7x24 avg', '$/MWh', 'AESO'),
 ('px_pk', 'Prices', 'Pool price, HE8-23 avg', '$/MWh', 'AESO'),
 ('px_max', 'Prices', 'Pool price, worst hour', '$/MWh', 'AESO'),
 ('n100', 'Prices', 'Hours over $100', 'hours', 'AESO'),
 ('da_pk', 'Prices', 'Day-ahead forward, peak (XDQ), last settle before delivery', '$/MWh', 'forward curve file'),
 ('da_flat', 'Prices', 'Day-ahead forward, flat (XDT), last settle before delivery', '$/MWh', 'forward curve file'),
 ('pk_minus_da', 'Prices', 'Peak settle - day-ahead forward (+ = buys won)', '$/MWh', 'derived'),
 ('t_max', 'Weather', 'Max temperature, province avg (Calgary / Edmonton / Pincher)', 'deg C', 'Open-Meteo'),
 ('t_avg', 'Weather', 'Mean temperature, province avg', 'deg C', 'Open-Meteo'),
 ('w100', 'Weather', 'Wind at 100 m, province avg, HE8-23', 'km/h', 'Open-Meteo'),
 ('rad', 'Weather', 'Solar radiation, 11:00-16:00 avg', 'W/m2', 'Open-Meteo'),
 ('cloud', 'Weather', 'Cloud cover, 11:00-16:00 avg', '%', 'Open-Meteo'),
 ('tight', 'Reference', 'Tight line: cushion below this = spike zone', 'MW', 'model'),
]
KEYS = [k for k, *_ in ITEMS]; COLIDX = {k: i+3 for i, k in enumerate(KEYS)}   # Data columns start at C
NR = len(D); DL, DH = 2, NR + 1

# ----------------------------------------------------------------- styling ---
F = lambda **kw: Font(**{'name': 'Arial', 'size': 10, **kw})
BLUE, GREEN, BLACK = F(color='0000FF'), F(color='008000'), F()
YEL = PatternFill('solid', fgColor='FFFF00'); HDR = PatternFill('solid', fgColor='DDEBF7'); GREY = PatternFill('solid', fgColor='F2F2F2')
thin = Side(style='thin', color='BFBFBF'); BOX = Border(bottom=thin)
wb = openpyxl.Workbook()

# ---------------------------------------------------------------- Data -------
CH_KEYS = ['lf_pk', 'la_pk', 'th_av', 'sc_av', 'plan_out', 'trip', 'cush_min', 'cush_avg', 'tight', 'rem_min', 'w_fc', 'w_act', 'px_pk', 'da_pk', 'pk_minus_da', 'atc', 'ni', 't_max', 't_avg', 'w100', 'midc']
SHORT = {'lf_pk': 'Load fc peak', 'la_pk': 'Load actual peak', 'th_av': 'Baseload gas', 'sc_av': 'Simple cycle', 'plan_out': 'Report MW out', 'trip': 'Trips', 'cush_min': 'Cushion tightest', 'cush_avg': 'Cushion avg', 'tight': 'Tight line',
         'rem_min': 'Thermal remaining', 'w_fc': 'Wind fc', 'w_act': 'Wind actual', 'px_pk': 'Pool HE8-23', 'da_pk': 'Day-ahead fwd', 'pk_minus_da': 'Settle - fwd', 'atc': 'Import ATC', 'ni': 'Net imports', 't_max': 'T max', 't_avg': 'T mean', 'w100': 'Wind 100 m', 'midc': 'Mid-C'}
def write_data(ws_, Dx, split):
    """The daily table plus the chart helper columns. split = mark the Outlook rows as a separate (dashed) series."""
    ws_.cell(1, 1, 'Date').font = F(bold=True); ws_.cell(1, 2, 'Period').font = F(bold=True)
    for k, g, lab, u, src in ITEMS:
        c = ws_.cell(1, COLIDX[k], f'{lab} ({u})'); c.font = F(bold=True); c.alignment = Alignment(wrap_text=True, vertical='top')
    for i, r in Dx.iterrows():
        row = i + 2; ws_.cell(row, 1, r.Date.to_pydatetime()).number_format = 'yyyy-mm-dd'; ws_.cell(row, 1).font = BLACK
        ws_.cell(row, 2, r.Period).font = BLACK
        for k in KEYS:
            v = r[k]; c = ws_.cell(row, COLIDX[k], None if pd.isna(v) else round(float(v), 2)); c.font = BLUE
            c.number_format = '#,##0.00' if k in ('gas',) else ('#,##0.0' if k in ('t_max', 't_avg', 'w100', 'rad', 'cloud', 'w_cf', 'px_flat', 'px_pk', 'px_max', 'da_pk', 'da_flat', 'pk_minus_da', 'midc') else '#,##0')
            if r.Period == 'Tomorrow': c.fill = YEL
        if r.Period == 'Tomorrow': ws_.cell(row, 1).fill = YEL; ws_.cell(row, 2).fill = YEL
    CHx = {}; n = len(Dx); c0 = len(KEYS) + 4
    ws_.cell(1, c0 - 1, 'Chart label').font = F(bold=True)
    for i, r in Dx.iterrows(): ws_.cell(i + 2, c0 - 1, r.Date.strftime('%d-%b')).font = BLACK
    for j, k in enumerate(CH_KEYS):
        ca, cb = c0 + 2*j, c0 + 2*j + 1; CHx[k] = (ca, cb); lab = SHORT[k]
        ws_.cell(1, ca, lab).font = F(bold=True, color='808080'); ws_.cell(1, cb, f'{lab} outlook').font = F(bold=True, color='808080')
        for i, r in Dx.iterrows():
            v = r[k]; v = None if pd.isna(v) else round(float(v), 2)
            isO = split and r.Period == 'Outlook'; last14 = split and (not isO) and (i + 1 < n) and Dx.iloc[i + 1].Period == 'Outlook'
            ws_.cell(i + 2, ca, None if isO else v).font = F(color='808080'); ws_.cell(i + 2, cb, v if (isO or last14) else None).font = F(color='808080')
    ws_.freeze_panes = 'C2'; ws_.column_dimensions['A'].width = 12; ws_.column_dimensions['B'].width = 10
    for k in KEYS: ws_.column_dimensions[CL(COLIDX[k])].width = 16
    ws_.row_dimensions[1].height = 60
    return CHx
ws = wb.active; ws.title = 'Data'; CH = write_data(ws, D, True)
ws1 = wb.create_sheet('Data_1y'); CH1 = write_data(ws1, D1, False)
ws2 = wb.create_sheet('Data_2y'); CH2 = write_data(ws2, D2, False)
rng = lambda k: f"Data!${CL(COLIDX[k])}${DL}:${CL(COLIDX[k])}${DH}"; DATES = f"Data!$A${DL}:$A${DH}"

# --------------------------------------------------------------- Summary -----
sm = wb.create_sheet('Summary', 0)
sm['A1'] = 'Pre-model checklist (Alberta): tomorrow vs the last 14 and 30 days'; sm['A1'].font = F(bold=True, size=12)
sm['A2'] = 'Tomorrow (delivery day)'; sm['D2'] = TOM.to_pydatetime(); sm['D2'].number_format = 'yyyy-mm-dd'; sm['D2'].fill = YEL; sm['D2'].font = BLUE
sm['A3'] = 'Last-14 / last-30 = the 14 / 30 delivery days before tomorrow. Next-14 = tomorrow + the 13 days after it. z = (tomorrow - last-30 mean) / last-30 SD; HIGH / LOW = at least one SD away.'
hdr = ['Group', 'Item', 'Unit', 'Tomorrow', 'Last-14 mean', 'Tomorrow - last-14', 'Last-30 mean', 'Tomorrow - last-30', 'Last-30 SD', 'z vs last-30', 'Flag', 'Last-30 min', 'Last-30 max', 'Next-14 mean', 'Next-14 - last-30', 'Source']
for j, h in enumerate(hdr, 1): c = sm.cell(5, j, h); c.font = F(bold=True); c.fill = HDR; c.alignment = Alignment(wrap_text=True)
SUMROW = {}
for i, (k, g, lab, u, src) in enumerate(ITEMS):
    r = 6 + i; SUMROW[k] = r; R = rng(k)
    sm.cell(r, 1, g).font = BLACK; sm.cell(r, 2, lab).font = BLACK; sm.cell(r, 3, u).font = BLACK
    sm.cell(r, 4, f'=IFERROR(IF(INDEX({R},MATCH($D$2,{DATES},0))="","",INDEX({R},MATCH($D$2,{DATES},0))),"")').font = GREEN
    sm.cell(r, 5, f'=IFERROR(AVERAGEIFS({R},{DATES},">="&$D$2-14,{DATES},"<"&$D$2),"")')
    sm.cell(r, 6, f'=IF(AND(ISNUMBER(D{r}),ISNUMBER(E{r})),D{r}-E{r},"")')
    sm.cell(r, 7, f'=IFERROR(AVERAGEIFS({R},{DATES},">="&$D$2-30,{DATES},"<"&$D$2),"")')
    sm.cell(r, 8, f'=IF(AND(ISNUMBER(D{r}),ISNUMBER(G{r})),D{r}-G{r},"")')
    sm.cell(r, 9, f'=IFERROR(SQRT(SUMPRODUCT(({DATES}>=$D$2-30)*({DATES}<$D$2)*ISNUMBER({R}),({R}-G{r})^2)/(COUNTIFS({DATES},">="&$D$2-30,{DATES},"<"&$D$2,{R},"<>")-1)),"")')
    sm.cell(r, 10, f'=IF(AND(ISNUMBER(H{r}),ISNUMBER(I{r})),IF(I{r}>0,H{r}/I{r},""),"")')
    sm.cell(r, 11, f'=IF(ISNUMBER(J{r}),IF(J{r}>=1,"HIGH",IF(J{r}<=-1,"LOW","")),"")')
    sm.cell(r, 12, f'=IFERROR(MINIFS({R},{DATES},">="&$D$2-30,{DATES},"<"&$D$2),"")'.replace('MINIFS', '_xlfn.MINIFS'))
    sm.cell(r, 13, f'=IFERROR(MAXIFS({R},{DATES},">="&$D$2-30,{DATES},"<"&$D$2),"")'.replace('MAXIFS', '_xlfn.MAXIFS'))
    sm.cell(r, 14, f'=IFERROR(AVERAGEIFS({R},{DATES},">="&$D$2,{DATES},"<="&$D$2+13),"")')
    sm.cell(r, 15, f'=IF(AND(ISNUMBER(N{r}),ISNUMBER(G{r})),N{r}-G{r},"")')
    sm.cell(r, 16, src).font = F(color='808080', size=9)
    for j in range(4, 16):
        c = sm.cell(r, j); c.number_format = '#,##0.0'
        if j != 4 and c.font != GREEN: c.font = BLACK
last = 5 + len(ITEMS)
sm.conditional_formatting.add(f'K6:K{last}', CellIsRule(operator='equal', formula=['"HIGH"'], fill=PatternFill('solid', fgColor='F8CBAD')))
sm.conditional_formatting.add(f'K6:K{last}', CellIsRule(operator='equal', formula=['"LOW"'], fill=PatternFill('solid', fgColor='C6E0B4')))
for col, w in zip('ABCDEFGHIJKLMNOP', [11, 62, 9, 11, 12, 13, 12, 13, 11, 10, 7, 11, 11, 12, 13, 50]): sm.column_dimensions[col].width = w
sm.freeze_panes = 'D6'

# ------------------------------------------------- Next_14 / Last_30 ---------
def block_sheet(name, title, days, newest_first):
    s = wb.create_sheet(name)
    s['A1'] = title; s['A1'].font = F(bold=True, size=12)
    s['A2'] = 'Red / green = the difference is more than one last-30 SD above / below.'
    s['A4'] = 'Date'; s['A4'].font = F(bold=True)
    for i, (k, g, lab, u, src) in enumerate(ITEMS):
        c0 = 2 + 3*i; s.cell(3, c0, f'{lab} ({u})').font = F(bold=True); s.cell(3, c0).alignment = Alignment(wrap_text=True)
        for j, h in enumerate(['Value', 'vs last-14', 'vs last-30']): c = s.cell(4, c0+j, h); c.font = F(bold=True); c.fill = HDR
        s.column_dimensions[CL(c0)].width = 11; s.column_dimensions[CL(c0+1)].width = 10; s.column_dimensions[CL(c0+2)].width = 10
    for n, d in enumerate(days):
        r = 5 + n; s.cell(r, 1, d.to_pydatetime()).number_format = 'yyyy-mm-dd'
        for i, (k, *_ ) in enumerate(ITEMS):
            c0 = 2 + 3*i; R = rng(k); sr = SUMROW[k]; cl = CL(c0)
            s.cell(r, c0, f'=IFERROR(IF(INDEX({R},MATCH($A{r},{DATES},0))="","",INDEX({R},MATCH($A{r},{DATES},0))),"")').font = GREEN
            s.cell(r, c0+1, f'=IF(AND(ISNUMBER({cl}{r}),ISNUMBER(Summary!$E${sr})),{cl}{r}-Summary!$E${sr},"")')
            s.cell(r, c0+2, f'=IF(AND(ISNUMBER({cl}{r}),ISNUMBER(Summary!$G${sr})),{cl}{r}-Summary!$G${sr},"")')
            for j in range(3): s.cell(r, c0+j).number_format = '#,##0.0'
            c2 = f'{CL(c0+2)}5:{CL(c0+2)}{4+len(days)}'
            if n == 0:
                s.conditional_formatting.add(c2, FormulaRule(formula=[f'AND(ISNUMBER({CL(c0+2)}5),ISNUMBER(Summary!$I${sr}),{CL(c0+2)}5>Summary!$I${sr})'], fill=PatternFill('solid', fgColor='F8CBAD')))
                s.conditional_formatting.add(c2, FormulaRule(formula=[f'AND(ISNUMBER({CL(c0+2)}5),ISNUMBER(Summary!$I${sr}),{CL(c0+2)}5<-Summary!$I${sr})'], fill=PatternFill('solid', fgColor='C6E0B4')))
    s.row_dimensions[3].height = 48; s.freeze_panes = 'B5'; s.column_dimensions['A'].width = 12
block_sheet('Next_14_Days', 'Next 14 days (tomorrow + 13): raw value, then the difference vs the last-14 and last-30 day means', list(pd.date_range(TOM, periods=14)), False)
block_sheet('Last_30_Days', 'Last 30 delivery days (newest first): raw value, then the difference vs the last-14 and last-30 day means', list(pd.date_range(TOM - pd.Timedelta(days=30), TOM - pd.Timedelta(days=1))[::-1]), True)

# --------------------------------------------------------------- Scenario ----
sc = wb.create_sheet('Scenario', 1)
gg = gc[gc.d == TOM].copy(); gg['load'] = ld.fc.reindex(gg.index); gg['wind'] = W.ml.reindex(gg.index); gg['solar'] = S.ml.reindex(gg.index).fillna(0)
gg['ni'] = ni30; gg['cush'] = gg.thermal + gg.sc + gg['OTHER'] + gg.wind + gg.solar - (gg.load - gg.ni)
# hourly price response: log price vs cushion by time block, last 120 days
h = C[(C.d >= TOM - pd.Timedelta(days=120)) & C.price.notna()].copy(); h['g'] = h.he.map(lambda x: 0 if x <= 6 else 1 if x <= 10 else 2 if x <= 16 else 3 if x <= 21 else 4)
coef = {}; base_px = {}
for g_, sub in h.groupby('g'):
    b, a = np.polyfit(sub.cush/1000, np.log1p(sub.price), 1); coef[g_] = (a, b)
sc['A1'] = f'Scenario for {TOM:%Y-%m-%d}: change the yellow inputs; the cushion, thermal remaining, the price forecast and the score move hour by hour'; sc['A1'].font = F(bold=True, size=12)
sc['A2'] = 'Cushion = gas + SC + bio + wind + solar - (load - net imports). Price moves by a log-linear response to cushion fitted per time block on the last 120 days. Score: 5 tight (cushion below the tight line), 4 watch, 3 neutral, 1 long. A scenario, not a forecast.'
inputs = [('Load change (MW, + = more load)', 0, 'e.g. +400 for a colder day than AESO assumes'), ('Wind change (% of forecast, - = less wind)', 0, 'e.g. -50 for a calm evening'),
          ('Solar change (% of forecast)', 0, ''), ('Baseload gas available change (MW, - = outage / trip)', 0, 'e.g. -400 for a CC unit tripping'),
          ('Simple cycle available change (MW)', 0, ''), ('Net imports change (MW, + = more imports)', 0, 'e.g. -300 if BC stops sending'),
          ('Apply from HE', 1, 'hour-ending 1-24'), ('Apply to HE', 24, '')]
for i, (lab, v, note) in enumerate(inputs):
    r = 4 + i; sc.cell(r, 1, lab).font = BLACK; c = sc.cell(r, 4, v); c.fill = YEL; c.font = BLUE; sc.cell(r, 5, note).font = F(color='808080', size=9)
sc['H4'] = 'Score thresholds (cushion, MW)'; sc['H4'].font = F(bold=True)
for i, (lab, v) in enumerate([('Tight (score 5): cushion below', TIGHT), ('Watch (score 4): cushion below', 1800), ('Long (score 1): cushion above', 2500)]):
    sc.cell(5+i, 8, lab); c = sc.cell(5+i, 11, v); c.fill = YEL; c.font = BLUE
sc['S3'] = 'Result'; sc['S3'].font = F(bold=True); sc['V3'] = 'Base'; sc['W3'] = 'Scenario'
res = [('Tightest cushion HE8-23', '=MIN(H23:H38)', '=MIN(I23:I38)'), ('Tightest thermal remaining HE8-23', '=MIN(J23:J38)', '=MIN(K23:K38)'),
       ('Price forecast, HE8-23 avg', '=AVERAGE(L23:L38)', '=AVERAGE(M23:M38)'), ('Price forecast, worst hour', '=MAX(L16:L39)', '=MAX(M16:M39)'),
       ('Tight hours (score 5)', '=COUNTIF(N16:N39,5)', '=COUNTIF(O16:O39,5)'), ('Hours whose score changes', '', '=SUMPRODUCT(--(O16:O39<>N16:N39))')]
for i, (lab, b, s_) in enumerate(res):
    sc.cell(4+i, 19, lab); sc.cell(4+i, 22, b if b else None).number_format = '#,##0'; sc.cell(4+i, 23, s_).number_format = '#,##0'
sc['A13'] = 'Quick scenarios: a CC unit trips in the evening -> Gas -400, HE 17-21 | calm evening -> Wind -60%, HE 16-22 | cold snap -> Load +500 | BC stops sending -> Imports -300. Set everything back to 0 and HE 1-24 for the base case. Yellow = the score changed.'
heads = ['HE', 'In range', 'Wind chg', 'Solar chg', 'Cushion chg', 'Load fc', 'Wind fc', 'Cushion base', 'Cushion scen.', 'Thermal rem base', 'Thermal rem scen.', 'Price fc base', 'Price fc scen.', 'Score base', 'Score scen.', 'b (log $/GW)', 'Gas avail', 'SC avail', 'Bio', 'Solar fc', 'Net imports', 'Hydro+storage']
for j, hh in enumerate(heads, 1): c = sc.cell(15, j, hh); c.font = F(bold=True); c.fill = HDR; c.alignment = Alignment(wrap_text=True)
for he in range(1, 25):
    r = 15 + he; row = gg[gg.he == he]
    if not len(row): continue
    x = row.iloc[0]; g_ = 0 if he <= 6 else 1 if he <= 10 else 2 if he <= 16 else 3 if he <= 21 else 4; a, b = coef[g_]
    vals = {1: he, 2: f'=IF(AND(A{r}>=$D$10,A{r}<=$D$11),1,0)', 3: f'=B{r}*G{r}*$D$5/100', 4: f'=B{r}*T{r}*$D$6/100',
            5: f'=B{r}*(-$D$4+$D$7+$D$8+$D$9)+C{r}+D{r}', 6: round(float(x.load), 0), 7: round(float(x.wind), 0) if not np.isnan(x.wind) else None,
            8: f'=Q{r}+R{r}+S{r}+G{r}+T{r}-(F{r}-U{r})', 9: f'=H{r}+E{r}',
            10: f'=Q{r}-(F{r}-G{r}-T{r}-U{r}-S{r}-V{r})', 11: f'=J{r}+E{r}-B{r}*$D$8-D{r}+B{r}*$D$8',
            12: f'=EXP({a:.4f}+{b:.4f}*H{r}/1000)-1', 13: f'=EXP({a:.4f}+{b:.4f}*I{r}/1000)-1',
            14: f'=IF(H{r}<$K$5,5,IF(H{r}<$K$6,4,IF(H{r}>$K$7,1,3)))', 15: f'=IF(I{r}<$K$5,5,IF(I{r}<$K$6,4,IF(I{r}>$K$7,1,3)))',
            16: round(b, 4), 17: round(float(x.thermal)), 18: round(float(x.sc)), 19: round(float(x['OTHER'])), 20: round(float(x.solar)), 21: round(ni30), 22: round(float(x['HYDRO'] + x['ENERGY STORAGE']))}
    for j, v in vals.items():
        c = sc.cell(r, j, v); c.number_format = '#,##0' if j not in (12, 13, 16) else ('#,##0.0' if j != 16 else '0.0000')
        if j in (6, 7, 17, 18, 19, 20, 21, 22): c.font = BLUE
    sc.cell(r, 11, f'=J{r}+B{r}*$D$7-B{r}*$D$4+B{r}*$D$9+C{r}+D{r}')
sc.conditional_formatting.add('O16:O39', FormulaRule(formula=['O16<>N16'], fill=YEL))
sc['A41'] = 'Sources: AESO gencap available capability (tomorrow hourly), AESO load / wind / solar forecasts, net imports = last-30-day mean actual scheduled. Price response b: log(1+price) per 1,000 MW of cushion, by time block, last 120 days.'
for col, w in zip('ABCDEFGHIJKLMNOPQRSTUVW', [44, 9, 9, 9, 11, 9, 9, 11, 11, 12, 12, 11, 11, 9, 9, 10, 9, 9, 12, 9, 10, 11, 11]): sc.column_dimensions[col].width = w
ch = LineChart(); ch.title = 'Cushion by hour: base vs scenario'; ch.height, ch.width = 7.5, 16
ch.add_data(Reference(sc, min_col=8, min_row=15, max_row=39), titles_from_data=True); ch.add_data(Reference(sc, min_col=9, min_row=15, max_row=39), titles_from_data=True)
ch.set_categories(Reference(sc, min_col=1, min_row=16, max_row=39)); ch.y_axis.title = 'MW'; sc.add_chart(ch, 'A42')
ch = LineChart(); ch.title = 'Price forecast by hour: base vs scenario'; ch.height, ch.width = 7.5, 16
ch.add_data(Reference(sc, min_col=12, min_row=15, max_row=39), titles_from_data=True); ch.add_data(Reference(sc, min_col=13, min_row=15, max_row=39), titles_from_data=True)
ch.set_categories(Reference(sc, min_col=1, min_row=16, max_row=39)); ch.y_axis.title = '$/MWh'; sc.add_chart(ch, 'J42')

# ----------------------------------------------------------------- Charts ----
from openpyxl.drawing.line import LineProperties
def write_charts(name, pos, ws_, CHx, Dx, TOM_, split, cap1, cap2):
    cs = wb.create_sheet(name, pos); DHx = len(Dx) + 1
    def line(title, keys, anchor, ytitle='MW', bar=False):
        c = BarChart() if bar else LineChart(); c.title = title; c.height, c.width = 7.5, 17; c.y_axis.title = ytitle
        for k in keys:
            ca, cb = CHx[k]
            c.add_data(Reference(ws_, min_col=ca, min_row=1, max_row=DHx), titles_from_data=True)
            if split: c.add_data(Reference(ws_, min_col=cb, min_row=1, max_row=DHx), titles_from_data=True)
        c.set_categories(Reference(ws_, min_col=len(KEYS) + 3, min_row=DL, max_row=DHx))
        c.x_axis.tickLblSkip = 7; c.x_axis.tickMarkSkip = 7; c.x_axis.delete = False; c.y_axis.delete = False; c.x_axis.number_format = 'dd-mmm'
        c.legend.position = 'b'
        if bar: c.grouping = 'stacked'; c.overlap = 100
        else:
            for i, sr in enumerate(c.series):
                sr.smooth = False
                if split and i % 2 == 1: sr.graphicalProperties.line.dashStyle = 'dash'
        cs.add_chart(c, anchor)
    def scatter(title, kx, ky, anchor):
        c = ScatterChart(); c.title = title; c.style = 13; c.height, c.width = 7.5, 17; c.x_axis.title = ITEMS[KEYS.index(kx)][2]; c.y_axis.title = ITEMS[KEYS.index(ky)][2]
        c.x_axis.delete = False; c.y_axis.delete = False
        xr = Reference(ws_, min_col=COLIDX[kx], min_row=DL, max_row=DL+NH-1); yr = Reference(ws_, min_col=COLIDX[ky], min_row=DL, max_row=DL+NH-1)
        s_ = Series(yr, xr, title=f'{NH} days before {TOM_:%d-%b-%Y}'); s_.marker.symbol = 'circle'; s_.marker.size = 4; s_.graphicalProperties.line.noFill = True; c.series.append(s_); cs.add_chart(c, anchor)
    cs['A1'] = cap1; cs['A1'].font = F(bold=True, size=12); cs['A2'] = cap2
    line('Load: daily peak, forecast vs actual', ['lf_pk', 'la_pk'], 'A4')
    line('Baseload gas and simple cycle available (HE8-23 avg)', ['th_av', 'sc_av'], 'K4')
    line('Thermal MW unavailable in the 90-day report, and trips', ['plan_out', 'trip'], 'U4', bar=True)
    line('Cushion: tightest HE8-23 vs the tight line', ['cush_min', 'cush_avg', 'tight'], 'A20')
    line('Thermal remaining, tightest HE8-23', ['rem_min'], 'K20')
    line('Wind: forecast vs actual (HE8-23 avg)', ['w_fc', 'w_act'], 'U20')
    line('Pool price: HE8-23 avg vs day-ahead forward', ['px_pk', 'da_pk'], 'A36', '$/MWh')
    line('Peak settle - day-ahead forward (+ = buys won)', ['pk_minus_da'], 'K36', '$/MWh', bar=True)
    line('Imports: ATC and net imports', ['atc', 'ni'], 'U36')
    line('Temperature, province avg: max and mean', ['t_max', 't_avg'], 'A52', 'deg C')
    line('Wind at 100 m, province avg (HE8-23)', ['w100'], 'K52', 'km/h')
    line('Mid-C and AB-NIT gas', ['midc'], 'U52', 'USD/MWh')
    scatter(f'Tightest cushion vs peak settle - forward ({NH} days)', 'cush_min', 'pk_minus_da', 'A68')
    scatter('Wind miss vs peak settle - forward', 'w_miss', 'pk_minus_da', 'K68')
    scatter('Load miss vs peak settle - forward', 'l_miss', 'pk_minus_da', 'U68')
    scatter('Thermal remaining vs pool price HE8-23', 'rem_min', 'px_pk', 'A84')
    return cs
cs = write_charts('Charts', 2, ws, CH, D, TOM, True,
    f'Last {NH} days (solid), next 14 days forecast (solid, past the last actual) and the outlook to day 30 (dashed). Tomorrow = {TOM:%Y-%m-%d}.',
    'Forward: AESO load / wind / solar forecasts and gencap for 14 days, Tesla load to where it reaches, then the same-weekday level of the last 4 weeks. Wind past the 216-h feed: ECMWF outlook if fresh, else wind MW from the 100 m wind-speed forecast (capacity factor by speed, last 365 days), else the calendar-month normal. Weather: Open-Meteo 16-day forecast (wx_fcst.py), then the day-of-year normal. Thermal past gencap = the last gencap day moved by the 90-day outage report. Net imports at the 30-day mean.')
T1, T2 = TOM - pd.DateOffset(years=1), TOM - pd.DateOffset(years=2)
write_charts('Charts_1y', 3, ws1, CH1, D1, T1, False, f'The same window one year ago: {T1 - pd.Timedelta(days=NH):%d-%b-%Y} to {T1 + pd.Timedelta(days=NF):%d-%b-%Y} (all actuals; forecasts are the day-ahead vintages of the time).', 'Data on the Data_1y tab.')
write_charts('Charts_2y', 4, ws2, CH2, D2, T2, False, f'The same window two years ago: {T2 - pd.Timedelta(days=NH):%d-%b-%Y} to {T2 + pd.Timedelta(days=NF):%d-%b-%Y} (all actuals). The composition feed starts 2024-08-31, so earlier days are blank.', 'Data on the Data_2y tab.')

# ------------------------------------------------------------ History -------
# Two views of every settled hour / day since the composition feed starts, from
# the same frames the model reads. Hourly = one row per HE; Daily = the day's
# averages (load peak and tightest cushion as extra columns).
import zipfile
tesla = vint('load_fc', 'Load', 12, 40, 'Tesla')
H = C[C.d < TOM].copy()
H['load_aeso'] = lfc.v.reindex(H.index); H['load_tesla'] = tesla.reindex(H.index)
H['temp'] = wx.set_index('ts').temp.reindex(H.index)
# import ATC by hour: from the archived intertie feeds (day-ahead vintage where one exists)
atc_rows = {}
for z in sorted((U/'archive').glob('*.zip')):
    try:
        zd = pd.Timestamp(z.stem)
        with zipfile.ZipFile(z) as zf:
            if 'intertie.json' not in zf.namelist(): continue
            J = json.loads(zf.read('intertie.json').decode('utf-8-sig'))['return']
    except Exception: continue
    for g_, al in J.items():
        if not isinstance(al, dict) or 'Allocations' not in al or g_ in ('BcMatlFlowgate', 'SystemlFlowgate'): continue
        for a_ in al['Allocations']:
            t = pd.Timestamp(a_['date']) + pd.Timedelta(hours=int(a_['he'])-1)
            if isinstance(a_.get('import'), dict) and t.normalize() >= zd:
                atc_rows.setdefault((t, zd), 0.0); atc_rows[(t, zd)] += float(a_['import'].get('atc') or 0)
if atc_rows:
    A = pd.Series(atc_rows); A.index.names = ['t', 'zd']; A = A.reset_index()
    A['lead'] = (A.t.dt.normalize() - A.zd).dt.days
    A = A[A.lead >= 0].sort_values('lead').groupby('t')[0].first()      # closest vintage on or before the day
    H['atc'] = A.reindex(H.index)
else: H['atc'] = np.nan
# the model's own hourly call, day-ahead vintage, from the forecast journal (only from when the journal starts)
jf = U/'verify/hourly.csv'
if jf.exists():
    J = pd.read_csv(jf, parse_dates=['published', 'target'])
    J = J[J.lead_h > 0].copy(); J['da'] = J.published.dt.normalize() < J.target.dt.normalize()
    J = J.sort_values(['da', 'published']).groupby('target').last()          # prefer a vintage published the day before
    for k in ('p25', 'p50', 'p75', 'p90', 'ev'): H[k] = J[k].reindex(H.index)
else:
    for k in ('p25', 'p50', 'p75', 'p90', 'ev'): H[k] = np.nan
H = H.sort_index(ascending=False)

HCOLS = [  # key, header, format
 ('temp', 'Temp, province avg (C)', '0.0'),
 ('load_tesla', 'Load fc Tesla (MW)', '#,##0'), ('load_aeso', 'Load fc AESO (MW)', '#,##0'), ('ail', 'Load actual (MW)', '#,##0'),
 ('cogen', 'Cogen (MW)', '#,##0'), ('cc', 'Comb cycle (MW)', '#,##0'), ('gfs', 'Gas steam (MW)', '#,##0'), ('sc', 'Simple cycle (MW)', '#,##0'),
 ('wind', 'Wind (MW)', '#,##0'), ('solar', 'Solar (MW)', '#,##0'),
 ('atc', 'Import ATC (MW)', '#,##0'), ('net_imports_actual_scheduled', 'Net imports (MW)', '#,##0'),
 ('rem', 'MW before simple cycle (MW)', '#,##0'), ('cush', 'Cushion (MW)', '#,##0'),
 ('p25', 'P25 ($/MWh)', '#,##0.0'), ('p50', 'P50 ($/MWh)', '#,##0.0'), ('p75', 'P75 ($/MWh)', '#,##0.0'), ('p90', 'P90 ($/MWh)', '#,##0.0'), ('ev', 'Fair value ($/MWh)', '#,##0.0'),
 ('price', 'Pool price ($/MWh)', '#,##0.0'),
]
def sheet_rows(ws_, top, frame, first_cols, getters):
    for j, h in enumerate(first_cols + [h for _, h, _ in HCOLS], 1):
        c = ws_.cell(top-1, j, h); c.font = F(bold=True); c.fill = HDR; c.alignment = Alignment(wrap_text=True, vertical='top')
    for n, (idx, r) in enumerate(frame.iterrows()):
        row = top + n
        for j, v in enumerate(getters(idx, r), 1):
            c = ws_.cell(row, j, v); c.font = BLACK
            if j == 1: c.number_format = 'yyyy-mm-dd'
        for j, (k, h, nf) in enumerate(HCOLS, len(first_cols)+1):
            v = r.get(k); c = ws_.cell(row, j, None if (v is None or pd.isna(v)) else round(float(v), 2)); c.font = BLUE; c.number_format = nf
    ws_.row_dimensions[top-1].height = 45
    for j in range(1, len(first_cols)+len(HCOLS)+1): ws_.column_dimensions[CL(j)].width = 12

# ---- Hourly
hh = wb.create_sheet('History_Hourly')
hh['A1'] = 'History, hourly: every settled hour since the feed starts (newest day first, HE1-24)'; hh['A1'].font = F(bold=True, size=12)
hh['A2'] = 'Same feeds and definitions as the model. MW before simple cycle = baseload gas (cogen + CC + GFS) available minus what it must serve after wind, solar, imports, hydro, storage and bio. Cushion = gas + SC + bio + wind + solar - (load - net imports). P25-P90 and fair value = the model\'s day-ahead call for that hour from the forecast journal (verify\\hourly.csv), blank before it started. Import ATC from the archived intertie feeds, blank before the archive started.'
Hh = H.copy(); Hh['he_'] = Hh.he; Hh = Hh.sort_values(['d', 'he_'], ascending=[False, True])
sheet_rows(hh, 5, Hh, ['Date', 'HE'], lambda idx, r: [idx.normalize().to_pydatetime(), int(r.he)])
hh.freeze_panes = 'C5'; hh.auto_filter.ref = f'A4:{CL(2+len(HCOLS))}{4+len(Hh)}'
NHH = len(Hh)

# ---- Daily
x = H; pk = x[PK(x.he)]; gd, gp = x.groupby('d'), pk.groupby('d')
HD = gd[[k for k, *_ in HCOLS]].mean()
HD['load_pk'] = gd.ail.max(); HD['cush_min'] = gp.cush.min(); HD['rem_min'] = gp.rem.min(); HD['px_pk'] = gp.price.mean(); HD['px_ll'] = x[~PK(x.he)].groupby('d').price.mean()
HD['t_max'] = gd.temp.max(); HD['n'] = gd.price.count(); HD = HD[HD.n >= 20].sort_index(ascending=False)
HD['wcap'] = gd.wcap.mean(); HD['w_cf'] = 100*HD.wind/HD.wcap
# cross-check against the Data tab
chk = D[D.Period == 'History'].set_index('Date')
for a_, b_ in (('la_pk', 'load_pk'), ('cush_min', 'cush_min'), ('rem_min', 'rem_min'), ('px_flat', 'price'), ('px_pk', 'px_pk')):
    both = chk[a_].dropna().index.intersection(HD.index); dif = (chk.loc[both, a_] - HD.loc[both, b_]).abs().max() if len(both) else 0
    assert dif < 0.01, ('History_Daily differs from Data', a_, dif)
hs = wb.create_sheet('History_Daily')
hs['A1'] = 'History, daily: the day\'s averages of the hourly view, plus load peak, tightest cushion, HL / LL price'; hs['A1'].font = F(bold=True, size=12)
hs['A2'] = 'Change the yellow cells to pull the days that looked like the one you are trading; Match = 1 marks them (filter on it) and they are listed at the top right.'
EXTRA = [('load_pk', 'Load peak (MW)', '#,##0'), ('cush_min', 'Cushion, tightest HE8-23 (MW)', '#,##0'), ('rem_min', 'MW before SC, tightest HE8-23 (MW)', '#,##0'),
         ('px_pk', 'HL price HE8-23 ($/MWh)', '#,##0.0'), ('px_ll', 'LL price HE1-7, 24 ($/MWh)', '#,##0.0'), ('t_max', 'Max temp (C)', '0.0'), ('w_cf', 'Wind capacity factor (%)', '0.0'), ('calm', 'Calm / Windy', '@')]
TOP, HRH = 16, 15; N = len(HD); BOT = TOP + N - 1
allcols = [('Date', '', ''), ('Month', '', ''), ('Match', '', ''), ('Rank', '', '')] + HCOLS + EXTRA
col = {k: i+1 for i, (k, *_ ) in enumerate(allcols)}
def hr(k): return f"${CL(col[k])}${TOP}:${CL(col[k])}${BOT}"
lab = [('Cushion, tightest HE8-23 (MW)', None, 'defaults to tomorrow from Summary; overwrite'), ('Cushion band, +/- (MW)', 300, ''),
       ('Month (1-12)', None, 'defaults to tomorrow'), ('Months either side', 1, '0 = same month only'), ('Wind (Any / Calm / Windy)', 'Any', 'Calm = capacity factor below 16%')]
for i, (t, v, note) in enumerate(lab):
    r = 4 + i; hs.cell(r, 1, t).font = BLACK; c = hs.cell(r, 3, v); c.fill = YEL; c.font = BLUE; hs.cell(r, 4, note).font = F(color='808080', size=9)
hs['C4'] = f"=Summary!D{SUMROW['cush_min']}"; hs['C6'] = '=MONTH(Summary!$D$2)'
hs['F3'] = 'Days that matched'; hs['F3'].font = F(bold=True)
outs = [('Days', f'=COUNTIF({hr("Match")},1)', '0'), ('Avg price 7x24', f'=IFERROR(AVERAGEIFS({hr("price")},{hr("Match")},1),"")', '#,##0.0'),
        ('Avg HL', f'=IFERROR(AVERAGEIFS({hr("px_pk")},{hr("Match")},1),"")', '#,##0.0'), ('Avg LL', f'=IFERROR(AVERAGEIFS({hr("px_ll")},{hr("Match")},1),"")', '#,##0.0'),
        ('Highest avg price', f'=IFERROR(_xlfn.MAXIFS({hr("price")},{hr("Match")},1),"")', '#,##0.0'), ('Lowest avg price', f'=IFERROR(_xlfn.MINIFS({hr("price")},{hr("Match")},1),"")', '#,##0.0'),
        ('Avg cushion, tightest', f'=IFERROR(AVERAGEIFS({hr("cush_min")},{hr("Match")},1),"")', '#,##0'), ('Avg max temp', f'=IFERROR(AVERAGEIFS({hr("t_max")},{hr("Match")},1),"")', '0.0')]
for i, (t, fm, nf) in enumerate(outs):
    r = 4 + i; hs.cell(r, 6, t).font = BLACK; c = hs.cell(r, 7, fm); c.number_format = nf; c.font = BLACK
NL = 60
hs['I3'] = 'The days it matched (newest first)'; hs['I3'].font = F(bold=True)
mh = ['#', 'Date', 'Avg price', 'HL', 'LL', 'Cushion tightest', 'Max temp', 'Wind CF %', 'Calm / Windy']
mk = [None, None, 'price', 'px_pk', 'px_ll', 'cush_min', 't_max', 'w_cf', 'calm']
for j, h in enumerate(mh): c = hs.cell(4, 9+j, h); c.font = F(bold=True); c.fill = HDR
for i in range(NL):
    r = 5 + i; hs.cell(r, 9, i+1).number_format = '0'
    hs.cell(r, 10, f'=IFERROR(INDEX($A${TOP}:$A${BOT},MATCH(I{r},{hr("Rank")},0)),"")').number_format = 'yyyy-mm-dd'
    for j, k in enumerate(mk):
        if k is None: continue
        c = hs.cell(r, 9+j, f'=IFERROR(INDEX({hr(k)},MATCH($I{r},{hr("Rank")},0)),"")'); c.number_format = '#,##0.0' if k != 'calm' else '@'
heads = ['Date', 'Month', 'Match', 'Match #'] + [h for _, h, _ in HCOLS] + [h for _, h, _ in EXTRA]
for j, h in enumerate(heads, 1):
    c = hs.cell(HRH, j, h); c.font = F(bold=True); c.fill = HDR; c.alignment = Alignment(wrap_text=True, vertical='top')
for n, (d, r) in enumerate(HD.iterrows()):
    row = TOP + n
    hs.cell(row, 1, d.to_pydatetime()).number_format = 'yyyy-mm-dd'; hs.cell(row, 1).font = BLACK
    hs.cell(row, 2, f'=MONTH(A{row})').number_format = '0'
    cu, mo, wc = f'{CL(col["cush_min"])}{row}', f'B{row}', f'{CL(col["calm"])}{row}'
    hs.cell(row, 3, f'=IF(AND(ISNUMBER({cu}),ISNUMBER($C$4),ABS({cu}-$C$4)<=$C$5,ABS(MOD({mo}-$C$6+6,12)-6)<=$C$7,OR($C$8="Any",{wc}=$C$8)),1,0)').number_format = '0'
    hs.cell(row, 4, f'=IF(C{row}=1,COUNTIF($C${TOP}:C{row},1),"")').number_format = '0'
    for k, h, nf in HCOLS + EXTRA:
        if k == 'calm': v = ('Calm' if r.w_cf < 100*CF_CALM else 'Windy') if pd.notna(r.w_cf) else None
        else:
            v = r.get(k); v = None if (v is None or pd.isna(v)) else round(float(v), 2)
        c = hs.cell(row, col[k], v); c.font = BLUE; c.number_format = nf
hs.conditional_formatting.add(f'C{TOP}:C{BOT}', CellIsRule(operator='equal', formula=['1'], fill=PatternFill('solid', fgColor='FFF2CC')))
hs.freeze_panes = f'E{TOP}'; hs.row_dimensions[HRH].height = 60; hs.auto_filter.ref = f'A{HRH}:{CL(len(heads))}{BOT}'
for j in range(1, len(heads)+1): hs.column_dimensions[CL(j)].width = 12
hs.column_dimensions['A'].width = 30; hs.column_dimensions['F'].width = 22; hs.column_dimensions['B'].width = 7; hs.column_dimensions['C'].width = 7; hs.column_dimensions['D'].width = 8
sc2 = ScatterChart(); sc2.title = 'Tightest cushion vs HL price, all history'; sc2.style = 13; sc2.height, sc2.width = 9, 16
sc2.x_axis.title = 'Cushion, tightest HE8-23 (MW)'; sc2.y_axis.title = '$/MWh'
xs = Reference(hs, min_col=col['cush_min'], min_row=TOP, max_row=BOT); ys = Reference(hs, min_col=col['px_pk'], min_row=TOP, max_row=BOT)
se = Series(ys, xs, title='days'); se.marker.symbol = 'circle'; se.marker.size = 3; se.graphicalProperties.line.noFill = True; sc2.series.append(se)
hs.add_chart(sc2, 'S3')
print('History_Hourly rows', NHH, ' History_Daily rows', N, 'from', HD.index.min().date(), 'to', HD.index.max().date(),
      ' model calls from', H.ev.dropna().index.min(), ' ATC from', H.atc.dropna().index.min())

# ----------------------------------------------------------------- README ----
rd = wb.create_sheet('README', 0)
txt = ['Pre-model checklist (Alberta) -- how to read this workbook', '',
 'Summary: one row per item. Tomorrow vs the average of the last 14 and last 30 delivery days, the 30-day spread (SD, min, max), and the next-14-day average vs the last 30.',
 '   Flag HIGH / LOW = tomorrow is at least one standard deviation above / below the last 30 days.',
 'Next_14_Days: each of the next 14 days, raw value then the difference vs the last-14 and last-30 means. Red / green = the difference is bigger than one 30-day SD.',
 'Last_30_Days: each of the last 30 days (newest first), raw value then the difference vs the last-14 and last-30 means.',
 "Scenario: change load, wind, solar, baseload gas / simple cycle availability and net imports (optionally for a range of hours) and see tomorrow's cushion, thermal remaining, price forecast and score change by hour.",
 'Charts: the key series over the last 75 days, the next 14 (forecasts) and the outlook to day 30 (dashed), plus scatter plots over the 75 days. Charts_1y / Charts_2y: the same window one and two years earlier, from the Data_1y / Data_2y tabs.',
 'Data: the raw daily numbers (blue = inputs written by the script; yellow row = tomorrow). Every average and difference elsewhere is a formula on this sheet. Green values = links to Data.',
 'History_Hourly: every settled hour (newest day first): temperature, load (Tesla forecast, AESO forecast, actual), cogen / CC / gas steam / simple cycle, wind, solar, import ATC, net imports, MW before simple cycle, cushion, the model\'s P25 / P50 / P75 / P90 and fair value for that hour, and the pool price.',
 'History_Daily: the same as daily averages, plus load peak, tightest cushion, HL / LL price. Yellow cells at the top pull the days with a similar cushion (same month, calm / windy); Match = 1 marks them and they are listed at the top right.', '',
 'What each row is',
 "History rows use what was known at the day-ahead vintage (AESO load / wind forecasts issued 12-40 h ahead) plus what then happened (actual load, wind, availability, pool price).",
 "Tomorrow and forward rows use AESO's 14-day load forecast, the 216-hour wind and solar forecasts (ECMWF outlook beyond them), the gencap available-capability feed (15 days) and the 90-day outage report.",
 'Peak = HE8-23 (Alberta convention). Weather is the average of Calgary, Edmonton and Pincher Creek (Open-Meteo); forward weather is blank until wx.py is run for the forecast.',
 'Cushion = gas + SC + bio + wind + solar - (load - net imports). Thermal remaining = baseload gas (cogen + CC + GFS) available - what it must serve after wind, solar, imports, hydro, storage and bio.',
 'Forward cushion assumes net imports at their last-30-day mean; the Scenario tab lets you change that.',
 'Items that cannot exist yet are blank: actuals for tomorrow and beyond, forward prices past the last curve settle, trips and misses for unfinished days.', '',
 'How to use it before the model',
 '1. Summary flags: what is unusual tomorrow vs the last month (HIGH / LOW).',
 '2. Cushion and thermal remaining: below 1,200 MW at the tightest peak hour is the spike zone (measured: 55% of such days print a $300 hour).',
 '3. Wind: the forecast miss has been the largest source of price error at every horizon; a low-wind forecast on a cold or thin-thermal day is a coin flip for a spike.',
 '4. Outages: the 90-day report is the plan; it runs light and slips. Trips are what the day-ahead view misses.',
 '5. Next_14_Days: step changes in scheduled availability, load and wind that are coming.', '',
 'Sources', 'CANPOWER composition (actuals); vendor load / wind forecast vintages (Warehouse); AESO API (load, gencap, ITC, pool price); AESO ETS wind / solar / outage reports; forward curve files (AESO daily, Mid-C, AB-NIT); Open-Meteo.',
 f'Built for delivery day {TOM:%Y-%m-%d}. Rebuild: python checklist_ab.py . Checklist_Deviations_AB.xlsx  (add a date as a third argument to build for another delivery day).']
for i, t in enumerate(txt, 1): rd.cell(i, 1, t).font = F(bold=(i == 1 or t in ('What each row is', 'How to use it before the model', 'Sources')), size=12 if i == 1 else 10)
rd.column_dimensions['A'].width = 160
for s in wb.worksheets:
    for row in s.iter_rows():
        for c in row:
            if c.value is not None and c.font.name != 'Arial': c.font = Font(name='Arial', size=c.font.size or 10, bold=c.font.bold, color=c.font.color)
wb.save(OUT); print('saved', OUT, 'rows', NR)
