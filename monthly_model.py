"""monthly_model.py — Monthly_Model.xlsx: the data behind a monthly view, laid out for a decision."""
import sys
from pathlib import Path
import numpy as np, pandas as pd, openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter as CL
from openpyxl.chart import LineChart, BarChart, Reference
U = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/mnt/user-data/uploads/aeso-cushion model')
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('/mnt/user-data/outputs/Monthly_Model.xlsx')
ASOF = pd.Timestamp('2026-09-29')
F = lambda **kw: Font(**{'name': 'Arial', 'size': 10, **kw}); BLUE, BLACK = F(color='0000FF'), F()
HDR = PatternFill('solid', fgColor='DDEBF7'); YEL = PatternFill('solid', fgColor='FFFF00')

# ---------------------------------------------------------------- daily ----
C = pd.read_csv(U/'cache/composition.csv', parse_dates=['datetime_begin']); C = C[C.lead_bucket == -1].set_index('datetime_begin').sort_index()
C = C[~C.index.duplicated(keep='last')]
ph = pd.read_csv(U/'model/price_history.csv', index_col=0, parse_dates=True); C['price'] = ph.price.reindex(C.index)
C['thermal'] = C.cogen + C.cc + C.gfs; C['cush'] = C.thermal + C.sc + C.biomass_and_other + C.wind + C.solar - (C.ail - C.net_imports_actual_scheduled)
C['rem'] = C.thermal - (C.ail - C.wind - C.solar - C.net_imports_actual_scheduled - C.hydro - C.energy_storage - C.biomass_and_other)
C['d'] = C.index.normalize(); C['he'] = C.index.hour + 1
x = C.dropna(subset=['price'])
D = x.groupby('d').agg(flat=('price', 'mean'), pmax=('price', 'max'), cush_min=('cush', 'min'), cush_mean=('cush', 'mean'), rem_min=('rem', 'min'),
                       wind=('wind', 'mean'), ail=('ail', 'mean'), thermal=('thermal', 'mean'), imp=('net_imports_actual_scheduled', 'mean'), hydro=('hydro', 'mean'))
D['peak'] = x[x.he.between(8, 23)].groupby('d').price.mean(); D['n100'] = x.groupby('d').price.apply(lambda s: (s > 100).sum())
D['spike'] = (D.pmax >= 300).astype(int)
wx = pd.read_csv(U/'cache/wx_hourly.csv', parse_dates=[0]); wx = wx.rename(columns={wx.columns[0]: 'ts'}); wx['d'] = wx.ts.dt.normalize()
D = D.join(wx.groupby('d').agg(temp=('temp', 'mean'), tmin=('temp', 'min')))
fe = pd.read_csv(U/'cache/fwd_extra.csv', parse_dates=['EffectiveDate', 'Strip']) if (U/'cache/fwd_extra.csv').exists() else None
if fe is not None:
    fe['lead'] = (fe.Strip - fe.EffectiveDate).dt.days; nb = fe[fe.lead >= 0].sort_values('lead').groupby(['Strip', 'ExchangeCode']).Price.first().unstack()
    D['gas'] = nb['XBG'].reindex(D.index); D['midc'] = nb['MPD'].reindex(D.index)
# ---------------------------------------------------------------- months ---
D['ym'] = D.index.to_period('M')
M = D.groupby('ym').agg(days=('flat', 'size'), flat=('flat', 'mean'), peak=('peak', 'mean'), worst=('pmax', 'max'), spike_days=('spike', 'sum'), hrs100=('n100', 'sum'),
                        cush_min=('cush_min', 'mean'), cush_mean=('cush_mean', 'mean'), rem_min=('rem_min', 'mean'), wind=('wind', 'mean'), load=('ail', 'mean'), thermal=('thermal', 'mean'),
                        imports=('imp', 'mean'), hydro=('hydro', 'mean'), temp=('temp', 'mean'), tmin=('tmin', 'mean'),
                        **({'gas': ('gas', 'mean'), 'midc': ('midc', 'mean')} if fe is not None else {}))
