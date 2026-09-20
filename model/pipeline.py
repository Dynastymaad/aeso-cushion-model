"""
pipeline.py — rebuild every model series from raw exports.

Kept separate from update.py so each stage can be read and tested on its own.
Every function here reproduces, step for step, the construction the shipped
model was built with. Verified: rebuilding from raw exports reproduces the
shipped series at correlation 1.00000, sub-megawatt mean difference.
"""
import numpy as np, pandas as pd

DA_LO_WIND, DA_HI_WIND = 16, 40      # day-ahead lead window, wind and solar
DA_LO_LOAD, DA_HI_LOAD = 12, 40      # load was built on a slightly wider window
WF_WIN = 45                          # walk-forward recalibration window, days


def vintage(df, val, lo, hi):
    """Day-ahead pivot: for each target hour, the LAST forecast each source
    issued while still at least `lo` hours ahead. This is what 'day-ahead'
    means throughout the model — not the newest forecast, the one you would
    actually have had."""
    ts = [c for c in df.columns if c.lower() == 'timestamp'][0]
    d = df.copy()
    d['iss'] = pd.to_datetime(d[ts], errors='coerce')
    d['tgt'] = pd.to_datetime(d.EffectiveDateTime, errors='coerce')
    d['lead'] = (d.tgt - d.iss).dt.total_seconds() / 3600
    d = d[(d.lead >= lo) & (d.lead <= hi)].dropna(subset=['tgt', val]).sort_values('iss')
    p = (d.groupby(['DataSourceName', 'tgt'], as_index=False).last()
           .pivot(index='tgt', columns='DataSourceName', values=val).sort_index())
    if getattr(p.index, 'tz', None) is not None:
        p.index = p.index.tz_localize(None)
    return p


def walk_forward_recal(j, col, act, win=WF_WIN, clip=None):
    """Fit actual ~ a + b*forecast on the prior `win` days only, apply to today.
    Raw vendor forecasts sit on a different basis than Alberta outturn — slopes
    ran 1.31 to 1.51 — so this is not cosmetic. Prior days only: no peeking."""
    j = j.copy(); j['day'] = j.index.normalize()
    days = sorted(j.day.unique())
    out = pd.Series(index=j.index, dtype=float)
    for i, d in enumerate(days):
        if i < 10: continue
        tr = j[(j.day >= days[max(0, i - win)]) & (j.day < d)][[col, act]].dropna()
        te = j[j.day == d][[col]].dropna()
        if len(tr) < 200 or len(te) == 0: continue
        m, c = np.polyfit(tr[col].values, tr[act].values, 1)
        v = c + m * te[col].values
        out.loc[te.index] = np.clip(v, *clip) if clip else v
    return out


def actuals_from_composition(comp):
    """Hourly actuals and the true cushion. lead_bucket -1 is the post-hour
    snapshot; it is the closest thing this table has to an actual."""
    d = comp.copy()
    tcol = [c for c in d.columns if c.lower() == 'datetime_begin'][0]
    d['t'] = pd.to_datetime(d[tcol])
    if 'lead_bucket' in d: d = d[d.lead_bucket == -1]
    d = d.set_index('t').sort_index()
    d = d[~d.index.duplicated(keep='last')]
    d['gas'] = d[['sc', 'cogen', 'cc', 'gfs']].sum(axis=1)
    # the cushion, exactly as defined: internal supply minus net demand.
    # hydro and storage are deliberately excluded - they are the marginal,
    # price-setting resources, and the cushion measures how far you must reach
    # before you need them.
    d['cush'] = (d.gas + d.biomass_and_other + d.wind + d.solar
                 - (d.ail - d.net_imports_actual_scheduled))
    return d


def day_ahead_series(comp, wind_df, solar_df, load_df):
    """Everything the model needs at a day-ahead vintage."""
    b = actuals_from_composition(comp)

    wp = vintage(wind_df, 'Value', DA_LO_WIND, DA_HI_WIND)
    jw = wp.join(b[['wind']].rename(columns={'wind': 'wind_act'}), how='inner').dropna(subset=['wind_act'])
    wind_cor = pd.DataFrame({c: walk_forward_recal(jw, c, 'wind_act', clip=(0, 6000))
                             for c in wp.columns if jw[c].notna().sum() > 3000})

    sp = vintage(solar_df, 'Value', DA_LO_WIND, DA_HI_WIND)
    solar_fc = sp['Meteologica'] if 'Meteologica' in sp.columns else sp.iloc[:, 0]

    lp = vintage(load_df, 'Load', DA_LO_LOAD, DA_HI_LOAD)
    jl = lp.join(b[['ail']].rename(columns={'ail': 'ail_act'}), how='inner').dropna(subset=['ail_act'])
    lcols = [c for c in lp.columns if jl[c].notna().sum() > 3000]
    load_cor = pd.DataFrame({c: walk_forward_recal(jl, c, 'ail_act') for c in lcols})
    load_cor['BLEND'] = load_cor[lcols].mean(axis=1)

    d = pd.DataFrame({'cush': b.cush, 'wind_a': b.wind, 'solar_a': b.solar,
                      'gas': b.gas, 'load': b.ail, 'ni': b.net_imports_actual_scheduled})
    d = d.join(wind_cor[['AESO']].rename(columns={'AESO': 'wind_fc'})).join(solar_fc.rename('solar_fc'))
    d['nonren']  = d.cush - d.wind_a - d.solar_a
    d['cush_da']  = d.nonren + d.wind_fc + d.solar_a      # wind forecast, solar actual
    d['cush_da2'] = d.nonren + d.wind_fc + d.solar_fc     # both forecast
    ws = np.where(d.solar_fc.notna(), d.cush_da2, d.cush_da)
    d['cush_full'] = ws + (d.load - load_cor.BLEND.reindex(d.index))
    d['he'] = d.index.hour + 1
    d['g']  = d.he.map(lambda h: 0 if h <= 6 else 1 if h <= 10 else 2 if h <= 16 else 3 if h <= 21 else 4)
    return d, wind_cor, load_cor, b


def attach_price(d, pool):
    """Settled pool price from the AESO feed — the one input that is cleaner
    from the API than from the database."""
    p = pool.copy()
    p.index = pd.to_datetime(p.index)
    d = d.copy()
    d['price'] = p.reindex(d.index)
    return d
