"""
cards.py -- the "day read" cards on the Fundamentals-first view, one set per date.

Seven cards per day: calendar & temperature, load, gas & ramp, wind, supply,
net it, and the trade checks. Every number comes from the same inputs the page's
model uses that morning. Every "tested" sentence comes from the live-model replay
(Oct 2024 - Sep 2026: StormVista wind/solar, corrected thermal, the page's import
rule; 674 mornings, 8,735 day-reads scored against what settled).
A card that cannot be built says so instead of breaking the page.
"""
import json, zipfile
from pathlib import Path
import numpy as np, pandas as pd


def _ord(x):
    n = int(round(x)); return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _tg(he): return 0 if he <= 6 else 1 if he <= 10 else 2 if he <= 16 else 3 if he <= 21 else 4


def _holidays(y):
    def nth(m, wd, n):
        d = pd.Timestamp(y, m, 1); d += pd.Timedelta(days=(wd - d.dayofweek) % 7)
        return d + pd.Timedelta(weeks=n - 1)
    a = y % 19; b = y // 100; c = y % 100; dd = b // 4; e = b % 4; f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - dd - g + 15) % 30; i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    easter = pd.Timestamp(y, (h + l - 7 * m + 114) // 31, ((h + l - 7 * m + 114) % 31) + 1)
    vic = pd.Timestamp(y, 5, 24); vic -= pd.Timedelta(days=vic.dayofweek % 7)
    return {pd.Timestamp(y, 1, 1): 'New Year', nth(2, 0, 3): 'Family Day', easter - pd.Timedelta(days=2): 'Good Friday',
            vic: 'Victoria Day', pd.Timestamp(y, 7, 1): 'Canada Day', nth(8, 0, 1): 'Heritage Day',
            nth(9, 0, 1): 'Labour Day', nth(10, 0, 2): 'Thanksgiving', pd.Timestamp(y, 11, 11): 'Remembrance Day',
            pd.Timestamp(y, 12, 25): 'Christmas', pd.Timestamp(y, 12, 26): 'Boxing Day'}


# replay results quoted on the cards
DOW_NOTE = {'Mon': 'Mondays settle in line with the model (tested).',
            'Tue-Thu': 'Tue-Thu settle about $6-9 below the model on average (tested).',
            'Fri': 'Fridays settle about $18 below the model on average; selling a Friday paid +$7-10/MWh (tested).',
            'Sat': 'Saturdays settle about $6-9 below the model; selling paid +$2-8/MWh (tested).',
            'Sun': 'Sundays settle about $7-12 below the model; selling paid +$4-11/MWh (tested).',
            'Holiday': 'Holiday - load behaves like a weekend. Not separately tested (too few days).'}
CALL_BT = {'SELL': {'d1': '66 next-day sells in the sized book, 80% won, +$271k at 10/20 MW, worst -$15k', 'd2-3': '190 days, 92% won, +$24.0/MWh (weekday single days 2-4 out: 113 passed, 90%+ won, +$21-25/MWh)',
                    'd4-7': '503 days, 88% won, +$19.3/MWh', 'd8-13': '1,138 days, 81% won, +$12.7/MWh'},
           'WATCH LONG': {'d1': '147 days, 49% won, +$17.0/MWh average from a few big wins, worst -$105',
                           'd2-3': '352 days, 47% won, +$13.6/MWh, worst -$79'}}