M = M[M.days >= 25]
top3 = D.groupby('ym').flat.apply(lambda s: s.nlargest(3).sum()/s.sum()); M['top3_share'] = top3.reindex(M.index)
mf = pd.read_csv(U/'cache/forward_px.csv', parse_dates=['EffectiveDate', 'strip']); mf['mo'] = mf.strip.dt.to_period('M'); mf['lead'] = (mf.strip - mf.EffectiveDate).dt.days
def fwd_at(mo, lo, hi):
    s = mf[(mf.mo == mo) & mf.lead.between(lo, hi)]; return float(s.price.mean()) if len(s) else np.nan
M['fwd_60d'] = [fwd_at(m, 50, 70) for m in M.index]; M['fwd_30d'] = [fwd_at(m, 20, 40) for m in M.index]; M['fwd_5d'] = [fwd_at(m, 1, 8) for m in M.index]
M['settle_minus_fwd30'] = M.flat - M.fwd_30d
M['month'] = [p.month for p in M.index]
GROWTH = 300.0   # MW per year of Alberta load growth, measured on this history
M['yr'] = [p.year for p in M.index]; ymax = M.yr.max()
M['load_adj'] = M.load + GROWTH*(ymax - M.yr); M['cush_adj'] = M.cush_mean - GROWTH*(ymax - M.yr)   # what that month's load / cushion would read on today's system
norm = M.groupby('month')[['flat', 'peak', 'cush_min', 'wind', 'load', 'thermal', 'temp']].transform('mean')
norm['load'] = M.groupby('month').load_adj.transform('mean'); norm['cush_min'] = M.groupby('month').cush_adj.transform('mean')
for c in ['flat', 'wind', 'thermal', 'temp']: M[f'{c}_vs_norm'] = M[c] - norm[c]
M['load_vs_norm'] = M.load_adj - norm.load; M['cush_min_vs_norm'] = M.cush_adj - norm.cush_min
M['band'] = np.where(M.flat_vs_norm > 0.1*norm.flat, 'above', np.where(M.flat_vs_norm < -0.1*norm.flat, 'below', 'average'))
# ------------------------------------------------------------- forward now ---
last = mf[mf.EffectiveDate == mf.EffectiveDate.max()].sort_values('strip'); last = last[last.strip <= ASOF + pd.DateOffset(months=15)]
hist90 = mf[(mf.EffectiveDate >= ASOF - pd.Timedelta(days=90))]
FW = hist90.pivot_table(index='EffectiveDate', columns='mo', values='price'); FW = FW[[c for c in FW.columns if c in set(last.mo)]]
ol = pd.read_csv(U/'cache/outlook.csv', parse_dates=['date'])
# ------------------------------------------------------------------ write ---
wb = openpyxl.Workbook(); rd = wb.active; rd.title = 'README'
txt = ['Monthly model — the data behind a monthly view, laid out for a decision', '',
 'What this is: not a fair value. At 30-60 days the level of an Alberta month is not forecastable from fundamentals — the walk-forward on the monthly tool itself says so (outlook-only correlation 0.40, and the market curve 0.06). What can be known is where the month sits against its own history, what the market is paying for it, and which drivers are running away from normal. That is a judgement laid on data, so the data is laid out and the judgement is left to you on the Decision tab.',
 '', 'Tabs',
 'Months: every settled month on file. Price (flat, peak, worst hour, spike days, hours over $100, share of the month in its 3 worst days), fundamentals (tightest cushion, thermal remaining, wind, load, thermal availability, imports, hydro, temperature, gas, Mid-C), each also as a deviation from the same calendar month\'s normal, the forward that was paid for the month 60 / 30 / 5 days out, and settle minus the 30-day forward. Band = above / average / below (10% of normal).',
 'Normals: the calendar-month normal for each item (mean across the years on file — two years, so read the direction).',
 'Premium: what the market has paid over what settled, by lead. This is the standing edge in the data and the reason a fundamental fair value always reads below the curve.',
 'Forward: the monthly curve as of the latest settle and its path over the last 90 days, one column per delivery month.',
 'Outlook: the next 44 days from outlook.csv — cushion percentiles, load, wind, temperature anomaly — against the seasonal normal.',
 'Decision: one row per driver per delivery month. Normal, what the market implies, what you see, direction, weight, and your call. Blue cells are yours.',
 '', 'What the monthly tool is missing (from reading it)',
 '1. Its fair value is a seasonal normal, not a view: beyond the 9-day wind and 14-day load horizon every scenario collapses to climatology, so every month lands at ~2,100-2,500 MW of cushion and the price is the trailing-year curve read at that point. It will always sit $3-4 under the market and always say sell.',
 '2. No risk premium term. Settle beat the month-ahead forward in 3 of 15 months (median -$13.7). Fair below market is the expected state, not a signal. The signal is when the gap is unusual against that history.',
 '3. Gas enters only as a floor. Measured: +16% on price per $/GJ, and it predicts the model\'s error. It belongs in the curve, as does Mid-C (+18% per log unit).',
 '4. The trailing-365-day curve is stale on level: load grows ~300 MW a year and new wind / solar / gas capacity moves the whole cushion-price relationship. Restate old cushions to today\'s fleet before fitting.',
 '5. Daily curve, monthly answer: a third of a month\'s dollars sit in its three worst days. Averaging 400 daily draws from a daily-mean curve under-produces that tail unless the residuals are drawn by hour block.',
 '6. Contract shape: the tool prices flat. Peak and off-peak months carry different premiums (peak settled $9.60 under its forward, off-peak $0.75).',
 '7. Hydro / imports: BC freshet (May-June) and the winter export pattern move imports by ±400 MW and are seasonal, not in the outlook.',
 '8. Text says a $5.00 spread in one place and $2.50 in another; percentile of model outcomes above 50 for a mean is skew, not a signal.',
 f'Built {ASOF:%Y-%m-%d}. Two years of history; the Normals are two-year means.']
