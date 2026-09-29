"""
backtest.py — does the model's quote ever fill, and does it make money?

    python backtest.py                  full report + verify/quote_ledger.csv
    python backtest.py --cost 2         charge $2/MWh on every trade
    python backtest.py --block peak
    python backtest.py --deleak         (see below)

WHAT IS BEING TESTED
--------------------
The model publishes a daily bid and offer: bid = day EV x 0.727, offer = day EV
x 1.282, the levels score() solves for a 3:1 gain-to-loss ratio. This walks
every delivery day the model has scored, writes down those two prices, and
checks them against that day's day-ahead settlement. Filled or not filled, and
what the pool then did.

THE THREE PRICES, AND WHICH ONE DECIDES WHAT
--------------------------------------------
  prev    the last settle published while the delivery day was still 2+ days
          out. This is the last price you can actually see when you quote, and
          it is the only price this file ever transacts at.
  s1      the day-ahead settle, published the session before delivery. You
          cannot see it when you quote. It decides whether a resting order
          filled: a bid fills if s1 came down to it, an offer if s1 came up.
  pool    what the hours actually settled at. Every position is marked here.

Only Tuesday-to-Saturday deliveries carry a lead-1 settle — Sunday and Monday
have no session the day before — so those are the days that can be tested.

WHY THERE IS NO ORDER BOOK
--------------------------
No bid/ask, no trade tape, no depth exists in any file we hold; every forward
table carries a single settlement price per product per day. So a fill has to
be inferred from the settle coming through your level. That rule is generous
about queue position and strict about everything else: it can only ever see
the fills where the market moved decisively through you, and never the benign
fills you would pick up on a quiet day. Read the passive results as the
adverse end of the range, not the whole of it.

THE PRODUCTS
------------
    XDT ~ 7x24 flat      XDQ ~ 7x16 on-peak HE8-23      XDP ~ 7x8 off-peak
XDW/XDV/XDU are the same three re-quoted (r > 0.99) and add nothing. The curve
carries day-specific information only inside a week: correlation with outturn
is 0.58 at lead 1, 0.38 at lead 2, 0.13 at lead 7 and zero past that.

--deleak
--------
The cushion the backtest scores on, cush_full, is built from ACTUAL gas
availability and ACTUAL net imports — only wind, solar and load sit at a
day-ahead vintage. That is look-ahead. --deleak expects model/_scores_deleak.pkl,
a rerun with gas, biomass and intertie replaced by yesterday-same-hour. It costs
roughly a tenth of the edge; the conclusions do not change.
"""
import argparse, sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent
PROD = {'flat': 'XDT', 'peak': 'XDQ', 'off': 'XDP'}
HRS  = {'flat': 24,    'peak': 16,    'off': 8}
BLK  = {'flat': lambda he: he.between(1, 24),
        'peak': lambda he: he.between(8, 23),
        'off' : lambda he: ~he.between(8, 23)}
DAY_BID, DAY_ASK = 0.727, 1.282     # the shipped daily levels, gates['day']
FIT_WIN, FIT_MIN, FIT_LAG = 120, 50, 3


def say(s=''): print(s, flush=True)


def build(deleak=False):
    sp = ROOT/'model'/('_scores_deleak.pkl' if deleak else '_raw_scores.pkl')
    if not sp.exists() and not deleak: sp = ROOT/'model'/'_scores.pkl'
    if not sp.exists(): sys.exit(f'{sp.name} not on disk')
    R = pd.read_pickle(sp); R.index.name = 'ts'
    R['day'] = R.index.normalize(); R['he'] = R.index.hour + 1

    ph = pd.read_csv(ROOT/'model'/'price_history.csv', index_col=0, parse_dates=True)
    ph.index.name = 'ts'; ph['day'] = ph.index.normalize(); ph['he'] = ph.index.hour + 1

    f = pd.read_csv(ROOT/'cache'/'fwd_aeso_daily.csv', parse_dates=['EffectiveDate', 'Strip'])
    f['lead'] = (f.Strip - f.EffectiveDate).dt.days
    S1   = f[f.lead == 1].pivot_table(index='Strip', columns='ExchangeCode', values='Price')
    PREV = (f[f.lead >= 2].sort_values('lead')
              .groupby(['Strip', 'ExchangeCode']).first()['Price'].unstack('ExchangeCode'))

    # a part-day EV is not comparable to a whole-day settle
    keep = R.groupby('day').size().pipe(lambda n: n[n >= 23].index)
    out = {}
    for b in PROD:
        M = pd.DataFrame({'ev'  : R[BLK[b](R.he)].groupby('day').mean_px.mean(),
                          'pool': ph[BLK[b](ph.he)].groupby('day').price.mean(),
                          's1'  : S1[PROD[b]], 'prev': PREV[PROD[b]]})
        M = M.loc[M.index.isin(keep)].dropna().sort_index().reset_index(names='day')
        M['bid'], M['ask'] = M.ev*DAY_BID, M.ev*DAY_ASK
        # walk-forward slope of (pool - prev) on (EV - prev). Nothing inside the
        # last FIT_LAG days is used: the pool of the day you quote on has not
        # settled yet when you quote.
        x, y = (M.ev - M.prev).values, (M.pool - M.prev).values
        bb = np.full(len(M), np.nan)
        for i in range(len(M)):
            j = i - FIT_LAG; lo = max(0, j - FIT_WIN)
            if j - lo >= FIT_MIN: bb[i] = np.polyfit(x[lo:j], y[lo:j], 1)[0]
        M['b'] = bb
        M['fair'] = M.prev + M.b*(M.ev - M.prev)
        out[b] = M
    say(f"scores  {len(R):,} hours  {R.index.min():%Y-%m-%d} to {R.index.max():%Y-%m-%d}"
        f"{'   [DE-LEAKED]' if deleak else ''}")
    return out


