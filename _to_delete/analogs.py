"""
analogs.py — the history tables: what price did when thermal remaining was X,
and what each month did when it ran hotter / colder / windier than normal.

    python analogs.py        -> verify/analogs.xlsx  (and prints the summaries)

Sheets
  ThermalRemaining   cogen+CC+GFS available minus what they must serve
                     (load - wind - solar - imports - hydro - storage - bio),
                     hourly and by day's tightest hour, against price.
  Months             every month on file: temperature, wind, thermal remaining,
                     gas, each against the same calendar month's normal; price,
                     peak, spike days, price as a ratio to normal, and the
                     month-ahead forward where one was on file.
  Conditional        pooled: colder / hotter / less wind / more wind / less or
                     more thermal remaining -> price vs normal, spike days.
  Daily              the day-level rows behind it, for pivoting.

Only two years are on file, so a month's "normal" is the other year. Read
the direction, not the second decimal. The tables extend themselves as
composition.csv and price_history.csv grow; to reach further back, the
pool-price and composition pulls in update.py take a longer history_days.
"""
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent; CACHE = ROOT/'cache'


def say(s=''): print(s, flush=True)


def load():
    C = pd.read_csv(CACHE/'composition.csv', parse_dates=['datetime_begin'])
    C = C[C.lead_bucket == -1].set_index('datetime_begin').sort_index()
    C = C[~C.index.duplicated(keep='last')]
    ph = pd.read_csv(ROOT/'model'/'price_history.csv', index_col=0, parse_dates=True)
    C['price'] = ph.price.reindex(C.index)
    C['need'] = C.ail - C.wind - C.solar - C.net_imports_actual_scheduled - C.hydro - C.energy_storage - C.biomass_and_other
    C['thermal_rem'] = (C.cogen + C.cc + C.gfs) - C.need
    C['thermal_av'] = C.sc + C.cogen + C.cc + C.gfs
    C['d'] = C.index.normalize(); C['he'] = C.index.hour + 1
    w = pd.read_csv(CACHE/'wx_hourly.csv', parse_dates=[0]); w = w.rename(columns={w.columns[0]: 'ts'})
    w['d'] = w.ts.dt.normalize(); W = w.groupby('d').agg(temp=('temp', 'mean'), tmin=('temp', 'min'), tmax=('temp', 'max'), wspd=('wind', 'mean'))
    g = None
    fx = CACHE/'fwd_extra.csv'
    if fx.exists():
        f = pd.read_csv(fx, parse_dates=['EffectiveDate', 'Strip']); f['lead'] = (f.Strip - f.EffectiveDate).dt.days
        g = f[(f.ExchangeCode == 'XBG') & (f.lead >= 0)].sort_values('lead').groupby('Strip').Price.first()
    return C, W, g


def daily(C, W, gas):
    x = C.dropna(subset=['price'])
    D = x.groupby('d').agg(flat=('price', 'mean'), pmax=('price', 'max'),
                           rem_min=('thermal_rem', 'min'), rem_mean=('thermal_rem', 'mean'),
                           thermal_av=('thermal_av', 'mean'), wind=('wind', 'mean'), ail=('ail', 'mean'),
                           imports=('net_imports_actual_scheduled', 'mean'))
    D['peak'] = x[x.he.between(8, 23)].groupby('d').price.mean()
    D['spike'] = (D.pmax >= 300).astype(int)
    D = D.join(W)
    if gas is not None: D['gas'] = gas.reindex(D.index)
    return D


def thermal_tables(C):
    x = C.dropna(subset=['price', 'thermal_rem']); rows = []
    bins = [(-9999, -500), (-500, 0), (0, 500), (500, 1000), (1000, 1500), (1500, 2000), (2000, 3000), (3000, 99999)]
    for lab, sub in (('all hours', x), ('evening HE17-21', x[x.he.between(17, 21)])):
        for lo, hi in bins:
            g = sub[sub.thermal_rem.between(lo, hi)]
            if not len(g): continue
            rows.append(dict(slice=lab, thermal_remaining=f'{lo} to {hi}', hours=len(g), avg=g.price.mean(), median=g.price.median(),
                             p_over_100=(g.price > 100).mean(), p_over_300=(g.price > 300).mean(), p_over_700=(g.price > 700).mean()))
    return pd.DataFrame(rows)