for i, t in enumerate(txt, 1): rd.cell(i, 1, t).font = F(bold=(i == 1 or t in ('Tabs', 'What the monthly tool is missing (from reading it)')), size=12 if i == 1 else 10); rd.cell(i, 1).alignment = Alignment(wrap_text=True, vertical='top')
rd.column_dimensions['A'].width = 150
def sheet(name, df, index_label='Month', fmt='#,##0.0', blue=False):
    s = wb.create_sheet(name); cols = [index_label] + list(df.columns)
    for j, c in enumerate(cols, 1): h = s.cell(1, j, c); h.font = F(bold=True); h.fill = HDR; h.alignment = Alignment(wrap_text=True, vertical='top')
    for i, (idx, r) in enumerate(df.iterrows(), 2):
        s.cell(i, 1, str(idx)).font = BLACK
        for j, c in enumerate(df.columns, 2):
            v = r[c]; v = None if (isinstance(v, float) and np.isnan(v)) else (float(v) if isinstance(v, (np.floating, np.integer)) else v)
            cell = s.cell(i, j, v); cell.font = BLUE if blue else BLACK
            if isinstance(v, float): cell.number_format = '0%' if 'share' in c else fmt
    s.freeze_panes = 'B2'; s.row_dimensions[1].height = 45
    for j in range(1, len(cols)+1): s.column_dimensions[CL(j)].width = 13
    return s
