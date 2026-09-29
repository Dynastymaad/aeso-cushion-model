"""
outages.py — the outage schedule, its history, and what moved overnight.

    python outages.py            ingest today's report, write the page
    python outages.py --no-open  same, don't open the browser

Reads AESO's 90-day outage report (refresh/outage_90d.csv, pulled by update.py)
and every archived copy under archive/*.zip, keeps one row per (report date,
delivery date, fuel) in cache/outage_history.csv, and writes docs/outages.html.

WHAT AESO ACTUALLY PUBLISHES — read this before asking the page for a unit name
  The public report is MW out PER FUEL TYPE per day: SC, Cogen, CC, GFS, Hydro,
  Wind, Solar, Storage, Biomass. There is no unit, no start date, no end date.
  Alberta withholds asset-level outage plans. So "which unit" cannot come from
  here; the asset monitor infers it from live output against nameplate, and
  that is a different, live-only thing.
  What this report does carry, and what the page tracks:
    * the schedule itself, 90 days out, by fuel;
    * how it moved since yesterday — every (date, fuel) that changed, which is
      the only public trace of a pushback, an extension, or a new outage;
    * how many times each future date has been revised, so a number that has
      been re-cut six times reads as what it is;
    * once enough history exists, how good the report was at lead L against
      what was recorded — the table fills itself in as the archive grows.
  Rows dated on or before the report date are recorded outages, planned and
  forced together. Rows after it are the plan. The plan runs light: forced
  outages are not in it and planned ones slip. That is not a defect to
  correct here, it is what the multi-day model's regression weight is for.
"""
import argparse, io, re, sys, webbrowser, zipfile
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent
FUELS = ['SC', 'Cogen', 'CC', 'GFS', 'Hydro', 'Wind', 'Solar', 'Energy Storage', 'Biomass and Other']
THERMAL = ['SC', 'Cogen', 'CC', 'GFS']
HIST = ROOT/'cache'/'outage_history.csv'


def say(s=''): print(s, flush=True)