def build(root, f, comp, grid, say=print):
    root = Path(root); today = pd.Timestamp.now().normalize()
    G = {}
    for r in grid: G.setdefault((r['g'], r.get('L', 0) or 0), []).append(r)
    for k in G: G[k].sort(key=lambda r: r['c'])

    def look(c, g, L, key='mean'):
        a = G.get((g, L)) or G[(g, 0)]
        return float(np.interp(c, [r['c'] for r in a], [r[key] for r in a]))

    def lead_of(t): return int((pd.Timestamp(t).normalize() - today).days)
    def bucket_of(L, cal):  # same as page.bucket_of
        for b in cal.get('buckets', []):
            if b['lo'] <= L <= b['hi']: return int(b['L'])
        return int(cal['buckets'][-1]['L']) if cal.get('buckets') and L > cal['buckets'][-1]['hi'] else 0
    lc = root / 'model' / 'leadcal.json'
    cal = json.loads(lc.read_text()) if lc.exists() else {}
    F = f.copy(); F['ld'] = [lead_of(t) for t in F.index]; F['he'] = F.index.hour + 1
    F['day'] = F.index.normalize(); F['gasv'] = F[['cc', 'sc', 'cogen', 'gfs']].sum(axis=1)
    F['netload'] = F['load'] - F.wind - F.solar
    F['lb'] = [bucket_of(max(L, 0), cal) for L in F.ld]
    F['ev'] = [look(c, _tg(he), int(lb)) for c, he, lb in zip(F.cush, F.he, F.lb)]
    F['p100'] = [look(c, _tg(he), int(lb), 'p100') for c, he, lb in zip(F.cush, F.he, F.lb)]

    # ---------------- history shared by the cards
    C = comp.copy(); C['h'] = C.index.hour; C['netload'] = C.ail - C.wind - C.solar
    W = wf = None
    try: W = pd.read_csv(root / 'cache' / 'wx_hourly.csv', parse_dates=['t']).set_index('t').temp
    except Exception: pass
    try: wf = pd.read_csv(root / 'cache' / 'wx_fcst.csv', parse_dates=['t']).set_index('t').temp
    except Exception: pass
    # daily load ~ heating + cooling degrees + weekend + trend, last 365 days
    slope_h, slope_c = 37.0, 140.0
    try:
        J = pd.DataFrame({'l': C.ail.resample('D').mean(), 't': W.resample('D').mean()}).dropna()
        J = J[J.index >= J.index.max() - pd.Timedelta(days=365)]
        X = np.c_[np.ones(len(J)), np.clip(12 - J.t, 0, None), np.clip(J.t - 16, 0, None),
                  (J.index.dayofweek >= 5).astype(float), np.arange(len(J)) / 365.0]
        beta = np.linalg.lstsq(X, J.l.values, rcond=None)[0]; slope_h, slope_c = float(beta[1]), float(beta[2])
    except Exception as e: say(f"  cards: load-temperature fit skipped ({e})")

    def per_c(t): return slope_h if t < 12 else slope_c if t > 16 else 0.0

    def temp_adj(t_from, t_to):
        return slope_h * (max(12 - t_to, 0) - max(12 - t_from, 0)) + slope_c * (max(t_to - 16, 0) - max(t_from - 16, 0))

    LV = None
    try:
        LV = pd.read_csv(root / 'cache' / 'load_fc.csv')
        LV['iss'] = pd.to_datetime(LV.Timestamp, errors='coerce'); LV['tgt'] = pd.to_datetime(LV.EffectiveDateTime, errors='coerce')
        if getattr(LV.tgt.dt, 'tz', None) is not None: LV['tgt'] = LV.tgt.dt.tz_localize(None)
        LV = LV.dropna(subset=['iss', 'tgt', 'Load']); LV['lead_h'] = (LV.tgt - LV.iss).dt.total_seconds() / 3600
        LV = LV[LV.tgt >= today - pd.Timedelta(days=40)]
    except Exception: LV = None

    def latest(src, t):
        if LV is None: return None
        g = LV[(LV.DataSourceName == src) & (LV.tgt == t)]
        return float(g.sort_values('iss').Load.iloc[-1]) if len(g) else None

    def da_vintage(src, t):
        if LV is None: return None
        g = LV[(LV.DataSourceName == src) & (LV.tgt == t) & LV.lead_h.between(16, 40)]
        return float(g.sort_values('iss').Load.iloc[-1]) if len(g) else None

    def miss30(src, he):
        if LV is None: return None
        g = LV[(LV.DataSourceName == src) & LV.lead_h.between(16, 40) & (LV.tgt.dt.hour == he - 1)
               & (LV.tgt >= today - pd.Timedelta(days=30)) & (LV.tgt < today)]
        g = g.sort_values('iss').groupby('tgt').Load.last(); a = C.ail.reindex(g.index); m = a.notna()
        return float((a[m] - g[m]).mean()) if m.sum() >= 10 else None

    def ramps(nl):  # nl indexed by hour-beginning 0..23 ; HE5->8 = 4->7 ; HE15->19 = 14->18
        return nl.get(7, np.nan) - nl.get(4, np.nan), nl.get(18, np.nan) - nl.get(14, np.nan)

    C60 = C[C.index >= today - pd.Timedelta(days=60)]
    RH = pd.DataFrame([dict(zip(('mr', 'er'), ramps(g.set_index('h').netload))) for _, g in C60.groupby(C60.index.normalize())])
    wyr = float(C.wind[C.index >= today - pd.Timedelta(days=365)].mean())
    mfac = C.wind.groupby(C.index.month).mean() / C.wind.mean()

    def gencap_out(txt):
        j = json.loads(txt); rows = []
        for b in j['return']:
            for h in b['Hours']:
                og = h['outage_grouping']
                rows.append((pd.Timestamp(h['begin_datetime_mpt']), b['fuel_type'], b['sub_fuel_type'], og.get('MC') or 0, og.get('AC') or 0, og.get('MBO OUT') or 0))
        X = pd.DataFrame(rows, columns=['t', 'fuel', 'sub', 'mc', 'ac', 'mbo'])
        X['out'] = X.mc - X.ac - X.mbo      # operating outages only; mothballed units (MBO) are shown on their own line
        X['day'] = X.t.dt.normalize()
        return X

    GN = GH = None
    try:
        GN = gencap_out((root / 'refresh' / 'gencap.json').read_text(encoding='utf-8-sig'))
        hist = []
        for z in sorted((root / 'archive').glob('20??-??-??.zip'))[-31:]:
            d0 = pd.Timestamp(z.stem)
            if d0 >= today: continue
            X = gencap_out(zipfile.ZipFile(z).read('gencap.json').decode('utf-8-sig')); X['vint'] = d0; hist.append(X)
        GH = pd.concat(hist) if hist else None
    except Exception as e: say(f"  cards: outage history skipped ({e})")
    fw = None
    try:
        fq = pd.read_csv(root / 'cache' / 'fwd_aeso_daily.csv', parse_dates=['EffectiveDate', 'Strip'])
        fq = fq[fq.EffectiveDate <= today]; sd = fq.EffectiveDate.max()
        fw = (sd, fq[fq.EffectiveDate == sd].pivot_table(index='Strip', columns='ExchangeCode', values='Price'))
    except Exception: pass
    ph = pd.read_csv(root / 'model' / 'price_history.csv', index_col=0, parse_dates=True).iloc[:, 0]
    pool = ph.groupby(ph.index.normalize()).mean()
    last7 = float(pool[today - pd.Timedelta(days=7):today - pd.Timedelta(days=1)].mean())
    hol = {**_holidays(today.year), **_holidays(today.year + 1)}

    out = {}
    for day, g in F[F.price.isna()].groupby('day'):
        if len(g) < 12: continue
        g = g.set_index('he'); L = int(g.ld.iloc[0]); cards = []; calc = None

        def card(title, rows, note, tested=None, warn=False):
            cards.append({'t': title, 'rows': [[a, b] for a, b in rows if b is not None], 'note': note, 'tested': tested, 'warn': bool(warn)})

        tday = wf[day:day + pd.Timedelta(hours=23)] if wf is not None else pd.Series(dtype=float)
        thi, tlo, tav = (float(tday.max()), float(tday.min()), float(tday.mean())) if len(tday) >= 20 else (None, None, None)
        # 1 calendar & temperature
        try:
            dtype = 'Holiday' if day in hol else ['Mon', 'Tue-Thu', 'Tue-Thu', 'Tue-Thu', 'Fri', 'Sat', 'Sun'][day.dayofweek]
            nrm = None
            if W is not None:
                dd = W.groupby(W.index.normalize()).mean(); doy = dd.index.dayofyear
                dz = np.minimum(np.abs(doy - day.dayofyear), 365 - np.abs(doy - day.dayofyear)); nrm = float(dd[dz <= 7].mean())
            anom = None if tav is None or nrm is None else tav - nrm
            pc = per_c(tav) if tav is not None else None
            side = None if tav is None else ('cold side' if tav < 12 else 'hot side' if tav > 16 else 'mild - load barely moves with temperature')
            tested = DOW_NOTE[dtype]
            if anom is not None and anom >= 6: tested += f' Days this much warmer than normal: selling paid +$13-19/MWh at 4-13 days out (tested).'
            elif anom is not None and anom <= -6: tested += f' Days this much colder than normal: selling LOST $8-12/MWh at 4-13 days out (tested).'
            note = ('No temperature forecast reaches this day.' if tav is None else
                    f"{'Cold' if tav < 12 else 'Hot' if tav > 16 else 'Mild'} side: the risk is a {'colder' if tav < 12 else 'hotter' if tav > 16 else 'different'} day than forecast, which adds load.")
            card('1 · Calendar & temperature', [
                ('Day type', dtype + (f' ({hol[day]})' if day in hol else '')),
                ('Alberta high / low (3-city avg)', None if thi is None else f'{thi:.0f}°C / {tlo:.0f}°C'),
                ('Daily average', None if tav is None else f'{tav:.0f}°C — {side}'),
                ('Normal for the date', None if anom is None else f'{nrm:.0f}°C (forecast {anom:+.0f}°C)'),
                ('Load per °C here', None if pc is None else f'{pc:.0f} MW'),
                ('±2°C bust', None if pc is None else f'±{2 * pc:,.0f} MW')], note, tested)
        except Exception as e: card('1 · Calendar & temperature', [], f'Unavailable today ({type(e).__name__}: {e}).', warn=True)
        # 2 load at the peak hour
        try:
            pk = int(g.loc[8:23, 'load'].idxmax()); t = day + pd.Timedelta(hours=pk - 1)
            aeso = latest('AESO', t); tesla = latest('Tesla', t); mi = miss30('AESO', pk)
            adj = None if aeso is None or mi is None else aeso + mi
            parts = [x for x in (adj, tesla) if x is not None]; blend = float(np.mean(parts)) if parts else None
            lw = t - pd.Timedelta(days=7); lwa = C.ail.get(lw); lwf = da_vintage('AESO', lw)
            tl = None if W is None else W.get(lw); tf = None if wf is None else wf.get(t)
            tadj = None if tl is None or tf is None or lwa is None else temp_adj(tl, tf)
            base = None if tadj is None else lwa + tadj
            used = float(g.loc[pk, 'load']); vs = None if base is None else used - base
            tested = None
            if vs is not None and L >= 4:
                tested = ('Far-out load over 300 MW ABOVE this level: the model ran high and selling paid +$8-18/MWh (tested).' if vs > 300 else
                          'Far-out load over 300 MW BELOW this level: the model ran LOW and selling lost $13-22/MWh (tested).' if vs < -300 else
                          'Load near the temperature-adjusted level - no tilt (tested).')
            note = '' if vs is None else (f"The model's load is {abs(vs):,.0f} MW {'above' if vs > 0 else 'below'} last week's temperature-adjusted level"
                                          + (' - worth a look.' if abs(vs) > 300 else ' - normal.'))
            card(f'2 · Load (peak HE{pk})', [
                ('Load the model uses', f'{used:,.0f}'), ('AESO forecast', None if aeso is None else f'{aeso:,.0f}'),
                ('AESO + its 30-day miss', None if adj is None else f'{adj:,.0f} ({mi:+,.0f})'),
                ('Tesla', None if tesla is None else f'{tesla:,.0f}'), ('Blend', None if blend is None else f'{blend:,.0f}'),
                (f'Same hour last week ({lw:%a %d %b})', None if lwa is None else f'{lwa:,.0f} actual' + ('' if tl is None else f' at {tl:.0f}°C')),
                ('… AESO had forecast', None if lwf is None or lwa is None else f'{lwf:,.0f} (missed {lwa - lwf:+,.0f})'),
                ('… temperature adjustment', None if tadj is None else f'{tadj:+,.0f} → baseline {base:,.0f}'),
                ('Model load vs that baseline', None if vs is None else f'{vs:+,.0f}')], note, tested)
        except Exception as e: card('2 · Load', [], f'Unavailable today ({type(e).__name__}: {e}).', warn=True)
        # 3 gas & ramp
        try:
            ti = int(g.cush.idxmin()); r = g.loc[ti]
            need = r.gasv - r.cush  # gas the model needs = available minus the cushion
            before_sc = r.cush - r.sc
            st = g.netload.diff(); up, dn = int(st.idxmax()), int(st.idxmin())
            mr, er = ramps(pd.Series(g.netload.values, index=g.index - 1))
            mp = None if RH.empty or np.isnan(mr) else float((RH.mr < mr).mean() * 100)
            ep = None if RH.empty or np.isnan(er) else float((RH.er < er).mean() * 100)
            tested = ('Morning ramps in the top quarter of the last 60 days: selling the day LOST $9-13/MWh (tested).'
                      if (mp is not None and mp >= 75) else 'Ramp size on its own has no consistent price effect (tested).')
            note = ('Needs simple cycle at the tightest hour - peakers set the price.' if before_sc < 0 else
                    'Thin margin before simple cycle.' if before_sc < 500 else 'Plenty of gas before simple cycle is needed.')
            card('3 · Gas & ramp', [
                ('Gas available (tightest hour)', f'{r.gasv:,.0f} MW'), (f'Gas needed (HE{ti})', f'{need:,.0f} MW'),
                ('Spare before simple cycle', f'{before_sc:+,.0f} MW'),
                (f'Biggest 1-h net-load step up (HE{up})', f'{st[up]:+,.0f} MW'), (f'Biggest 1-h step down (HE{dn})', f'{st[dn]:+,.0f} MW'),
                ('Morning ramp HE5→8 (load − wind − solar)', None if np.isnan(mr) else f'{mr:+,.0f} MW' + ('' if mp is None else f' · {_ord(mp)} pct (60d)')),
                ('Evening ramp HE15→19', None if np.isnan(er) else f'{er:+,.0f} MW' + ('' if ep is None else f' · {_ord(ep)} pct (60d)'))],
                note, tested, warn=before_sc < 0)
        except Exception as e: card('3 · Gas & ramp', [], f'Unavailable today ({type(e).__name__}: {e}).', warn=True)
        # 4 wind HE17-20
        try:
            ev = g.loc[17:20]; wv = float(ev.wind.mean()); norm = wyr * float(mfac.get(day.month, 1.0)); rel = wv / norm if norm else None
            wa = float(ev.wind_aeso.mean()) if ('wind_aeso' in ev and 2 <= L <= 7) else None
            p10 = float(ev.sv_p10.mean()) if ('sv_p10' in ev and ev.sv_p10.notna().all()) else None
            p90 = float(ev.sv_p90.mean()) if ('sv_p90' in ev and ev.sv_p90.notna().all()) else None
            rr = float(g.wind.get(20, np.nan) - g.wind.get(15, np.nan))
            tested = ('Evening wind under half of normal: the model ran HIGH by $11-29 and selling LOST $7-15/MWh (tested).' if rel is not None and rel < .5 else
                      'Evening wind above 120% of normal: selling paid +$10-15/MWh (tested).' if rel is not None and rel > 1.2 else
                      'Evening wind near normal - no tilt (tested).')
            note = 'Calm evening (under 70% of the fleet average).' if wv < 0.7 * wyr else ''
            if wa is not None and abs(wa - wv) > 400: note += f' AESO and StormVista disagree by {abs(wa - wv):,.0f} MW - check the timing.'
            card('4 · Wind (HE17–20)', [
                ('AESO forecast' if L <= 1 else 'StormVista (corrected) - used', f'{wv:,.0f} MW'),
                ('AESO feed (second opinion)', None if wa is None else f'{wa:,.0f} MW'),
                ('Ensemble P10–P90', None if p10 is None else f'{p10:,.0f} – {p90:,.0f} MW'),
                ('Normal for the month', None if rel is None else f'{norm:,.0f} MW (forecast is {100 * rel:.0f}%)'),
                ('Ramp HE15 → HE20', None if np.isnan(rr) else f'{rr:+,.0f} MW')], note.strip(), tested, warn=rel is not None and rel < .5)
        except Exception as e: card('4 · Wind', [], f'Unavailable today ({type(e).__name__}: {e}).', warn=True)
        # 5 supply
        try:
            rows = []
            if GN is not None:
                gd = GN[(GN.day == day) & (GN.fuel == 'GAS')].groupby('t').out.sum()
                if len(gd):
                    gas_out = float(gd.mean()); rows.append(('Gas out - operating outages', f'{gas_out:,.0f} MW'))
                    if GH is not None:
                        h0 = GH[(GH.fuel == 'GAS') & (GH.day == GH.vint)].groupby(['vint', 't']).out.sum().groupby('vint').mean()
                        if len(h0) >= 5: rows.append(('… vs last 30 mornings', f'avg {h0.mean():,.0f} · higher than {int((h0 < gas_out).sum())} of {len(h0)}'))
                        prev = GH[(GH.vint == GH.vint.max()) & (GH.day == day) & (GH.fuel == 'GAS')]
                        if len(prev):
                            ch = gas_out - float(prev.groupby('t').out.sum().mean())
                            rows.append((f'Change since the {GH.vint.max():%a %d %b} report', f'{ch:+,.0f} MW out'))
                    sub = GN[(GN.day == day) & (GN.fuel == 'GAS')].groupby(['sub', 't']).out.sum().groupby('sub').mean().sort_values(ascending=False)
                    rows.append(('Most out', ', '.join(f"{k.replace('_', ' ').title()} {v:,.0f}" for k, v in sub.head(2).items())))
                    mb = GN[(GN.day == day) & (GN.fuel == 'GAS')].groupby('t').mbo.sum()
                    if len(mb) and mb.mean() > 0:
                        rows.append(('Mothballed gas (not counted above)', f'{mb.mean():,.0f} MW' + (' - Sundance 6 (401) + Sheerness 1 (~400), economic mothballs' if 790 <= mb.mean() <= 810 else ' - check which units (the mothball total changed)')))
                hd = GN[(GN.day == day) & (GN['sub'] == 'HYDRO')].groupby('t').out.sum()
                if len(hd): rows.append(('Hydro out', f'{hd.mean():,.0f} MW'))
            rows.append(('Gas the model uses (corrected outlook)', f'{g.gasv.mean():,.0f} MW average'))
            if 'imp' in g and g.imp.loc[17:20].notna().any():
                rows.append(('Import capability HE17–20', f'{g.imp.loc[17:20].mean():,.0f} MW · assumed {g.it.loc[17:20].mean():+,.0f}'))
            card('5 · Supply', rows, 'Operating outages AESO already knows about (mothballed units excluded - they are not available and are not expected back soon). The model uses the corrected AESO outlook, which runs lower further out.',
                 'Outage history only goes back to Sep 20, so supply reads are not backtested yet.')
        except Exception as e: card('5 · Supply', [], f'Unavailable today ({type(e).__name__}: {e}).', warn=True)
        # 6 net it, 7 checks
        try:
            ti = int(g.cush.idxmin()); model = float(g.ev.mean()); mon = float(g.loc[8:23].ev.mean())
            fwdp = fon = sdate = None
            if fw is not None and day in fw[1].index:
                sdate, tab = fw; fwdp = float(tab.loc[day, 'XDT']); fon = float(tab.loc[day, 'XDQ']) if 'XDQ' in tab else None
            gap = None if fwdp is None else fwdp - model
            bar = 25.0 if day.month in (7, 8, 9, 10, 11) else 10.0
            ev = g.loc[17:20]; ew = float(ev.wind.mean())
            calm = ew < 0.7 * wyr; extreme = thi is not None and (thi >= 28 or tlo <= -20)
            veto_t = float(g.cush.min()) < 800; veto_w = ew < 0.5 * wyr * float(mfac.get(day.month, 1.0))
            c1 = gap is not None and gap > bar; c2 = day.dayofweek >= 5 or (fwdp is not None and fwdp >= last7); c3 = not (calm and extreme)
            # the ingredients, so the page can re-run this call on a broker bid/offer you type in
            calc = {'model': round(model, 2), 'model_on': round(mon, 2), 'fwd': fwdp, 'fwd_on': fon,
                    'settle_date': None if sdate is None else str(sdate.date()), 'last7': round(last7, 2), 'bar': bar,
                    'calm': bool(calm), 'extreme': bool(extreme), 'veto_t': bool(veto_t), 'veto_w': bool(veto_w), 'L': L, 'wkday': bool(day.dayofweek <= 4)}
            if c1 and c2 and c3: call = 'SELL'
            elif gap is not None and gap <= -15 and L <= 3: call = 'WATCH LONG'
            else: call = 'NO TRADE'
            if L <= 0: call = 'TODAY - not tradeable'
            lg = 'd1' if L <= 1 else 'd2-3' if L <= 3 else 'd4-7' if L <= 7 else 'd8-13'
            bt = CALL_BT.get(call, {}).get(lg)
            tested = f"This call at this distance: {bt} (replay, Oct 2024 - Sep 2026)." if bt else 'No trade - nothing to test.'
            note = {'SELL': 'The forward is rich against the model and all three checks pass.',
                    'WATCH LONG': 'The model is $15+ above the forward. Not a tested trade: a long here won only about half the time, the money coming from a few big days. A reason not to sell this day; any long is your call.',
                    'NO TRADE': 'No edge big enough on this day by itself.',
                    'TODAY - not tradeable': 'Today is already trading in real time - shown for reference.'}[call]
            if veto_t: note += ' Caution - tight day (cushion under 800 MW). Not a blocker: in the sized backtest book the 10 next-day sells that carried a caution won only half the time but still netted +$34k.'
            if veto_w: note += ' Caution - evening wind under half of normal. Not a blocker: in the sized backtest book the 10 next-day sells that carried a caution won only half the time but still netted +$34k.'
            card('6 · Net it', [
                ('Tightest hour', f'HE{ti} · {g.cush.min():,.0f} MW'), ('Chance of > $100 at that hour', f'{100 * float(g.loc[ti, "p100"]):.0f}%'),
                ('Model fair value (flat / on-peak)', f'${model:,.2f} / ${mon:,.2f}'),
                ('Forward settle' + ('' if sdate is None else f' {sdate:%d %b}') + ' (flat / on-peak)', None if fwdp is None else f'${fwdp:,.2f}' + ('' if fon is None else f' / ${fon:,.2f}')),
                ('Gap (forward − model)', None if gap is None else f'{gap:+,.2f}'), ('Model says', call)],
                note, tested, warn=call in ('SELL', 'WATCH LONG'))
            card('7 · The trade checks', [
                ('Gap over the bar', f"{'✓' if c1 else '✗'} " + ('no forward' if gap is None else f'{gap:+.2f}') + f" vs ${bar:.0f}" + (' (Jul–Nov)' if bar == 25 else '')),
                ("Forward ≥ last week's pool", f"{'✓' if c2 else '✗'} " + ('no forward' if fwdp is None else f'${fwdp:.2f}') + f" vs ${last7:.2f}"),
                ('Not calm AND extreme', f"{'✓' if c3 else '✗'} {'calm' if calm else 'not calm'}, {'extreme' if extreme else 'no extreme temps'}"),
                ('Cautions (do not block)', 'none' if not (veto_t or veto_w) else ', '.join(x for x, v in (('tight day', veto_t), ('very calm evening', veto_w)) if v)),
                ('Tradeable as', 'same day (info only)' if L <= 0 else 'weekend package, decided Thu/Fri (single weekend days info only)' if day.dayofweek >= 5 else ('next day' if L == 1 else f'single day ({L} out)') + (' + current-week strip' if day <= today + pd.Timedelta(days=(4 - today.dayofweek) % 7) and today.dayofweek <= 1 and day.dayofweek <= 3 else '') if L <= 6 - today.dayofweek and day.dayofweek <= 4 else 'next week Mon-Fri strip' if L <= 13 - today.dayofweek else 'week after next (no read yet)')],
                'All three must pass to sell (weekend days skip the pool check). Cautions come from the card backtest and do not block - in the sized book they cut profit. Contracts, sizes, live quotes and the trade log are on the Trades tab.')
        except Exception as e: card('6 · Net it', [], f'Unavailable today ({type(e).__name__}: {e}).', warn=True)
        out[str(day.date())] = {'lead': L, 'cards': cards, 'calc': calc}
    bad = sorted({c['t'] for v in out.values() for c in v['cards'] if not c['rows']})
    say(f"day cards: {len(out)} days" + (f"  WARNING blank: {', '.join(bad)}" if bad else ''))
    return out