sheet('Months', M.drop(columns='month').round(2))
N = M.groupby('month')[['flat', 'peak', 'spike_days', 'hrs100', 'cush_min', 'cush_mean', 'rem_min', 'wind', 'load', 'thermal', 'imports', 'hydro', 'temp', 'tmin'] + (['gas', 'midc'] if fe is not None else [])].mean().round(1)
N['load'] = M.groupby('month').load_adj.mean().round(0); N['cush_mean'] = M.groupby('month').cush_adj.mean().round(0)   # restated to today's system (+300 MW/yr)
N['years'] = M.groupby('month').size()
N.index = [pd.Timestamp(2000, m, 1).strftime('%b') for m in N.index]; sheet('Normals', N, 'Calendar month'); wb['Normals']['A15'] = 'Load and tightest cushion are restated to the latest year at +300 MW/yr of load growth; a raw average would read low on load and high on cushion.'
# premium by lead
rows = []
for lo, hi, lab in ((1, 8, '1-8 days'), (9, 20, '9-20'), (20, 40, '20-40'), (40, 70, '40-70'), (70, 120, '70-120')):
    s = mf[mf.lead.between(lo, hi)].groupby('mo').price.mean(); j = pd.concat([M.flat, s.rename('fwd')], axis=1).dropna(); d = j.flat - j.fwd
    rows.append(dict(lead=lab, months=len(j), settle_above_fwd=(d > 0).mean(), mean=d.mean(), median=d.median(), p10=d.quantile(.1), p90=d.quantile(.9), worst=d.min(), best=d.max()))
P = pd.DataFrame(rows).set_index('lead'); sheet('Premium', P.round(2), 'Forward lead')
fw = wb.create_sheet('Forward'); fw['A1'] = f'Monthly flat forward, as of {last.EffectiveDate.max():%Y-%m-%d}'; fw['A1'].font = F(bold=True)
for j, h in enumerate(['Delivery month', 'Forward now', '30 days ago', '60 days ago', '90 days ago', 'Normal settle (same calendar month)', 'Forward - normal', 'Spike days normal'], 1): c = fw.cell(2, j, h); c.font = F(bold=True); c.fill = HDR; c.alignment = Alignment(wrap_text=True)
for i, r in enumerate(last.itertuples(), 3):
    fw.cell(i, 1, f'{r.strip:%Y-%m}'); fw.cell(i, 2, float(r.price)).number_format = '0.00'
    for k, dd in enumerate((30, 60, 90), 3):
        s = mf[(mf.mo == r.mo) & (mf.EffectiveDate <= ASOF - pd.Timedelta(days=dd))]
        fw.cell(i, k, float(s.sort_values('EffectiveDate').price.iloc[-1]) if len(s) else None).number_format = '0.00'
    mn = pd.Timestamp(2000, r.strip.month, 1).strftime('%b')
    if mn in N.index:
        fw.cell(i, 6, float(N.loc[mn, 'flat'])).number_format = '0.00'; fw.cell(i, 7, f'=B{i}-F{i}').number_format = '0.00'; fw.cell(i, 8, float(N.loc[mn, 'spike_days'])).number_format = '0.0'
fw.row_dimensions[2].height = 45
for j in range(1, 9): fw.column_dimensions[CL(j)].width = 15
r0 = 3 + len(last) + 2; fw.cell(r0, 1, 'Curve history, last 90 days (rows = settle date, columns = delivery month)').font = F(bold=True)
for j, c in enumerate(FW.columns, 2): fw.cell(r0+1, j, str(c)).font = F(bold=True)
for i, (dt, r) in enumerate(FW.iterrows(), r0+2):
    fw.cell(i, 1, dt.to_pydatetime()).number_format = 'yyyy-mm-dd'
    for j, c in enumerate(FW.columns, 2):
        if not np.isnan(r[c]): fw.cell(i, j, float(r[c])).number_format = '0.00'