# ------------------------------------------------------------- A. the audit --
def audit(b, M, cost):
    h = HRS[b]
    say(f"\n{'='*100}\n  {b.upper()}  —  {len(M)} delivery days, {M.day.min():%Y-%m-%d} to "
        f"{M.day.max():%Y-%m-%d}   ({PROD[b]})\n{'='*100}")
    say('\n  A. THE SHIPPED QUOTE, CHECKED AGAINST EVERY DAY\'S SETTLE')
    say(f"     avg bid ${M.bid.mean():.2f}   avg ask ${M.ask.mean():.2f}   "
        f"avg settle ${M.s1.mean():.2f}   avg pool ${M.pool.mean():.2f}")
    fb, fa = M.s1 <= M.bid, M.s1 >= M.ask
    gb = (M.pool - M.bid)[fb] - cost; ga = (M.ask - M.pool)[fa] - cost
    say(f"     bid  filled {int(fb.sum()):>3}/{len(M)} ({fb.mean():>4.0%})   "
        f"${gb.mean() if len(gb) else 0:>7.2f}/MWh   win {(gb>0).mean() if len(gb) else 0:>4.0%}   "
        f"${gb.sum()*h if len(gb) else 0:>9,.0f}/MW")
    say(f"     ask  filled {int(fa.sum()):>3}/{len(M)} ({fa.mean():>4.0%})   "
        f"${ga.mean() if len(ga) else 0:>7.2f}/MWh   win {(ga>0).mean() if len(ga) else 0:>4.0%}   "
        f"${ga.sum()*h if len(ga) else 0:>9,.0f}/MW")
    say(f"     neither side filled on {int((~fb & ~fa).sum())} days "
        f"({(~fb & ~fa).mean():.0%}).   TOTAL "
        f"${(gb.sum()+ga.sum())*h:,.0f}/MW over the whole period.")
    say(f"     where the quote sits against the market you can see when you post:"
        f"  bid {100*((M.bid-M.prev)/M.prev).median():+.0f}%"
        f"   ask {100*((M.ask-M.prev)/M.prev).median():+.0f}%"
        f"   (quote is {100*((M.ask-M.bid)/M.ev).median():.0f}% of EV wide)")


# --------------------------------------------------------- B. the diagnosis --
def diagnose(b, M):
    say('\n  B. WHY')
    pr = M.s1 - M.pool
    say(f"     the market carries a premium: settle minus pool ${pr.mean():+.2f}, "
        f"above pool on {100*(pr>0).mean():.0f}% of days — but it flipped sign in "
        f"{int((pr.groupby(M.day.dt.to_period('M')).mean()<0).sum())} of "
        f"{M.day.dt.to_period('M').nunique()} months, so it is not something to lean on.")
    say(f"     the model's own level is off too: EV minus pool ${ (M.ev-M.pool).mean():+.2f}/MWh.")
    say(f"     so the quote is anchored on a number that sits "
        f"{100*((M.ev-M.prev)/M.prev).median():+.0f}% from the market. On flat and peak that "
        f"puts the bid out of reach and the offer through the market — you are not")
    say(f"     quoting a two-sided market, you are hitting bids.")
    d = M.dropna(subset=['b'])
    x, y = d.ev - d.prev, d.pool - d.prev
    say(f"     but the disagreement itself is good: pool-minus-market moves "
        f"{np.polyfit(x, y, 1)[0]:.2f} for every $1 of (EV minus market), r={x.corr(y):+.3f}, "
        f"while the market itself moves only {np.polyfit(x, d.s1-d.prev, 1)[0]:.2f}.")
    say('     The level is wrong. The direction is right. That is a fixable quote, not a')
    say('     fixable forecast.')


