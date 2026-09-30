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
NH, NF = 30, 13
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
dates = pd.date_range(TOM - pd.Timedelta(days=NH), TOM + pd.Timedelta(days=NF))
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
recs = []
for d in dates:
    r = {'Date': d, 'Period': 'History' if d < TOM else ('Tomorrow' if d == TOM else 'Forward')}
    H = d < TOM
    # load
    if H:
        r['lf_pk'] = daymax(lfc, 'v', d); r['lf_avg'] = pkavg(lfc, 'v', d); r['la_pk'] = daymax(C, 'ail', d)
    else:
        r['lf_pk'] = daymax(ld, 'fc', d); r['lf_avg'] = pkavg(ld, 'fc', d); r['la_pk'] = None
    r['l_miss'] = (r['la_pk'] - r['lf_pk']) if (r['la_pk'] is not None and r['lf_pk'] is not None) else None
    # thermal
    if H:
        r['th_av'] = pkavg(C, 'thermal', d); r['sc_av'] = pkavg(C, 'sc', d)
        g = C[C.d == d].thermal; r['trip'] = float(-(g.diff().min())) if len(g) > 1 else None
    else:
        r['th_av'] = pkavg(gc, 'thermal', d); r['sc_av'] = pkavg(gc, 'sc', d); r['trip'] = None
    r['plan_out'] = float(orep.thermal_out.get(d, np.nan)) if d in orep.index else None
    # renewables
    if H:
        r['w_fc'] = pkavg(wfc, 'v', d); r['w_act'] = pkavg(C, 'wind', d); r['sol_fc'] = daymax(C, 'solar', d)
    else:
        r['w_fc'] = pkavg(W, 'ml', d) if (W.d == d).sum() >= 16 else (float(ol.wind.get(d, np.nan)) if d in ol.index else None)
        r['w_act'] = None; r['sol_fc'] = daymax(S, 'ml', d) if (S.d == d).any() else None
    r['w_miss'] = (r['w_act'] - r['w_fc']) if (r['w_act'] is not None and r['w_fc'] is not None) else None
    if H:
        wd = dmean(C, 'wind', d); r['w_cf'] = 100*wd/float(C[C.d == d].wcap.mean()) if wd is not None else None
    else:
        wd = dmean(W, 'ml', d) if (W.d == d).sum() >= 16 else (float(ol.wind.get(d, np.nan)) if d in ol.index else None)
        r['w_cf'] = 100*wd/WCAP_NOW if wd is not None and not np.isnan(wd) else None
    # supply
    if H:
        r['cush_min'] = daymin_pk(C, 'cush', d); r['cush_avg'] = pkavg(C, 'cush', d); r['rem_min'] = daymin_pk(C, 'rem', d)
    else:
        # forward cushion from gencap + AESO forecasts, hour by hour
        gg = gc[(gc.d == d)].copy()
        if len(gg) and (ld.d == d).any():
            gg['load'] = ld.fc.reindex(gg.index); gg['wind'] = W.ml.reindex(gg.index) if (W.d == d).sum() >= 16 else (float(ol.wind.get(d, np.nan)) if d in ol.index else np.nan)
            gg['solar'] = S.ml.reindex(gg.index).fillna(0) if (S.d == d).any() else 0
            gg['cushf'] = gg.thermal + gg.sc + gg['OTHER'] + gg.wind + gg.solar - (gg.load - ni30)
            gg['remf'] = gg.thermal - (gg.load - gg.wind - gg.solar - ni30 - gg['HYDRO'] - gg['ENERGY STORAGE'] - gg['OTHER'])
            p = gg[PK(gg.he)]
            r['cush_min'] = float(p.cushf.min()) if p.cushf.notna().any() else None; r['cush_avg'] = float(p.cushf.mean()) if p.cushf.notna().any() else None
            r['rem_min'] = float(p.remf.min()) if p.remf.notna().any() else None
        else: r['cush_min'] = r['cush_avg'] = r['rem_min'] = None
    # interties / neighbours
    r['atc'] = float(atc.get(d, np.nan)) if d in atc.index else None
    r['ni'] = dmean(C, 'net_imports_actual_scheduled', d) if H else None
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
    else: r['t_max'] = r['t_avg'] = r['w100'] = r['rad'] = r['cloud'] = None
    r['tight'] = TIGHT
    recs.append(r)