ch = LineChart(); ch.title = 'Monthly forward: how each delivery month has moved (last 90 days)'; ch.height, ch.width = 9, 22
for j in range(2, min(2+len(FW.columns), 8)): ch.add_data(Reference(fw, min_col=j, min_row=r0+1, max_row=r0+1+len(FW)), titles_from_data=True)
ch.set_categories(Reference(fw, min_col=1, min_row=r0+2, max_row=r0+1+len(FW))); fw.add_chart(ch, 'J2')
ch = BarChart(); ch.title = 'Settle vs the same calendar month normal, flat $/MWh'; ch.height, ch.width = 9, 22
ms = wb['Months']; ch.add_data(Reference(ms, min_col=2, min_row=1, max_row=1+len(M)), titles_from_data=True); ch.add_data(Reference(ms, min_col=list(M.drop(columns='month').columns).index('fwd_30d')+2, min_row=1, max_row=1+len(M)), titles_from_data=True)
ch.set_categories(Reference(ms, min_col=1, min_row=2, max_row=1+len(M))); ms.add_chart(ch, 'B' + str(len(M)+4))
ol2 = ol.set_index('date'); sheet('Outlook', ol2, 'Date', '#,##0.0')
# inputs for the decision tab: gencap thermal AC by day (15 d), the 90-day report, the AB-NIT monthly forward
import json, io
try:
    G = json.load(open(U/'refresh/gencap.json', encoding='utf-8-sig'))['return']; rows = {}
    for b in G:
        if b['sub_fuel_type'] in ('COGENERATION', 'COMBINED_CYCLE', 'GAS_FIRED_STEAM'):
            for h in b['Hours']: t = pd.Timestamp(h['begin_datetime_mpt']); rows[t] = rows.get(t, 0) + (h['outage_grouping'].get('AC') or 0)
    gcm = pd.Series(rows).sort_index(); gcm = gcm.groupby(gcm.index.normalize()).mean(); gcm = gcm[gcm.index >= ASOF]
    thermal_mc = sum(b['Hours'][0]['outage_grouping'].get('MC', 0) for b in G if b['sub_fuel_type'] in ('COGENERATION', 'COMBINED_CYCLE', 'GAS_FIRED_STEAM'))
except Exception: gcm = pd.Series(dtype=float); thermal_mc = np.nan
try:
    lines = open(U/'refresh/outage_90d.csv', encoding='utf-8', errors='ignore').read().splitlines(); k0 = [k for k, l in enumerate(lines) if l.strip().startswith('"   Date')][0]
    orep = pd.read_csv(io.StringIO('\n'.join(lines[k0:])), skipinitialspace=True); orep.columns = [c.strip() for c in orep.columns]
    orep = orep[orep.Date.astype(str).str.match(r'\d\d-\w\w\w-\d{4}')].copy(); orep['Date'] = pd.to_datetime(orep.Date, format='%d-%b-%Y'); orep = orep.set_index('Date')
    for c in ['SC', 'Cogen', 'CC', 'GFS']: orep[c] = pd.to_numeric(orep[c], errors='coerce')
    orep['thermal_out'] = orep[['SC', 'Cogen', 'CC', 'GFS']].sum(axis=1)
except Exception: orep = pd.DataFrame(columns=['thermal_out'])
gp = U.parent/'Documents'/'aeso-monthly'/'cache'/'gas_fwd.csv'
if not gp.exists(): gp = U.parent/'aeso-monthly'/'cache'/'gas_fwd.csv'
gasf = pd.read_csv(gp, parse_dates=['EffectiveDate', 'Strip']) if gp.exists() else pd.DataFrame(columns=['EffectiveDate', 'Strip', 'ExchangeCode', 'Price'])
gasf = gasf[gasf.ExchangeCode == 'XAV'].copy(); gasf['mo'] = gasf.Strip.dt.to_period('M')
# decision tab
dc = wb.create_sheet('Decision'); dc['A1'] = 'Decision: one row per driver per delivery month. Blue = yours.'; dc['A1'].font = F(bold=True, size=12)
heads = ['Delivery month', 'Driver', 'Normal for this month', 'What the outlook / market shows now', 'Above / average / below', 'Pushes price', 'Weight (1-5)', 'Your view', 'Call (buy / sell / no trade)']
for j, h in enumerate(heads, 1): c = dc.cell(3, j, h); c.font = F(bold=True); c.fill = HDR; c.alignment = Alignment(wrap_text=True)
drivers = [('Wind vs normal', 'wind', 'down when high'), ('Load / temperature vs normal', 'load', 'up when high'), ('Thermal availability vs normal (now = gencap AC, else nameplate minus the 90-day report)', 'thermal', 'up when low'),
           ('Daily-mean cushion vs normal (outlook p50 vs restated normal)', 'cush_mean', 'up when low'), ('Gas: AB-NIT monthly forward vs normal spot', 'gas', 'up when high'), ('Imports / BC hydro vs normal (now = last 30 days actual; no forward exists)', 'imports', 'up when low'),
           ('Spike days: normal for the month vs the last 3 months', 'spike_days', 'tail'), ('Market forward vs normal settle', None, 'premium'), ('Premium the market usually pays at this lead', None, 'see Premium tab')]