def month_table(D):
    D = D.copy(); D['ym'] = D.index.to_period('M')
    M = D.groupby('ym').agg(days=('flat', 'size'), temp=('temp', 'mean'), wind=('wind', 'mean'), thermal_rem=('rem_min', 'mean'),
                            thermal_av=('thermal_av', 'mean'), gas=('gas', 'mean') if 'gas' in D else ('flat', 'size'),
                            flat=('flat', 'mean'), peak=('peak', 'mean'), spike_days=('spike', 'sum'), worst_hour=('pmax', 'max'))
    M = M[M.days >= 25]; M['month'] = [p.month for p in M.index]
    nm = M.groupby('month')[['temp', 'wind', 'thermal_rem', 'flat']].transform('mean')
    M['temp_vs_normal'] = M.temp - nm.temp; M['wind_vs_normal'] = M.wind - nm.wind
    M['thermal_rem_vs_normal'] = M.thermal_rem - nm.thermal_rem; M['price_vs_normal'] = M.flat/nm.flat
    fp = CACHE/'forward_px.csv'
    if fp.exists():
        f = pd.read_csv(fp, parse_dates=['EffectiveDate', 'strip']); f['mo'] = f.strip.dt.to_period('M')
        ld = (f.strip - f.EffectiveDate).dt.days
        fw = f[ld.between(20, 40)].groupby('mo').price.mean()
        M['fwd_month_ahead'] = fw.reindex(M.index).values; M['settle_minus_fwd'] = M.flat - M.fwd_month_ahead
    M.index = M.index.astype(str)
    return M.drop(columns='month')


def conditional(M):
    rows = []
    for lab, m in (('colder than normal (>1.5C)', M.temp_vs_normal < -1.5), ('hotter than normal (>1.5C)', M.temp_vs_normal > 1.5),
                   ('wind below normal (>150 MW)', M.wind_vs_normal < -150), ('wind above normal (>150 MW)', M.wind_vs_normal > 150),
                   ('thermal remaining below normal (>200)', M.thermal_rem_vs_normal < -200),
                   ('thermal remaining above normal (>200)', M.thermal_rem_vs_normal > 200)):
        g = M[m]
        rows.append(dict(condition=lab, months=len(g), price_vs_normal=g.price_vs_normal.mean(), spike_days=g.spike_days.mean(),
                         avg_flat=g.flat.mean(), which=', '.join(g.index)))
    return pd.DataFrame(rows)


def main():
    C, W, gas = load(); D = daily(C, W, gas); T = thermal_tables(C); M = month_table(D); K = conditional(M)
    out = ROOT/'verify'/'analogs.xlsx'; out.parent.mkdir(exist_ok=True)
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        T.round(3).to_excel(xw, sheet_name='ThermalRemaining', index=False)
        M.round(2).to_excel(xw, sheet_name='Months')
        K.round(2).to_excel(xw, sheet_name='Conditional', index=False)
        D.round(2).to_excel(xw, sheet_name='Daily')
        for ws in xw.book.worksheets:
            from openpyxl.styles import Font
            for row in ws.iter_rows():
                for c in row: c.font = Font(name='Arial', size=10, bold=(c.row == 1))
            for col in ws.columns: ws.column_dimensions[col[0].column_letter].width = 16
    say(f'wrote {out}')
    say('\nthermal remaining, evening hours:'); say(T[T.slice == 'evening HE17-21'].to_string(index=False))
    say('\nconditional:'); say(K.drop(columns='which').to_string(index=False))


if __name__ == '__main__':
    main()