D = pd.DataFrame(recs)
for c in D.columns:
    if c not in ('Date', 'Period'): D[c] = pd.to_numeric(D[c], errors='coerce')

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
ws = wb.active; ws.title = 'Data'
ws.cell(1, 1, 'Date').font = F(bold=True); ws.cell(1, 2, 'Period').font = F(bold=True)
for k, g, lab, u, src in ITEMS:
    c = ws.cell(1, COLIDX[k], f'{lab} ({u})'); c.font = F(bold=True); c.alignment = Alignment(wrap_text=True, vertical='top')
for i, r in D.iterrows():
    row = i + 2; ws.cell(row, 1, r.Date.to_pydatetime()).number_format = 'yyyy-mm-dd'; ws.cell(row, 1).font = BLACK
    ws.cell(row, 2, r.Period).font = BLACK
    for k in KEYS:
        v = r[k]; c = ws.cell(row, COLIDX[k], None if pd.isna(v) else round(float(v), 2)); c.font = BLUE
        c.number_format = '#,##0.00' if k in ('gas',) else ('#,##0.0' if k in ('t_max', 't_avg', 'w100', 'rad', 'cloud', 'w_cf', 'px_flat', 'px_pk', 'px_max', 'da_pk', 'da_flat', 'pk_minus_da', 'midc') else '#,##0')
        if r.Period == 'Tomorrow': c.fill = YEL
    if r.Period == 'Tomorrow': ws.cell(row, 1).fill = YEL; ws.cell(row, 2).fill = YEL
ws.freeze_panes = 'C2'; ws.column_dimensions['A'].width = 12; ws.column_dimensions['B'].width = 10
for k in KEYS: ws.column_dimensions[CL(COLIDX[k])].width = 16
ws.row_dimensions[1].height = 60
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
cs = wb.create_sheet('Charts', 2)
cs['A1'] = f'How things are projecting: last 30 days (actual / known at the bid) and the next 14 (tomorrow {TOM:%Y-%m-%d} + 13, AESO schedules and forecasts)'; cs['A1'].font = F(bold=True, size=12)
cs['A2'] = 'Forward values are forecasts / schedules; prices and actuals stop at the last finished day. Scatter charts use the last 30 days only.'
def line(title, keys, anchor, ytitle='MW', bar=False):
    c = BarChart() if bar else LineChart(); c.title = title; c.height, c.width = 7.5, 17; c.y_axis.title = ytitle
    for k in keys: c.add_data(Reference(ws, min_col=COLIDX[k], min_row=1, max_row=DH), titles_from_data=True)
    c.set_categories(Reference(ws, min_col=1, min_row=DL, max_row=DH)); c.x_axis.number_format = 'mm-dd'
    if bar: c.grouping = 'stacked'; c.overlap = 100
    cs.add_chart(c, anchor)
line('Load: daily peak, forecast vs actual', ['lf_pk', 'la_pk'], 'A4')
line('Baseload gas and simple cycle available (HE8-23 avg)', ['th_av', 'sc_av'], 'K4')
line('Thermal MW unavailable in the 90-day report, and trips', ['plan_out', 'trip'], 'U4', bar=True)
line('Cushion: tightest HE8-23 vs the tight line', ['cush_min', 'cush_avg', 'tight'], 'A20')
line('Thermal remaining, tightest HE8-23', ['rem_min'], 'K20')
line('Wind: forecast vs actual (HE8-23 avg)', ['w_fc', 'w_act'], 'U20')
line('Pool price: HE8-23 avg vs day-ahead forward', ['px_pk', 'da_pk'], 'A36', '$/MWh')
line('Peak settle - day-ahead forward (+ = buys won)', ['pk_minus_da'], 'K36', '$/MWh', bar=True)
line('Imports: ATC and actual net imports', ['atc', 'ni'], 'U36')
line('Temperature, province avg: max and mean', ['t_max', 't_avg'], 'A52', 'deg C')
line('Wind at 100 m, province avg (HE8-23)', ['w100'], 'K52', 'km/h')
line('Mid-C and AB-NIT gas', ['midc'], 'U52', 'USD/MWh')
def scatter(title, kx, ky, anchor):
    c = ScatterChart(); c.title = title; c.style = 13; c.height, c.width = 7.5, 17; c.x_axis.title = ITEMS[KEYS.index(kx)][2]; c.y_axis.title = ITEMS[KEYS.index(ky)][2]
    xr = Reference(ws, min_col=COLIDX[kx], min_row=DL, max_row=DL+NH-1); yr = Reference(ws, min_col=COLIDX[ky], min_row=DL, max_row=DL+NH-1)
    s = Series(yr, xr, title='last 30 days'); s.marker.symbol = 'circle'; s.graphicalProperties.line.noFill = True; c.series.append(s); cs.add_chart(c, anchor)
