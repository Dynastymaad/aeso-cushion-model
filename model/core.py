"""
core.py — the model itself: level curve, residual pools, simulation, calibration.

Shared by the rebuild so there is exactly one implementation of each idea.
"""
import numpy as np, pandas as pd

E    = np.array([-1e9,0,200,400,600,800,1000,1250,1500,1800,2100,2500,3000,1e9])
NB   = len(E)-1
KMIN = 60
THR  = [50,100,300,700]


# ---------------------------------------------------------------- curve ----
def knots(cush, price):
    """Twenty cushion buckets; the mean price in each, with the single highest
    hour trimmed. A seven-day window holds about eight hours per bucket, so one
    $716 print would otherwise drag the whole tight end. The tail is not lost -
    it lives in the residual pools, where there is a year of data to shape it."""
    pts=[]
    for k in range(20):
        lo,hi=np.percentile(cush,k*5),np.percentile(cush,(k+1)*5)
        m=(cush>=lo)&(cush<=hi)
        if m.sum()<3: continue
        s=np.sort(price[m])
        if len(s)>=5: s=np.concatenate([s[:-1],[s[-2]]])
        pts.append(((lo+hi)/2,s.mean()))
    pts=sorted(pts)
    return np.array([p[0] for p in pts]), np.array([p[1] for p in pts])


def curve(xs, ys, v):
    v=np.atleast_1d(np.asarray(v,float)); o=np.interp(v,xs,ys)
    lo,hi=v<xs[0],v>xs[-1]
    if lo.any(): o[lo]=ys[0] +(ys[1]-ys[0])/(xs[1]-xs[0])   *(v[lo]-xs[0])
    if hi.any(): o[hi]=ys[-1]+(ys[-1]-ys[-2])/(xs[-1]-xs[-2])*(v[hi]-xs[-1])
    return np.clip(o,1.0,999.99)


def walk_forward_ref(d, win=7):
    """The level each day was judged against AT THE TIME. Using today's curve on
    a year of history would make a generally expensive week look like a week of
    spikes."""
    d=d.copy(); d['day']=d.index.normalize()
    days=sorted(d.day.unique()); out=pd.Series(index=d.index,dtype=float)
    for i,dy in enumerate(days):
        if i<win: continue
        tr=d[(d.day>=days[i-win])&(d.day<dy)]; te=d[d.day==dy]
        if len(tr)<120 or len(te)==0: continue
        xs,ys=knots(tr.cush.values,tr.price.values)
        out.loc[te.index]=curve(xs,ys,te.cush.values)
    return out


# ----------------------------------------------------------------- pools ----
def build_pools(bins, grps, vals):
    """Thin cells borrow from adjacent CUSHION levels in the same time block.
    Time of day matters more than cushion level does - at the same cushion,
    walking 600 MW up the stack costs $75 overnight and $446 in the evening -
    so the time block is the wrong thing to relax."""
    pool={}
    for b in range(NB):
        hb=vals[bins==b]
        for g in range(5):
            a=vals[(bins==b)&(grps==g)]
            if len(a)>=KMIN: pool[(b,g)]=a; continue
            for step in range(1,NB):
                for nb in (b-step,b+step):
                    if 0<=nb<NB: a=np.concatenate([a,vals[(bins==nb)&(grps==g)]])
                if len(a)>=KMIN: break
            if len(a)<KMIN: a=np.concatenate([a,hb])
            if len(a)<KMIN: a=np.concatenate([a,vals])
            pool[(b,g)]=a
    return pool


# ----------------------------------------------------------- calibration ----
class Cal:
    """Isotonic, with three corrections. Extends rather than clips past the
    fitted range (flat clipping let P(>$300) exceed P(>$100)); merges thin
    blocks to at least NMIN hours and reads each as (hits+1/2)/(n+1), so six
    spikes out of six is 93% not 100%; and never states more than CAP."""
    NMIN=20; CAP=0.95
    def __init__(self, raw, hit):
        from sklearn.isotonic import IsotonicRegression
        raw=np.asarray(raw,float); hit=np.asarray(hit,float)
        iso=IsotonicRegression(y_min=0,y_max=1,out_of_bounds='clip').fit(raw,hit)
        f=iso.predict(raw)
        lv=np.unique(f); groups=[]; cur=[]
        for v in lv:
            cur.append(v)
            if np.isin(f,cur).sum()>=self.NMIN: groups.append(cur); cur=[]
        if cur: groups[-1]+=cur if groups else None
        if not groups: groups=[list(lv)]
        adj=np.empty_like(f)
        for gp in groups:
            m=np.isin(f,gp); adj[m]=(hit[m].sum()+0.5)/(m.sum()+1.0)
        self.iso=IsotonicRegression(y_min=0,y_max=1,out_of_bounds='clip').fit(raw,adj)
        self.xmax=float(raw.max()); self.ymax=float(self.iso.predict([self.xmax])[0])
    def __call__(self, v):
        v=np.atleast_1d(np.asarray(v,float))
        o=self.iso.predict(np.clip(v,None,self.xmax))
        hi=v>self.xmax
        if hi.any():
            span=max(1.0-self.xmax,1e-9)
            o[hi]=self.ymax+(self.CAP-self.ymax)*(v[hi]-self.xmax)/span
        return np.clip(o,0,self.CAP)


def hot_ratio_curve(d):
    """Among tight after-dark hours that cleared $100, how many went on to clear
    $300? It runs from about 76% at a cushion near zero to 32% by 1,400, so the
    deep tail cannot simply be pinned to P(>$100)."""
    ev=d[d.g.isin([3,4])]
    pts=[]
    for lo,hi in [(-2000,200),(200,400),(400,600),(600,900),(900,1400)]:
        s=ev[(ev.cush>=lo)&(ev.cush<hi)]; a=s[s.price>100]
        if len(a)<20: continue
        pts.append([int((lo+hi)/2 if lo>-2000 else 0), round(float((a.price>300).mean()),3)])
    return pts or [[0,0.7],[1150,0.32]]
