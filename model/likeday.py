"""
likeday.py - which days in the last two years had weather shaped like today's.

Weather only. Load is deliberately NOT part of the search - it is the thing we
want to learn about. If load were in the distance, every match would be a day
with today's load and the answer would be circular.

Four variables, each as a 24-hour shape:

    temp    deg C     temperature
    wind    km/h      wind speed at 100 m
    rad     W/m^2     shortwave radiation
    cloud   %         cloud cover

Each is z-scored against its own two-year history before anything is squared.
Without that step temperature (range ~60) and cloud (range 100) would swamp
wind (range ~50) purely because of the units they happen to be measured in.

Distance for one variable is the root-mean-square gap across the 24 hours.
The overall score is the plain average of the four - the four were asked for
as equals, so they are weighted as equals. Zero means identical weather.

The last 7 days are excluded from the search. Weather runs in spells, so the
three nearest days would otherwise almost always be this week - which you
already remember, and whose price has barely settled.
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path

VARS = ['temp', 'wind', 'rad', 'cloud']
MIN_HOURS = 23           # a day must be near-complete to be comparable
EXCLUDE_RECENT = 7       # days
NTOP = 3


def _daily_matrix(wx):
    """hourly frame -> {var: DataFrame(day x hour 0..23)}, complete days only."""
    w = wx.copy()
    w['t'] = pd.to_datetime(w['t'])
    w = w.dropna(subset=['t']).sort_values('t').drop_duplicates('t', keep='last')
    w['day'] = w.t.dt.normalize()
    w['h'] = w.t.dt.hour
    out, keep = {}, None
    for v in VARS:
        p = w.pivot_table(index='day', columns='h', values=v, aggfunc='mean')
        p = p.reindex(columns=range(24))
        ok = p.notna().sum(axis=1) >= MIN_HOURS
        keep = ok if keep is None else (keep & ok)
        out[v] = p
    for v in VARS:
        out[v] = out[v][keep].interpolate(axis=1, limit_direction='both')
    return out


def _normal_fn(daily, n_harm=2):
    """What load is normally doing on a day like this one.

    A smooth seasonal curve - two annual harmonics plus a straight trend for
    the province growing - fitted across the whole history, with separate
    levels for Saturday and Sunday. It is the shape of the year, not a 30-day
    average, so a cold snap inside the window cannot drag 'normal' toward
    itself.

    The weekend terms matter. Alberta runs roughly 700 MW lighter on a Saturday
    and lighter still on a Sunday. Without them, every weekend match would come
    back reading 'load was below normal' when all that happened is that it was
    a weekend. Normal means normal for that kind of day.

    Returns f(DatetimeIndex) -> ndarray, so today can be priced off the same
    fit even though today has not finished yet.
    """
    s = daily.dropna()
    t0 = s.index[0] if len(s) else pd.Timestamp('2024-01-01')

    def design(idx):
        doy = idx.dayofyear.values.astype(float)
        tt = (idx - t0).days.values / 365.25
        dow = idx.dayofweek.values
        cols = [np.ones(len(idx)), tt,
                (dow == 5).astype(float), (dow == 6).astype(float)]
        for k in range(1, n_harm + 1):
            cols += [np.sin(2 * np.pi * k * doy / 365.25),
                     np.cos(2 * np.pi * k * doy / 365.25)]
        return np.column_stack(cols)

    if len(s) < 120:
        m = float(s.mean()) if len(s) else np.nan
        return lambda idx: np.full(len(idx), m)

    b, *_ = np.linalg.lstsq(design(s.index), s.values, rcond=None)
    return lambda idx: design(pd.DatetimeIndex(idx)) @ b


def build(root, loads_by_day=None, say=print):
    """Returns the payload block, or None if the weather history is not there.

    loads_by_day: optional {'YYYY-MM-DD': [24 values]} from the page's own
    hourly frame - actual for the hours that have happened, AESO's forecast for
    the rest. Today is only part-settled in the composition table, so without
    this today's load curve would stop mid-afternoon.
    """
    root = Path(root)
    wxp = root / 'cache' / 'wx_hourly.csv'
    if not wxp.exists():
        say("  like-day: cache/wx_hourly.csv is not there - run  python wx.py  first. Panel skipped.")
        return None

    wx = pd.read_csv(wxp)
    missing = [v for v in VARS if v not in wx.columns]
    if missing:
        say(f"  like-day: wx_hourly.csv is missing {', '.join(missing)}. Panel skipped.")
        return None

    M = _daily_matrix(wx)
    days = M['temp'].index
    if len(days) < 60:
        say(f"  like-day: only {len(days)} usable weather days. Panel skipped.")
        return None

    # ---- load history, hourly, from the composition table ------------------
    comp = pd.read_csv(root / 'cache' / 'composition.csv')
    comp['t'] = pd.to_datetime(comp['datetime_begin'])
    if 'lead_bucket' in comp:
        comp = comp[comp.lead_bucket == -1]
    comp = (comp.dropna(subset=['t', 'ail']).sort_values('t')
                .drop_duplicates('t', keep='last'))
    comp['day'] = comp.t.dt.normalize()
    comp['h'] = comp.t.dt.hour
    LD = comp.pivot_table(index='day', columns='h', values='ail', aggfunc='mean')
    LD = LD.reindex(columns=range(24))
    LD = LD[LD.notna().sum(axis=1) >= MIN_HOURS].interpolate(axis=1, limit_direction='both')

    daily_mean = LD.mean(axis=1)
    normal_of = _normal_fn(daily_mean)

    # settled pool price, if the model has it
    PX = None
    pxp = root / 'model' / 'price_history.csv'
    if pxp.exists():
        try:
            p = pd.read_csv(pxp, index_col=0, parse_dates=True)
            col = [c for c in p.columns if 'price' in c.lower()][0]
            g = p[col].groupby(p.index.normalize())
            PX = pd.DataFrame({'avg': g.mean(), 'max': g.max(),
                               'n100': g.apply(lambda x: int((x > 100).sum()))})
        except Exception as e:
            say(f"  like-day: could not read price_history.csv ({e}); prices omitted.")

    # ---- today -------------------------------------------------------------
    today = days.max()
    Z, mu, sd = {}, {}, {}
    for v in VARS:
        a = M[v].values
        mu[v], sd[v] = float(np.nanmean(a)), float(np.nanstd(a))
        if sd[v] == 0:
            sd[v] = 1.0
        Z[v] = (a - mu[v]) / sd[v]

    i_today = list(days).index(today)
    cutoff = today - pd.Timedelta(days=EXCLUDE_RECENT)
    cand = np.array([i for i, d in enumerate(days) if d < cutoff])
    if len(cand) < 30:
        say(f"  like-day: only {len(cand)} candidate days. Panel skipped.")
        return None

    per = {v: np.sqrt(((Z[v][cand] - Z[v][i_today]) ** 2).mean(axis=1)) for v in VARS}
    score = np.mean([per[v] for v in VARS], axis=0)
    top = cand[np.argsort(score)[:NTOP]]

    def series(v, i):
        return [round(float(x), 1) for x in M[v].values[i]]

    def load_of(d):
        if d in LD.index:
            return [round(float(x)) for x in LD.loc[d].values]
        return None

    # today's load: the caller's vector wins (it carries AESO's forecast for
    # the hours that have not happened yet); otherwise whatever has settled.
    t_load = None
    if loads_by_day:
        v = loads_by_day.get(str(today.date()))
        if v and len(v) == 24:
            t_load = [None if x is None or (isinstance(x, float) and np.isnan(x))
                      else round(float(x)) for x in v]
    if t_load is None:
        t_load = load_of(today)

    def stats(i):
        return {'tmean': round(float(np.mean(M['temp'].values[i])), 1),
                'wmean': round(float(np.mean(M['wind'].values[i])), 1),
                'rtot': round(float(np.sum(M['rad'].values[i])) / 1000.0, 2),
                'cmean': round(float(np.mean(M['cloud'].values[i])))}

    t_stats = stats(i_today)
    t_norm = float(normal_of([today])[0])
    t_known = [x for x in (t_load or []) if x is not None]
    t_lmean = round(float(np.mean(t_known))) if t_known else None

    out = {
        'day': str(today.date()),
        'dow': today.strftime('%a'),
        'today': dict(t_stats,
                      load=t_load,
                      temp=series('temp', i_today), wind=series('wind', i_today),
                      rad=series('rad', i_today), cloud=series('cloud', i_today),
                      lmean=t_lmean,
                      # today is only part-settled until the composition table
                      # holds a near-complete day; the rest is AESO's forecast
                      partial=bool(today not in LD.index),
                      norm=None if np.isnan(t_norm) else round(t_norm),
                      vs=None if (t_lmean is None or np.isnan(t_norm)) else round(t_lmean - t_norm),
                      vspc=None if (t_lmean is None or np.isnan(t_norm))
                      else round(100.0 * (t_lmean - t_norm) / t_norm, 1)),
        'span': [str(days.min().date()), str(days.max().date())],
        'ndays': int(len(cand)),
        'excluded': EXCLUDE_RECENT,
        'matches': []}

    for i in top:
        d = days[i]
        st = stats(i)
        lm = float(daily_mean.get(d, np.nan))
        nm = float(normal_of([d])[0])
        m = {
            'd': str(d.date()),
            'dow': d.strftime('%a'),
            'back': int((today - d).days),
            'score': round(float(score[list(cand).index(i)]), 3),
            'per': {v: round(float(per[v][list(cand).index(i)]), 3) for v in VARS},
            'load': load_of(d),
            'temp': series('temp', i), 'wind': series('wind', i),
            'rad': series('rad', i), 'cloud': series('cloud', i),
            # deltas are ALWAYS like-day minus today
            'dt': round(st['tmean'] - t_stats['tmean'], 1),
            'dw': round(st['wmean'] - t_stats['wmean'], 1),
            'dr': round(st['rtot'] - t_stats['rtot'], 2),
            'dc': int(st['cmean'] - t_stats['cmean']),
        }
        m.update(st)
        m['lmean'] = None if np.isnan(lm) else round(lm)
        m['lpk'] = None if d not in LD.index else round(float(LD.loc[d].max()))
        m['norm'] = None if np.isnan(nm) else round(nm)
        if not np.isnan(lm) and not np.isnan(nm):
            m['vs'] = round(lm - nm)
            m['vspc'] = round(100.0 * (lm - nm) / nm, 1)
        else:
            m['vs'] = m['vspc'] = None
        if PX is not None and d in PX.index:
            m['px'] = {'avg': round(float(PX.loc[d, 'avg']), 2),
                       'max': round(float(PX.loc[d, 'max']), 2),
                       'n100': int(PX.loc[d, 'n100'])}
        out['matches'].append(m)

    say(f"  like-day: {len(cand):,} candidate days, best match "
        f"{out['matches'][0]['d']} (score {out['matches'][0]['score']})")
    return out


if __name__ == '__main__':
    import sys
    r = build(Path(__file__).resolve().parent.parent)
    print(json.dumps(r, indent=2)[:4000] if r else 'no payload')