scatter('Tightest cushion vs peak settle - forward (last 30 days)', 'cush_min', 'pk_minus_da', 'A68')
scatter('Wind miss vs peak settle - forward', 'w_miss', 'pk_minus_da', 'K68')
scatter('Load miss vs peak settle - forward', 'l_miss', 'pk_minus_da', 'U68')
scatter('Thermal remaining vs pool price HE8-23', 'rem_min', 'px_pk', 'A84')

# --------------------------------------------------------------- Historical --
# One row per settled day since the composition feed starts. Same frames, same definitions
# as the Data tab (peak = HE8-23, cushion / thermal remaining as above), so a date that is
# on both tabs shows the same numbers on both.
x = C[C.d < TOM].copy(); pk = x[PK(x.he)]
gd, gp = x.groupby('d'), pk.groupby('d')
HD = pd.DataFrame({
    'la_pk': gd.ail.max(), 'la_avg': gp.ail.mean(),
    'w_act': gp.wind.mean(), 'w_day': gd.wind.mean(), 'wcap': gd.wcap.mean(),
    'sol_max': gd.solar.max(),
    'th_av': gp.thermal.mean(), 'sc_av': gp.sc.mean(), 'hydro': gd.hydro.mean(), 'ni': gd.net_imports_actual_scheduled.mean(),
    'cush_min': gp.cush.min(), 'cush_avg': gp.cush.mean(), 'rem_min': gp.rem.min(),
    'trip': -gd.thermal.apply(lambda g: g.diff().min()),
    'px_n': gd.price.count(), 'px_flat': gd.price.mean(), 'px_pk': gp.price.mean(), 'px_ll': x[~PK(x.he)].groupby('d').price.mean(), 'px_max': gd.price.max(), 'n100': gd.price.apply(lambda g: int((g > 100).sum())),
})
HD = HD[HD.px_n >= 20].drop(columns='px_n')
HD['w_cf'] = 100*HD.w_day/HD.wcap
wg = wx[wx.d < TOM].groupby('d'); HD['t_max'] = wg.temp.max(); HD['t_avg'] = wg.temp.mean(); HD['w100'] = wx[(wx.d < TOM) & PK(wx.he)].groupby('d').wind.mean()
HD['da_pk'] = da['XDQ'].reindex(HD.index) if 'XDQ' in da else np.nan; HD['da_flat'] = da['XDT'].reindex(HD.index) if 'XDT' in da else np.nan
HD['gas'] = nb['XBG'].reindex(HD.index) if fe is not None and 'XBG' in nb else np.nan
HD['midc'] = nb['MPD'].reindex(HD.index) if fe is not None and 'MPD' in nb else np.nan
HD = HD.sort_index(ascending=False)
# cross-check against the Data tab: same day, same number
chk = D[D.Period == 'History'].set_index('Date')
for k in ('la_pk', 'w_act', 'th_av', 'sc_av', 'cush_min', 'cush_avg', 'rem_min', 'px_flat', 'px_pk', 'px_max', 'n100', 'ni', 'trip', 'w_cf'):
    both = chk[k].dropna().index.intersection(HD.index); dif = (chk.loc[both, k] - HD.loc[both, k]).abs().max() if len(both) else 0
    assert dif < 0.01, ('Historical differs from Data', k, dif)