r = 4
for mrow in last.head(4).itertuples():
    mn = pd.Timestamp(2000, mrow.strip.month, 1).strftime('%b'); mo = mrow.mo
    olm = ol[ol.date.dt.to_period('M') == mo]
    for lab, key, push in drivers:
        dc.cell(r, 1, f'{mrow.strip:%Y-%m}'); dc.cell(r, 2, lab); dc.cell(r, 6, push)
        if key and mn in N.index and key in N.columns: dc.cell(r, 3, float(N.loc[mn, key])).number_format = '#,##0.0'
        now = None
        if key == 'thermal':
            gm = gcm[gcm.index.to_period('M') == mo] if len(gcm) else gcm
            if len(gm) >= 5: now = float(gm.mean()); dc.cell(r, 4).comment = None
            elif len(orm := orep[orep.index.to_period('M') == mo]) and len(orep[orep.index <= ASOF]):
                now = float(D.thermal[D.index >= ASOF - pd.Timedelta(days=30)].mean() - (orm.thermal_out.mean() - orep[orep.index <= ASOF].thermal_out.iloc[-7:].mean()))
        if key == 'gas':
            gx = gasf[(gasf.mo == mo) & (gasf.EffectiveDate <= ASOF)].sort_values('EffectiveDate')
            if len(gx): now = float(gx.Price.iloc[-1])
        if key == 'imports': now = float(D.imp[D.index >= ASOF - pd.Timedelta(days=30)].mean())
        if key == 'spike_days': now = float(M.spike_days[M.index >= (ASOF - pd.DateOffset(months=3)).to_period('M')].mean())
        if key == 'wind' and len(olm): now = float(olm.wind.mean())
        if key == 'load' and len(olm): now = float(olm.ail.mean())
        if key == 'cush_mean' and len(olm): now = float(olm.p50.mean())
        if key is None and lab.startswith('Market'): now = float(mrow.price); dc.cell(r, 3, float(N.loc[mn, 'flat']) if mn in N.index else None)
        if key is None and lab.startswith('Premium'):
            lead = (mrow.strip - ASOF).days; lab_b = '1-8 days' if lead <= 8 else '9-20' if lead <= 20 else '20-40' if lead <= 40 else '40-70' if lead <= 70 else '70-120'
            typ = -float(P.loc[lab_b, 'median']); dc.cell(r, 3, typ).number_format = '0.0'
            now = float(mrow.price) - (float(N.loc[mn, 'flat']) if mn in N.index else np.nan); dc.cell(r, 2, f'Premium: market minus normal settle now, vs what the market usually pays over the settle at {lab_b} out')
        if now is not None: dc.cell(r, 4, now).number_format = '#,##0.0'
        if key in ('imports', 'spike_days') and now is not None: dc.cell(r, 4).number_format = '#,##0.0'
        if now is not None and dc.cell(r, 3).value:
            dc.cell(r, 5, f'=IF(ABS(C{r})<1,IF(D{r}>C{r}+50,"above",IF(D{r}<C{r}-50,"below","average")),IF(D{r}>C{r}*1.1,"above",IF(D{r}<C{r}*0.9,"below","average")))')
        for j in (7, 8, 9): dc.cell(r, j).fill = YEL; dc.cell(r, j).font = BLUE
        r += 1
    r += 1
for j, w in enumerate([13, 40, 16, 22, 16, 16, 11, 40, 20], 1): dc.column_dimensions[CL(j)].width = w
dc.freeze_panes = 'C4'
wb.save(OUT); print('saved', OUT, len(M), 'months')
