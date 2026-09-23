"""
outage_delta.py -- what changed in the supply picture, day by day.

Answers one question: since yesterday's view, how much generation has come off
or come back for each day of the week? A -50 MW on Friday is a new outage; a
+50 is one that cleared or a return that got pulled forward.

    python outage_delta.py                 today vs the last archived vintage
    python outage_delta.py --days 10       longer horizon (max ~22)
    python outage_delta.py --vs 2026-09-20 compare against a specific vintage
    python outage_delta.py --min 25        only call out moves above 25 MW

Reads gencap.json - the AESO capability feed the cushion model already pulls -
from refresh/ for the live view and from archive/*.zip for every past vintage.
AC is available capability, already net of outages, so a fall in AC IS the
outage. MBO OUT and OP OUT are shown alongside so you can see which kind.

Sign convention throughout: negative = supply lost = tighter.
"""
import argparse, json, re, zipfile
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
# wind and solar capability moves with the forecast, not with outages - keeping
# them in would drown a real 50 MW trip in a wind revision
THERMAL = {'COGENERATION', 'COMBINED_CYCLE', 'GAS_FIRED_STEAM', 'SIMPLE_CYCLE',
           'HYDRO', 'OTHER', 'ENERGY STORAGE'}


def parse(raw):
    """gencap payload -> tidy hourly rows"""
    d = json.loads(raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw)
    out = []
    for g in d.get('return', []):
        sub = g.get('sub_fuel_type') or g.get('fuel_type')
        for h in g.get('Hours', []):
            o = h.get('outage_grouping', {})
            out.append({'hr': h.get('begin_datetime_mpt'), 'sub': sub,
                        'ac': o.get('AC'), 'mc': o.get('MC'),
                        'op_out': o.get('OP OUT'), 'mbo_out': o.get('MBO OUT')})
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df['hr'] = pd.to_datetime(df.hr, errors='coerce')
    df = df.dropna(subset=['hr'])
    for c in ('ac', 'mc', 'op_out', 'mbo_out'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df[df['sub'].isin(THERMAL)]


def vintages(root):
    """every gencap snapshot we hold, newest last"""
    v = {}
    for z in sorted((root / 'archive').glob('*.zip')):
        m = re.search(r'(\d{4}-\d{2}-\d{2})', z.name)
        if not m:
            continue
        try:
            with zipfile.ZipFile(z) as zf:
                name = next((n for n in zf.namelist() if n.endswith('gencap.json')), None)
                if name:
                    v[m.group(1)] = parse(zf.read(name))
        except Exception as e:
            print(f'  skipped {z.name} ({e})')
    live = root / 'refresh' / 'gencap.json'
    if live.exists():
        v['live'] = parse(live.read_bytes())
    return {k: d for k, d in v.items() if not d.empty}


def daily(df):
    """hourly capability -> one row per target day"""
    g = df.copy()
    g['day'] = g.hr.dt.normalize()
    # sum across fuels within an hour, then take the day's mean across hours
    per_hr = g.groupby(['day', 'hr'])[['ac', 'op_out', 'mbo_out']].sum()
    return per_hr.groupby('day').mean()


def by_fuel(df):
    g = df.copy()
    g['day'] = g.hr.dt.normalize()
    return g.groupby(['day', 'sub']).ac.mean()


def main(a):
    root = Path(a.model).expanduser().resolve()
    V = vintages(root)
    if len(V) < 2:
        raise SystemExit(f'need at least two gencap vintages, found {len(V)} in '
                         f'{root}/archive and refresh. Let the daily archive build up.')
    keys = list(V)
    cur = keys[-1]
    if a.vs:
        base = a.vs if a.vs in V else None
    else:
        # the live feed and today's archive are written from the same pull, so
        # comparing them shows nothing. default to the newest vintage from a
        # previous day - that is the "since yesterday" the question is asking.
        today_s = pd.Timestamp.now().strftime('%Y-%m-%d')
        prior = [k for k in keys if k not in (cur, 'live') and k < today_s]
        base = prior[-1] if prior else (keys[-2] if len(keys) > 1 else None)
    if base is None:
        raise SystemExit(f'vintage {a.vs} not held. have: {", ".join(keys)}')

    D = {k: daily(v) for k, v in V.items()}
    today = pd.Timestamp.now().normalize()
    days = [d for d in D[cur].index if today <= d < today + pd.Timedelta(days=a.days)]

    print(f'\nvintages held: {", ".join(keys)}')
    print(f'comparing  {cur}  against  {base}')
    print(f'sign: negative = capability lost since {base} = new outage\n')

    rows = []
    for d in days:
        now = D[cur].loc[d] if d in D[cur].index else None
        was = D[base].loc[d] if d in D[base].index else None
        if now is None or was is None:
            continue
        r = {'day': d.strftime('%a %d %b'),
             'ac_now': round(now.ac), 'ac_was': round(was.ac),
             'd_supply': round(now.ac - was.ac),
             'd_planned': round(now.mbo_out - was.mbo_out),
             'd_forced': round(now.op_out - was.op_out),
             'out_now': round(now.op_out + now.mbo_out)}
        # every vintage, so a creeping change is visible rather than a single step
        for k in keys[:-1]:
            r[k] = (round(D[k].loc[d].ac - now.ac) * -1
                    if d in D[k].index else None)
        rows.append(r)

    t = pd.DataFrame(rows)
    if t.empty:
        raise SystemExit('no overlapping forward days between the two vintages.')
    pd.set_option('display.width', 200)
    show = ['day', 'ac_now', 'ac_was', 'd_supply', 'd_planned', 'd_forced', 'out_now']
    print(t[show].to_string(index=False))
    print('\n  ac_now/ac_was  mean available capability that day, MW (thermal+hydro+storage)')
    print('  d_supply       change since the comparison vintage  <- the number you asked for')
    print('  d_planned      of which planned (MBO) outage        d_forced = operational (OP)')
    print('  out_now        total MW on outage that day')

    older = [k for k in keys[:-1] if k in t.columns and t[k].notna().any()]
    if len(older) > 1:
        print(f'\n  cumulative change into {cur} from each earlier vintage:')
        print(t[['day'] + older].to_string(index=False))

    big = t[t.d_supply.abs() >= a.min]
    print('\n' + '-' * 78)
    if not len(big):
        print(f'  Nothing moved more than {a.min} MW on any day. Supply picture unchanged.')
    else:
        for _, r in big.iterrows():
            kind = ('planned' if abs(r.d_planned) > abs(r.d_forced) else 'forced')
            print(f'  {r.day}: {r.d_supply:+,.0f} MW '
                  f'({"lost" if r.d_supply < 0 else "returned"}, mostly {kind})')
        w = big.loc[big.d_supply.abs().idxmax()]
        print(f'\n  biggest mover: {w.day} at {w.d_supply:+,.0f} MW')

    # which fuel moved, for the days that moved
    if len(big):
        fn, fw = by_fuel(V[cur]), by_fuel(V[base])
        print('\n  what moved, by fuel (MW):')
        for _, r in big.iterrows():
            d = pd.Timestamp(pd.to_datetime(r.day + f' {pd.Timestamp.now().year}'))
            d = next((x for x in days if x.strftime('%a %d %b') == r.day), None)
            if d is None:
                continue
            try:
                delta = (fn.loc[d] - fw.loc[d]).round(0)
            except KeyError:
                continue
            delta = delta[delta.abs() >= 5].sort_values()
            if len(delta):
                print(f'    {r.day}: ' + ', '.join(f'{k} {v:+,.0f}' for k, v in delta.items()))
    print('-' * 78)

    out = ROOT / 'outage_delta.csv'
    t.to_csv(out, index=False)
    print(f'  wrote {out}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default=str(Path(__file__).resolve().parent))
    ap.add_argument('--days', type=int, default=8)
    ap.add_argument('--vs', default=None, help='vintage date, e.g. 2026-09-20')
    ap.add_argument('--min', type=float, default=25, help='MW worth mentioning')
    main(ap.parse_args())