HCOLS = [  # key, header, format
 ('px_flat', 'Avg price 7x24 ($/MWh)', '#,##0.0'), ('px_pk', 'HL price HE8-23 ($/MWh)', '#,##0.0'), ('px_ll', 'LL price HE1-7, 24 ($/MWh)', '#,##0.0'),
 ('t_max', 'Max temp, province avg (C)', '0.0'), ('t_avg', 'Mean temp, province avg (C)', '0.0'),
 ('la_pk', 'Load peak (MW)', '#,##0'), ('la_avg', 'Load HE8-23 avg (MW)', '#,##0'),
 ('w_act', 'Wind HE8-23 avg (MW)', '#,##0'), ('w_day', 'Wind daily avg (MW)', '#,##0'), ('wcap', 'Wind capacity, rolling max (MW)', '#,##0'), ('w_cf', 'Wind capacity factor (%)', '0.0'), ('calm', 'Calm / Windy', '@'),
 ('sol_max', 'Solar daily max (MW)', '#,##0'), ('hydro', 'Hydro daily avg (MW)', '#,##0'), ('ni', 'Net imports daily avg (MW)', '#,##0'),
 ('th_av', 'Baseload gas available HE8-23 (MW)', '#,##0'), ('sc_av', 'Simple cycle available HE8-23 (MW)', '#,##0'), ('trip', 'Largest hourly drop in baseload gas (MW)', '#,##0'),
 ('cush_min', 'Cushion, tightest HE8-23 (MW)', '#,##0'), ('cush_avg', 'Cushion HE8-23 avg (MW)', '#,##0'), ('rem_min', 'Thermal remaining, tightest HE8-23 (MW)', '#,##0'),
 ('gas', 'AB-NIT gas ($/GJ)', '#,##0.00'), ('midc', 'Mid-C (USD/MWh)', '#,##0.0'),
 ('w100', 'Wind at 100 m HE8-23 (km/h)', '0.0'),
]
hs = wb.create_sheet('Historical')
hs['A1'] = 'Historical: every settled day, the fundamentals as the model sees them, and what the pool did'; hs['A1'].font = F(bold=True, size=12)
hs['A2'] = 'Same feeds and definitions as the Data tab. Blue = written by the script. Change the yellow cells to pull the days that looked like the one you are trading; the Match column marks them (filter on it).'
lab = [('Cushion, tightest HE8-23 (MW)', None, 'defaults to tomorrow from Summary; overwrite'), ('Cushion band, +/- (MW)', 300, ''),
       ('Month (1-12)', None, 'defaults to tomorrow'), ('Months either side', 1, '0 = same month only'), ('Wind (Any / Calm / Windy)', 'Any', 'Calm = capacity factor below 16%')]
HR0, HRH, TOP = 14, 15, 16          # panel rows 4-11, headers row 15, first data row 16
N = len(HD); BOT = TOP + N - 1
col = {k: i+1 for i, (k, *_ ) in enumerate([('Date', '', ''), ('Month', '', ''), ('Match', '', ''), ('Rank', '', '')] + HCOLS)}
def hr(k): return f"${CL(col[k])}${TOP}:${CL(col[k])}${BOT}"
for i, (t, v, note) in enumerate(lab):
    r = 4 + i; hs.cell(r, 1, t).font = BLACK; c = hs.cell(r, 3, v); c.fill = YEL; c.font = BLUE; hs.cell(r, 4, note).font = F(color='808080', size=9)
hs['C4'] = f"=Summary!D{SUMROW['cush_min']}"; hs['C6'] = '=MONTH(Summary!$D$2)'
hs['F3'] = 'Days that matched'; hs['F3'].font = F(bold=True)
outs = [('Days', f'=COUNTIF({hr("Match")},1)', '0'), ('Avg price 7x24', f'=IFERROR(AVERAGEIFS({hr("px_flat")},{hr("Match")},1),"")', '#,##0.0'),
        ('Avg HL', f'=IFERROR(AVERAGEIFS({hr("px_pk")},{hr("Match")},1),"")', '#,##0.0'), ('Avg LL', f'=IFERROR(AVERAGEIFS({hr("px_ll")},{hr("Match")},1),"")', '#,##0.0'),
        ('Highest avg price', f'=IFERROR(_xlfn.MAXIFS({hr("px_flat")},{hr("Match")},1),"")', '#,##0.0'), ('Lowest avg price', f'=IFERROR(_xlfn.MINIFS({hr("px_flat")},{hr("Match")},1),"")', '#,##0.0'),
        ('Avg cushion, tightest', f'=IFERROR(AVERAGEIFS({hr("cush_min")},{hr("Match")},1),"")', '#,##0'), ('Avg max temp', f'=IFERROR(AVERAGEIFS({hr("t_max")},{hr("Match")},1),"")', '0.0')]