# ------------------------------------------------------------------ parse ---
def parse(text):
    lines = text.splitlines()
    asof = None
    for l in lines[:6]:
        m = re.search(r'received as of:","\s*\w+,\s*(\w+ \d+ \d{4})', l)
        if m: asof = pd.Timestamp(m.group(1))
    hdr = [k for k, l in enumerate(lines) if l.strip().startswith('"   Date')]
    if not hdr: return None, None
    df = pd.read_csv(io.StringIO('\n'.join(lines[hdr[0]:])), skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    df = df[df.Date.astype(str).str.match(r'\d\d-\w\w\w-\d{4}')].copy()
    df['Date'] = pd.to_datetime(df.Date, format='%d-%b-%Y')
    keep = ['Date'] + [c for c in FUELS if c in df.columns]
    df = df[keep]
    for c in keep[1:]: df[c] = pd.to_numeric(df[c], errors='coerce')
    long = df.melt(id_vars='Date', var_name='fuel', value_name='mw').dropna()
    long = long.rename(columns={'Date': 'date'})
    long.insert(0, 'rep', asof)
    return asof, long


def ingest():
    old = pd.read_csv(HIST, parse_dates=['rep', 'date']) if HIST.exists() else \
          pd.DataFrame(columns=['rep', 'date', 'fuel', 'mw'])
    have = set(pd.to_datetime(old.rep).dt.normalize()) if len(old) else set()
    new = []
    for z in sorted((ROOT/'archive').glob('*.zip')):
        try:
            with zipfile.ZipFile(z) as zf:
                nm = [n for n in zf.namelist() if n.endswith('outage_90d.csv')]
                if not nm: continue
                asof, d = parse(zf.read(nm[0]).decode('utf-8', 'ignore'))
        except Exception as e:
            say(f'  {z.name}: {e}'); continue
        if d is None: continue
        if asof is None: asof = pd.Timestamp(z.stem)
        if asof.normalize() in have: continue
        d['rep'] = asof.normalize(); new.append(d); have.add(asof.normalize())
    cur = ROOT/'refresh'/'outage_90d.csv'
    if cur.exists():
        asof, d = parse(cur.read_text(encoding='utf-8', errors='ignore'))
        if d is not None:
            asof = (asof or pd.Timestamp.today()).normalize()
            if asof not in have:
                d['rep'] = asof; new.append(d); have.add(asof)
    H = pd.concat([old] + new, ignore_index=True) if new else old
    H['rep'] = pd.to_datetime(H.rep); H['date'] = pd.to_datetime(H.date)
    H = H.drop_duplicates(['rep', 'date', 'fuel'], keep='last').sort_values(['rep', 'date', 'fuel'])
    HIST.parent.mkdir(exist_ok=True); H.to_csv(HIST, index=False)
    say(f'history: {H.rep.nunique()} reports, {H.rep.min():%Y-%m-%d} to {H.rep.max():%Y-%m-%d}'
        f'  (+{len(new)} new)')
    return H


# --------------------------------------------------------------- analysis ---
def wide(H, asof):
    g = H[H.rep == asof].pivot(index='date', columns='fuel', values='mw')
    for f in FUELS:
        if f not in g: g[f] = np.nan
    g['thermal'] = g[THERMAL].sum(axis=1); g['total'] = g[FUELS].sum(axis=1)
    return g.sort_index()


def analyse(H):
    asofs = sorted(H.rep.unique())
    today = asofs[-1]; T = wide(H, today)
    prev = asofs[-2] if len(asofs) > 1 else None
    P = wide(H, prev) if prev is not None else None
    fut = T[T.index > today]
    out = dict(today=today, prev=prev, T=T, P=P, fut=fut)

    # overnight changes, every (date, fuel) that moved
    ch = []
    if P is not None:
        for f in FUELS + ['thermal']:
            j = pd.concat([P[f], T[f]], axis=1, keys=['was', 'now']).dropna()
            j = j[j.index > today]
            d = (j.now - j.was)
            for dt, v in d[d.abs() >= 25].items():
                ch.append(dict(date=dt, fuel=f, was=j.was[dt], now=j.now[dt], delta=v))
    CH = pd.DataFrame(ch)
    out['changes'] = CH.sort_values(['date', 'fuel']) if len(CH) else CH

    # EVENTS. The report has no unit names, but one unit's outage being edited
    # leaves a signature: the same MW change across a run of consecutive dates.
    # Group those runs into blocks, then pair a removed block with an added block
    # of similar size in the same fuel within 21 days either way — that is a
    # move, and the direction says whether it was pulled forward or pushed back.
    ev = []
    if len(CH):
        for f in FUELS:
            c = CH[CH.fuel == f].set_index('date').delta.sort_index()
            if c.empty: continue
            run = [c.index[0]]; val = c.iloc[0]
            for dt in c.index[1:]:
                if (dt - run[-1]).days == 1 and abs(c[dt] - val) <= 15:
                    run.append(dt)
                else:
                    ev.append(dict(fuel=f, start=run[0], end=run[-1], days=len(run), mw=round(val)))
                    run = [dt]; val = c[dt]
            ev.append(dict(fuel=f, start=run[0], end=run[-1], days=len(run), mw=round(val)))
    EV = pd.DataFrame(ev)
    moves = []
    if len(EV):
        EV['used'] = False
        for i, r in EV[EV.mw < -50].iterrows():
            cand = EV[(EV.fuel == r.fuel) & (EV.mw > 50) & (~EV.used) &
                      ((EV.start - r.end).dt.days.abs() <= 21) & (EV.mw.abs().between(abs(r.mw)*0.6, abs(r.mw)*1.6))]
            if len(cand):
                j = cand.index[0]; a = EV.loc[j]
                EV.loc[[i, j], 'used'] = True
                moves.append(dict(fuel=r.fuel, mw=-r.mw, from_start=r.start, from_end=r.end,
                                  to_start=a.start, to_end=a.end,
                                  direction='pulled forward' if a.start < r.start else 'pushed back'))
    out['events'] = EV.sort_values(['fuel', 'start']) if len(EV) else EV
    out['moves'] = pd.DataFrame(moves)

    # revisions per future date: how many reports changed the thermal figure
    rev = {}
    th = H[H.fuel.isin(THERMAL)].groupby(['rep', 'date']).mw.sum().unstack('rep')
    for dt in fut.index:
        if dt in th.index:
            s = th.loc[dt].dropna(); rev[dt] = int((s.diff().abs() >= 25).sum())
    out['rev'] = pd.Series(rev)

    # report vs recorded, by lead — recorded = what the latest report says for past dates
    rec = T[T.index <= today].thermal
    rows = []
    for a in asofs[:-1]:
        W = wide(H, a); f = W[W.index > a].thermal
        for dt, v in f.items():
            if dt in rec.index and dt <= today:
                rows.append(dict(lead=int((dt - a).days), fc=v, act=rec[dt],
                                 persist=float(W[W.index <= a].thermal.iloc[-1]) if (W.index <= a).any() else np.nan))
    out['skill'] = pd.DataFrame(rows)
    return out


# ------------------------------------------------------------------- page ---
CSS = """
:root{color-scheme:light;--bg:#fcfcfb;--panel:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--ink3:#8a8985;
--line:#e4e3df;--s1:#2a78d6;--s2:#eb6834;--up:#e34948;--dn:#008300;--warn:#eda100}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--bg:#1a1a19;--panel:#222221;
--ink:#fff;--ink2:#c3c2b7;--ink3:#8f8e88;--line:#333331;--s1:#3987e5;--s2:#d95926;--up:#e66767;--dn:#3fb950;--warn:#c98500}}
:root[data-theme=dark]{color-scheme:dark;--bg:#1a1a19;--panel:#222221;--ink:#fff;--ink2:#c3c2b7;--ink3:#8f8e88;
--line:#333331;--s1:#3987e5;--s2:#d95926;--up:#e66767;--dn:#3fb950;--warn:#c98500}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Inter,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 16px 60px}h1{font-size:20px;margin:0 0 2px}h2{font-size:14px;margin:26px 0 8px;color:var(--ink2);text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--ink3);font-size:12.5px;margin-bottom:14px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}.tile .l{font-size:11px;color:var(--ink3)}
.tile .v{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}.tile .u{font-size:11px;color:var(--ink2)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:12.5px}th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--ink3);font-weight:500;font-size:11px}td:first-child,th:first-child{text-align:left}.up{color:var(--up)}.dn{color:var(--dn)}.mut{color:var(--ink3)}
.note{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;font-size:13px;color:var(--ink2)}
.note b{color:var(--ink)}svg text{fill:var(--ink2);font-size:11px}.lg{display:flex;gap:16px;font-size:12px;color:var(--ink2);margin:4px 0 0}
.lg i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:5px}
.ttip{position:absolute;pointer-events:none;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;display:none}
"""


def svg_chart(T, P, today):
    """thermal MW out over the next 90 days: today's report vs yesterday's"""
    W, Hh, ml, mb = 1100, 260, 48, 26
    f = T[(T.index > today) & (T.index <= today + pd.Timedelta(days=90))].thermal
    if f.empty: return ''
    g = P[(P.index > today) & (P.index <= today + pd.Timedelta(days=90))].thermal if P is not None else None
    ymax = max(float(f.max()), float(g.max()) if g is not None and len(g) else 0, 500)*1.08
    x = lambda d: ml + (d - f.index[0]).days/max((f.index[-1] - f.index[0]).days, 1)*(W - ml - 10)
    y = lambda v: Hh - mb - v/ymax*(Hh - mb - 12)
    def path(s): return 'M' + ' L'.join(f'{x(d):.1f},{y(v):.1f}' for d, v in s.items())
    ticks = ''.join(f'<line x1="{ml}" x2="{W-10}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="var(--line)"/>'
                    f'<text x="{ml-6}" y="{y(v)+4:.1f}" text-anchor="end">{v:,.0f}</text>'
                    for v in np.linspace(0, ymax/1.08, 5))
    xt = ''.join(f'<text x="{x(d):.1f}" y="{Hh-8}" text-anchor="middle">{d:%b %d}</text>'
                 for d in f.index[::max(1, len(f)//8)])
    prevp = f'<path d="{path(g)}" fill="none" stroke="var(--s2)" stroke-width="2" stroke-dasharray="5 4"/>' if g is not None and len(g) else ''
    pts = ''.join(f'<circle class="pt" cx="{x(d):.1f}" cy="{y(v):.1f}" r="9" fill="transparent" data-d="{d:%a %b %d}" data-v="{v:,.0f}"/>' for d, v in f.items())
    return (f'<div style="position:relative"><svg viewBox="0 0 {W} {Hh}" width="100%" role="img" aria-label="thermal MW unavailable, next 90 days">'
            f'{ticks}{xt}{prevp}<path d="{path(f)}" fill="none" stroke="var(--s1)" stroke-width="2"/>{pts}</svg>'
            f'<div class="ttip" id="tt"></div></div>'
            f'<div class="lg"><span><i style="background:var(--s1)"></i>today\'s report</span>'
            f'<span><i style="background:var(--s2)"></i>yesterday\'s</span></div>'
            '<script>const tt=document.getElementById("tt");document.querySelectorAll(".pt").forEach(c=>{'
            'c.onmousemove=e=>{tt.style.display="block";tt.style.left=(e.offsetX+12)+"px";tt.style.top=(e.offsetY-10)+"px";'
            'tt.innerHTML=c.dataset.d+" &middot; <b>"+c.dataset.v+" MW</b> thermal unavailable"};c.onmouseleave=()=>tt.style.display="none"})</script>')


def page(R):
    T, P, today, fut = R['T'], R['P'], R['today'], R['fut']
    n7 = fut.iloc[:7]; n30 = fut.iloc[:30]
    tod = T[T.index == today].thermal.iloc[0] if (T.index == today).any() else np.nan
    tiles = [('thermal unavailable today', f'{tod:,.0f}', 'MW, recorded'),
             ('next 7 days, average', f'{n7.thermal.mean():,.0f}', 'MW unavailable, planned'),
             ('next 30 days, peak', f'{n30.thermal.max():,.0f}', f'MW on {n30.thermal.idxmax():%b %d}' if len(n30) else ''),
             ('changed overnight', f'{len(R["changes"]) if len(R["changes"]) else 0}', 'date/fuel pairs, ≥25 MW'),
             ('reports on file', f'{R["skill"].lead.nunique() if len(R["skill"]) else 0}+', 'days of history')]
    tl = ''.join(f'<div class="tile"><div class="l">{a}</div><div class="v">{b}</div><div class="u">{c}</div></div>' for a, b, c in tiles)

    # schedule table, next 30 days
    rows = []
    for dt, r in n30.iterrows():
        dlt = ''
        if P is not None and dt in P.index and not np.isnan(P.loc[dt, 'thermal']):
            dv = r.thermal - P.loc[dt, 'thermal']
            dlt = f'<td class="{"up" if dv>0 else "dn" if dv<0 else "mut"}">{dv:+,.0f}</td>' if abs(dv) >= 1 else '<td class="mut">—</td>'
        else: dlt = '<td class="mut">—</td>'
        rv = R['rev'].get(dt, 0)
        rows.append(f'<tr><td>{dt:%a %b %d}</td>' + ''.join(f'<td>{r[f]:,.0f}</td>' for f in THERMAL) +
                    f'<td><b>{r.thermal:,.0f}</b></td>{dlt}<td>{r["Hydro"]:,.0f}</td><td>{r["Wind"]:,.0f}</td><td>{r["Solar"]:,.0f}</td>'
                    f'<td class="{"up" if rv>=4 else "mut"}">{rv}</td></tr>')
    sched = ('<table><tr><th>delivery</th><th>SC</th><th>Cogen</th><th>CC</th><th>GFS</th><th>thermal</th><th>Δ unavailable vs yday</th>'
             '<th>Hydro</th><th>Wind</th><th>Solar</th><th>revisions</th></tr>' + ''.join(rows) + '</table>')

    # changes
    CH = R['changes']
    if len(CH):
        CH = CH[CH.fuel != 'thermal']
        crow = ''.join(f'<tr><td>{r.date:%a %b %d}</td><td style="text-align:left">{r.fuel}</td><td>{r.was:,.0f}</td><td>{r.now:,.0f}</td>'
                       f'<td class="{"up" if r.delta>0 else "dn"}">{r.delta:+,.0f}</td>'
                       f'<td style="text-align:left" class="mut">{"+ MW unavailable" if r.delta>0 else "MW back in service"}</td></tr>'
                       for r in CH.sort_values('delta', key=abs, ascending=False).itertuples())
        changes = f'<table><tr><th>delivery</th><th style="text-align:left">fuel</th><th>was</th><th>now</th><th>Δ MW</th><th style="text-align:left">read</th></tr>{crow}</table>'
    else:
        changes = '<div class="note">No change of 25 MW or more against the previous report.</div>' if P is not None else '<div class="note">First report on file — nothing to compare yet.</div>'

    EV = R['events']; MV = R['moves']; blocks = ''
    if len(MV):
        blocks += ('<h2>Blocks that moved — one outage, re-dated</h2><table><tr><th style="text-align:left">fuel</th><th>MW</th>'
                   '<th>was scheduled</th><th>now scheduled</th><th style="text-align:left">read</th></tr>' +
                   ''.join(f'<tr><td style="text-align:left">{r.fuel}</td><td>{r.mw:,.0f}</td>'
                           f'<td>{r.from_start:%b %d} – {r.from_end:%b %d}</td><td>{r.to_start:%b %d} – {r.to_end:%b %d}</td>'
                           f'<td style="text-align:left" class="{"dn" if r.direction=="pulled forward" else "up"}">{r.direction}</td></tr>'
                           for r in MV.itertuples()) + '</table>')
    if len(EV):
        rest = EV[~EV.used] if 'used' in EV else EV
        if len(rest):
            blocks += ('<h2>Other blocks that changed</h2><table><tr><th style="text-align:left">fuel</th><th>dates</th><th>days</th><th>MW</th><th style="text-align:left">read</th></tr>' +
                       ''.join(f'<tr><td style="text-align:left">{r.fuel}</td><td>{r.start:%b %d} – {r.end:%b %d}</td><td>{r.days}</td>'
                               f'<td class="{"up" if r.mw>0 else "dn"}">{r.mw:+,.0f}</td>'
                               f'<td style="text-align:left" class="mut">{"more MW unavailable — new outage, or one extended" if r.mw>0 else "MW back in service — outage cancelled, shortened, or moved elsewhere"}</td></tr>'
                               for r in rest.itertuples()) + '</table>')
    slips = blocks

    # skill
    K = R['skill']; skill = ''
    if len(K) >= 10:
        rr = ''.join(f'<tr><td>{L}</td><td>{len(g)}</td><td>{(g.fc-g.act).abs().mean():,.0f}</td><td>{(g.fc-g.act).mean():+,.0f}</td>'
                     f'<td>{(g.persist-g.act).abs().mean():,.0f}</td></tr>'
                     for L, g in K[K.lead <= 14].groupby('lead') if len(g) >= 3)
        skill = ('<h2>How good has the plan been? report at lead L vs what was recorded (thermal MW unavailable)</h2>'
                 '<table><tr><th>lead</th><th>n</th><th>report MAE</th><th>report bias</th><th>just-carry-today MAE</th></tr>' + rr + '</table>'
                 '<div class="sub">Bias negative = the plan ran light (forced outages are not in it, planned ones slip). '
                 'This table fills in as the archive grows; treat anything under 30 per lead as indicative.</div>')

    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Outage Schedule</title><style>{CSS}</style></head><body><div class="wrap">
<h1>Outage schedule — AESO 90-day report</h1>
<div class="sub">report of {today:%A %d %B %Y}{f', compared with {P.index.min():%b %d}' if False else ''}{f', compared with the report of {R["prev"]:%d %b}' if R['prev'] is not None else ''} · every number is MW <b>unavailable</b> (out of service) by fuel type · up to today recorded, after today the plan</div>
<div class="tiles">{tl}</div>
<h2>Thermal MW unavailable, next 90 days</h2>{svg_chart(T, P, today)}
<h2>What moved overnight — every date and fuel that changed by 25 MW or more</h2>{changes}
{slips}
<h2>Next 30 days</h2>{sched}
{skill}
<h2>Read this once</h2><div class="note">
<b>Sign convention: every figure is MW unavailable.</b> A positive change means more capacity is out, a negative change means capacity came back. <b>There are no unit names because AESO does not publish them.</b> The public report is MW out per fuel type per day. A start date, an end date and a pushback for a specific unit are not in any public feed; the asset monitor infers which unit is off from live output against nameplate, and that is a live-only picture. What this page can do is show the fuel-level plan, show every overnight change to it — which is the only public trace of an extension or a slip — and count how many times each date has been re-cut, so a number revised six times is read as a guess.<br><br>
<b>The plan runs light.</b> Forced outages are not in it and planned ones slip late more often than early. The multi-day model does not add these numbers to the cushion raw; it carries the report as a feature and lets a walk-forward regression decide how much of it to believe at each lead, which it can only do once this history is a couple of months deep.</div>
</div></body></html>"""
    return html


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--no-open', action='store_true'); a = ap.parse_args()
    H = ingest()
    if H.rep.nunique() == 0: sys.exit('no outage reports found')
    R = analyse(H)
    out = ROOT/'docs'/'outages.html'; out.parent.mkdir(exist_ok=True)
    out.write_text(page(R), encoding='utf-8')
    CH = R['changes']
    say(f"report of {R['today']:%Y-%m-%d}: {len(CH) if len(CH) else 0} changes ≥25 MW overnight, "
        f"{len(R['moves'])} blocks moved")
    if len(CH):
        big = CH[CH.fuel != 'thermal'].sort_values('delta', key=abs, ascending=False).head(6)
        for r in big.itertuples():
            say(f"   {r.date:%a %b %d}  {r.fuel:<18} {r.was:>6,.0f} -> {r.now:>6,.0f}  ({r.delta:+,.0f})")
    say(f'wrote {out}')
    if not a.no_open:
        try: webbrowser.open(out.as_uri())
        except Exception: pass


if __name__ == '__main__':
    main()