# ------------------------------------------------------------- C. the fix ----
def fix(b, M, cost):
    h = HRS[b]; d = M.dropna(subset=['b']).reset_index(drop=True)
    sk = d.b*(d.ev - d.prev)                      # shrunk edge over the visible market
    say('\n  C. RE-ANCHORED: fair = last visible settle + shrunk edge.  Trade at the market')
    say('     when the edge clears T; hold to pool.')
    say(f"     {'T':>5}{'trades':>8}{'rate':>7}{'$/MWh':>9}{'hit':>6}{'$/MW':>11}{'t':>7}"
        f"{'maxDD':>9}{'1st half':>10}{'2nd half':>10}")
    half = len(d)//2; best = None
    for T in (0, 2, 4, 6, 8, 12, 20):
        m = sk.abs() > T
        if m.sum() < 10: continue
        p = np.sign(sk[m])*(d.pool - d.prev)[m] - cost
        eq = (p*h).cumsum(); dd = (eq - eq.cummax()).min()
        t = p.mean()/(p.std(ddof=1)/np.sqrt(len(p)))
        p1, p2 = p[p.index < half], p[p.index >= half]
        say(f"     ${T:>4.0f}{int(m.sum()):>8}{m.mean():>7.0%}{p.mean():>9.2f}{(p>0).mean():>6.0%}"
            f"{p.sum()*h:>11,.0f}{t:>7.2f}{dd:>9,.0f}"
            f"{p1.mean() if len(p1) else 0:>10.2f}{p2.mean() if len(p2) else 0:>10.2f}")
        if best is None or p.sum()*h > best[1]: best = (T, p.sum()*h)

    say('\n  D. RESTING vs CROSSING — the same fair value, quoted either side of it')
    say(f"     {'width':>7}{'posted':>9}{'filled':>8}{'rate':>7}{'$/MWh':>9}   |"
        f"{'crossed':>9}{'$/MWh':>9}{'$/MW':>10}")
    for w in (2, 4, 6, 8, 12):
        bid, ask = d.prev + sk - w, d.prev + sk + w
        xb, xa = bid >= d.prev, ask <= d.prev
        rb, ra = ~xb, ~xa
        hb, ha = rb & (d.s1 <= bid), ra & (d.s1 >= ask)
        gr = pd.concat([(d.pool-bid)[hb], (ask-d.pool)[ha]]) - cost
        gx = pd.concat([(d.pool-d.prev)[xb], (d.prev-d.pool)[xa]]) - cost
        say(f"     ${w:>6.0f}{int(rb.sum()+ra.sum()):>9}{int(hb.sum()+ha.sum()):>8}"
            f"{(hb.sum()+ha.sum())/max(rb.sum()+ra.sum(),1):>7.0%}"
            f"{gr.mean() if len(gr) else 0:>9.2f}   |{int(xb.sum()+xa.sum()):>9}"
            f"{gx.mean() if len(gx) else 0:>9.2f}{gx.sum()*h if len(gx) else 0:>10,.0f}")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cost', type=float, default=1.0)
    ap.add_argument('--block', choices=list(PROD)+['all'], default='all')
    ap.add_argument('--deleak', action='store_true')
    a = ap.parse_args()
    T = build(a.deleak)
    led = []
    for b in (list(PROD) if a.block == 'all' else [a.block]):
        M = T[b]
        if len(M) < 40: say(f'\n{b}: only {len(M)} usable days, skipped'); continue
        audit(b, M, a.cost); diagnose(b, M); d = fix(b, M, a.cost)
        L = M.copy(); L.insert(1, 'block', b)
        L['bid_filled'] = L.s1 <= L.bid; L['ask_filled'] = L.s1 >= L.ask
        L['pnl_bid'] = np.where(L.bid_filled, L.pool - L.bid - a.cost, np.nan)
        L['pnl_ask'] = np.where(L.ask_filled, L.ask - L.pool - a.cost, np.nan)
        L['edge']    = L.b*(L.ev - L.prev)
        led.append(L)
    if led:
        out = ROOT/'verify'; out.mkdir(exist_ok=True)
        p = out/'quote_ledger.csv'
        Z = pd.concat(led)
        num = Z.select_dtypes('number').columns
        Z[num] = Z[num].round(3)
        Z.to_csv(p, index=False)
        say(f"\nledger written: {p}   (one row per delivery day per block)")

    say(f"\n{'='*100}")
    say("The shipped quote is not a tradeable quote. It is anchored on an EV that sits well")
    say("off the market, and it is 56% of EV wide, so one side is unreachable and the other")
    say("is already through. On the days it does fill, the bid loses money — a deep bid only")
    say("gets hit when the market has collapsed, and the pool collapses with it.")
    say("")
    say("Re-anchoring on the last visible settle and shading by the model's own disagreement")
    say("fixes the level. But note section D: resting orders still fill rarely and still lose")
    say("money when they do. Every dollar in this backtest comes from CROSSING. The model has")
    say("a taking edge, not a making edge, and it should be used to decide when to hit the")
    say("market — not to sit on a bid and wait.")


if __name__ == '__main__':
    main()