for i, (t, fm, nf) in enumerate(outs):
    r = 4 + i; hs.cell(r, 6, t).font = BLACK; c = hs.cell(r, 7, fm); c.number_format = nf; c.font = BLACK
# the matched days themselves, listed (up to NL), via a rank helper column
NL = 60
hs['I3'] = 'The days it matched (newest first)'; hs['I3'].font = F(bold=True)
mh = ['#', 'Date', 'Avg price', 'HL', 'LL', 'Cushion tightest', 'Max temp', 'Wind CF %', 'Calm / Windy']
mk = [None, None, 'px_flat', 'px_pk', 'px_ll', 'cush_min', 't_max', 'w_cf', 'calm']
for j, h in enumerate(mh): c = hs.cell(4, 9+j, h); c.font = F(bold=True); c.fill = HDR
for i in range(NL):
    r = 5 + i; hs.cell(r, 9, i+1).number_format = '0'
    hs.cell(r, 10, f'=IFERROR(INDEX($A${TOP}:$A${BOT},MATCH(I{r},{hr("Rank")},0)),"")').number_format = 'yyyy-mm-dd'
    for j, k in enumerate(mk):
        if k is None: continue
        c = hs.cell(r, 9+j, f'=IFERROR(INDEX({hr(k)},MATCH($I{r},{hr("Rank")},0)),"")'); c.number_format = '#,##0.0' if k != 'calm' else '@'
hs.cell(13, 1, 'Analog avg = the average 7x24 price of the other days in the same calendar month with a tightest cushion within the band above. Anomaly = this day minus that.').font = F(color='808080', size=9)
heads = ['Date', 'Month', 'Match', 'Match #'] + [h for _, h, _ in HCOLS] + ['Analog avg 7x24 ($/MWh)', 'Anomaly vs analogs ($/MWh)']
for j, h in enumerate(heads, 1):
    c = hs.cell(HRH, j, h); c.font = F(bold=True); c.fill = HDR; c.alignment = Alignment(wrap_text=True, vertical='top')
cA, cB = len(heads) - 1, len(heads)
for n, (d, r) in enumerate(HD.iterrows()):
    row = TOP + n
    hs.cell(row, 1, d.to_pydatetime()).number_format = 'yyyy-mm-dd'; hs.cell(row, 1).font = BLACK
    hs.cell(row, 2, f'=MONTH(A{row})').number_format = '0'
    cu, mo, wc = f'{CL(col["cush_min"])}{row}', f'B{row}', f'{CL(col["calm"])}{row}'
    hs.cell(row, 3, f'=IF(AND(ISNUMBER({cu}),ISNUMBER($C$4),ABS({cu}-$C$4)<=$C$5,ABS(MOD({mo}-$C$6+6,12)-6)<=$C$7,OR($C$8="Any",{wc}=$C$8)),1,0)').number_format = '0'
    hs.cell(row, 4, f'=IF(C{row}=1,COUNTIF($C${TOP}:C{row},1),"")').number_format = '0'
    for k, h, nf in HCOLS:
        j = col[k]
        if k == 'spike': v = int(r.px_max >= 300) if pd.notna(r.px_max) else None
        elif k == 'calm': v = ('Calm' if r.w_cf < 100*CF_CALM else 'Windy') if pd.notna(r.w_cf) else None
        elif k == 'pk_minus_da': v = (r.px_pk - r.da_pk) if pd.notna(r.da_pk) and pd.notna(r.px_pk) else None
        else:
            v = r[k]; v = None if pd.isna(v) else round(float(v), 2)
        c = hs.cell(row, j, v); c.font = BLUE; c.number_format = nf
    fl = CL(col['px_flat']); cm = CL(col['cush_min'])
    hs.cell(row, cA, f'=IFERROR(AVERAGEIFS({hr("px_flat")},{hr("cush_min")},">="&{cu}-$C$5,{hr("cush_min")},"<="&{cu}+$C$5,{hr("Month")},{mo},$A${TOP}:$A${BOT},"<>"&A{row}),"")').number_format = '#,##0.0'
    hs.cell(row, cB, f'=IF(AND(ISNUMBER({fl}{row}),ISNUMBER({CL(cA)}{row})),{fl}{row}-{CL(cA)}{row},"")').number_format = '#,##0.0'
hs.conditional_formatting.add(f'C{TOP}:C{BOT}', CellIsRule(operator='equal', formula=['1'], fill=PatternFill('solid', fgColor='FFF2CC')))
hs.conditional_formatting.add(f'{CL(cB)}{TOP}:{CL(cB)}{BOT}', CellIsRule(operator='greaterThan', formula=['40'], fill=PatternFill('solid', fgColor='F8CBAD')))
hs.conditional_formatting.add(f'{CL(cB)}{TOP}:{CL(cB)}{BOT}', CellIsRule(operator='lessThan', formula=['-40'], fill=PatternFill('solid', fgColor='C6E0B4')))
hs.freeze_panes = f'E{TOP}'; hs.row_dimensions[HRH].height = 60; hs.auto_filter.ref = f'A{HRH}:{CL(cB)}{BOT}'
for j in range(1, cB+1): hs.column_dimensions[CL(j)].width = 13
hs.column_dimensions['B'].width = 7; hs.column_dimensions['C'].width = 7; hs.column_dimensions['D'].width = 8
hs.column_dimensions['A'].width = 30; hs.column_dimensions['F'].width = 22
sc2 = ScatterChart(); sc2.title = 'Tightest cushion vs pool price HE8-23, all history'; sc2.style = 13; sc2.height, sc2.width = 9, 16
sc2.x_axis.title = 'Cushion, tightest HE8-23 (MW)'; sc2.y_axis.title = '$/MWh'
xs = Reference(hs, min_col=col['cush_min'], min_row=TOP, max_row=BOT); ys = Reference(hs, min_col=col['px_pk'], min_row=TOP, max_row=BOT)
se = Series(ys, xs, title='days'); se.marker.symbol = 'circle'; se.marker.size = 3; se.graphicalProperties.line.noFill = True; sc2.series.append(se)
hs.add_chart(sc2, 'S3')
print('Historical rows', N, 'from', HD.index.min().date(), 'to', HD.index.max().date())

# ----------------------------------------------------------------- README ----
rd = wb.create_sheet('README', 0)
txt = ['Pre-model checklist (Alberta) -- how to read this workbook', '',
 'Summary: one row per item. Tomorrow vs the average of the last 14 and last 30 delivery days, the 30-day spread (SD, min, max), and the next-14-day average vs the last 30.',
 '   Flag HIGH / LOW = tomorrow is at least one standard deviation above / below the last 30 days.',
 'Next_14_Days: each of the next 14 days, raw value then the difference vs the last-14 and last-30 means. Red / green = the difference is bigger than one 30-day SD.',
 'Last_30_Days: each of the last 30 days (newest first), raw value then the difference vs the last-14 and last-30 means.',
 "Scenario: change load, wind, solar, baseload gas / simple cycle availability and net imports (optionally for a range of hours) and see tomorrow's cushion, thermal remaining, price forecast and score change by hour.",
 'Charts: the key series over the last 30 days and the next 14 (forecasts), plus scatter plots of what drove the peak settle against the day-ahead forward over the last 30 days.',
 'Data: the raw daily numbers (blue = inputs written by the script; yellow row = tomorrow). Every average and difference elsewhere is a formula on this sheet. Green values = links to Data.',
 'Historical: every settled day since the feed starts (newest first): pool price, fundamentals and cushion as the model saw them. Yellow cells at the top pull the days with a similar cushion (same month, calm / windy); Match = 1 marks them, and each row also shows how it settled against its own analogs (Anomaly).', '',
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
