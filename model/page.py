"""page.py — turn the rebuilt model into docs/index.html."""
import json, numpy as np, pandas as pd
from pathlib import Path
OTHER_MW=1000.0

def _feeds(folder):
    f=Path(folder)
    need={'gencap.json':'generation capability by fuel type',
          'load.json':'Alberta internal load, actual and forecast',
          'poolprice.json':'settled pool price',
          'wind_fc.csv':'AESO wind forecast (ets.aeso.ca)',
          'solar_fc.csv':'AESO solar forecast (ets.aeso.ca)'}
    miss=[(n,d) for n,d in need.items() if not (f/n).exists()]
    if miss:
        lines=[f"    {n:<16} {d}" for n,d in miss]
        raise SystemExit(
            "\nStage 6 cannot build the page - these feeds are missing from "
            f"{f}:\n" + "\n".join(lines) +
            "\n\n  They failed during stage 2. The model itself is fine; there is\n"
            "  simply no forward view without them. Options:\n"
            "    - run again, the failure may be transient\n"
            "    - fetch them by hand into that folder:\n"
            "        $u='https://ets.aeso.ca/Market/Reports/Manual/Operations/prodweb_reports/wind_solar_forecast'\n"
            "        iwr \"$u/wind_rpt_longterm.csv\"  -OutFile refresh\\wind_fc.csv  -UseBasicParsing\n"
            "        iwr \"$u/solar_rpt_longterm.csv\" -OutFile refresh\\solar_fc.csv -UseBasicParsing\n"
            "      then:  python update.py --skip-score")
    def j(n): return json.loads((f/f'{n}.json').read_text(encoding='utf-8-sig'))
    G=j('gencap')['return']
    MAP={'COGENERATION':'cogen','COMBINED_CYCLE':'cc','GAS_FIRED_STEAM':'gfs',
         'SIMPLE_CYCLE':'sc','OTHER':'bio','HYDRO':'hydro','ENERGY STORAGE':'stor'}
    rows={}
    for blk in G:
        c=MAP.get(blk['sub_fuel_type'])
        if not c: continue
        for h in blk['Hours']:
            rows.setdefault(pd.Timestamp(h['begin_datetime_mpt']),{})[c]=h['outage_grouping'].get('AC')
    gc=pd.DataFrame.from_dict(rows,orient='index').sort_index()
    L=j('load')['return']['Actual Forecast Report']
    ld=pd.DataFrame([{'t':pd.Timestamp(r['begin_datetime_mpt']),
        'ail_act':pd.to_numeric(r.get('alberta_internal_load'),errors='coerce'),
        'ail_fc':pd.to_numeric(r.get('forecast_alberta_internal_load'),errors='coerce')}
        for r in L]).set_index('t').sort_index()
    def ws(n,p):
        x=pd.read_csv(f/f'{n}.csv'); x['t']=pd.to_datetime(x['Forecast Transaction Date'])
        x=x.set_index('t').sort_index()
        return pd.DataFrame({f'{p}_ml':pd.to_numeric(x['Most Likely'],errors='coerce'),
                             f'{p}_lo':pd.to_numeric(x['Min'],errors='coerce'),
                             f'{p}_hi':pd.to_numeric(x['Max'],errors='coerce'),
                             f'{p}_a':pd.to_numeric(x['Actual'],errors='coerce')})
    W,S=ws('wind_fc','wind'),ws('solar_fc','solar')
    P=j('poolprice')['return']['Pool Price Report']
    pp=pd.DataFrame([{'t':pd.Timestamp(r['begin_datetime_mpt']),
        'price':pd.to_numeric(r.get('pool_price'),errors='coerce')} for r in P]).set_index('t').sort_index()
    pp=pp[~pp.index.duplicated(keep='last')]
    atc=None
    if (f/'intertie.json').exists():
        try:
            I=json.loads((f/'intertie.json').read_text(encoding='utf-8-sig'))['return']
            ri={}
            for g,al in I.items():
                if not isinstance(al,dict) or 'Allocations' not in al: continue
                if g in ('BcMatlFlowgate','SystemlFlowgate'): continue
                for a in al['Allocations']:
                    t=pd.Timestamp(a['date'])+pd.Timedelta(hours=int(a['he'])-1)
                    r=ri.setdefault(t,{'imp':0.0,'exp':0.0})
                    if isinstance(a.get('import'),dict): r['imp']+=float(a['import'].get('atc') or 0)
                    if isinstance(a.get('export'),dict): r['exp']+=float(a['export'].get('atc') or 0)
            if ri: atc=pd.DataFrame.from_dict(ri,orient='index').sort_index()
        except Exception: pass
    x=gc.join([ld,W,S,pp],how='outer')
    x['wind']=x.wind_a.fillna(x.wind_ml); x['solar']=x.solar_a.fillna(x.solar_ml)
    x['load']=x.ail_act.fillna(x.ail_fc)
    for c in ('cc','sc','cogen','gfs','bio','hydro','stor'):
        if c not in x: x[c]=np.nan
    x['gas']=x[['cc','sc','cogen','gfs']].sum(axis=1)
    if atc is not None: x=x.join(atc,how='left')
    else: x['imp']=np.nan; x['exp']=np.nan
    return x

def frame(root, folder, comp, say):
    """The forward feeds, with the intertie rule applied and the cushion built.

    Shared by the page and by the history extension in update.py, so the
    cushion on the newest settled hours is computed exactly one way."""
    itb=json.loads((root/'model'/'itbands.json').read_text())
    IX=np.array([b['cushion'] for b in itb if b.get('med') is not None])
    IY=np.array([b['med'] for b in itb if b.get('med') is not None])
    o=np.argsort(IX); IX,IY=IX[o],IY[o]
    f=_feeds(folder)
    f=f.dropna(subset=['cc','sc','cogen','gfs','bio','load','wind','solar'])
    f['internal']=f.gas+f.bio+f.wind+f.solar
    it=np.array([float(np.interp(v,IX,IY)) for v in (f.internal-f['load']-125.0)])
    if f['imp'].notna().any():
        hi=f['imp'].to_numpy(float); lo=-f['exp'].to_numpy(float)
        cap=np.where(np.isfinite(hi),np.minimum(it,hi),it)
        cap=np.where(np.isfinite(lo),np.maximum(cap,lo),cap)
        n=int((np.abs(cap-it)>1).sum())
        if n: say(f"intertie assumption capped at published ATC on {n} hours")
        it=cap
    f['it']=it
    # On hours that have already settled, what actually flowed beats any rule.
    # net_imports_actual_scheduled is the composition table's observed
    # interchange; it only exists for the past, so this touches ACTUAL hours
    # only and leaves every forecast hour on the assumption.
    if comp is not None and 'net_imports_actual_scheduled' in comp.columns:
        ni=pd.to_numeric(comp['net_imports_actual_scheduled'],errors='coerce').reindex(f.index)
        m=ni.notna()&f['price'].notna()
        if m.any():
            f.loc[m,'it']=ni[m]
            say(f"observed interchange used on {int(m.sum())} settled hours")
    f['cush']=f.internal-(f['load']-f.it)
    f['he']=f.index.hour+1
    f['g']=f.he.map(lambda h: 0 if h<=6 else 1 if h<=10 else 2 if h<=16 else 3 if h<=21 else 4)
    return f, itb


def build(root, folder, d, scored, grid, tr7, comp, load_cor, gates, say, f=None, itb=None, pdays=365):
    if f is None or itb is None: f, itb = frame(root, folder, comp, say)
    hours=[]
    for t,r in f.iterrows():
        if pd.isna(r.wind) or pd.isna(r.solar): continue
        h={'dt':t.isoformat(),'he':int(t.hour)+1,
           'status':'ACTUAL' if pd.notna(r.get('price')) else 'FORECAST',
           'load':round(float(r['load']),1)}
        for c in ('cc','sc','cogen','gfs','bio'): h[c]=round(float(r[c]),1)
        h['wind']=round(float(r.wind),2); h['solar']=round(float(r.solar),2)
        h['bcmatl']=round(float(r.it),1); h['sask']=0.0
        h['price']=round(float(r.price),2) if pd.notna(r.get('price')) else None
        for s,dst in (('wind_lo','w_lo'),('wind_hi','w_hi'),('solar_lo','s_lo'),('solar_hi','s_hi')):
            if pd.notna(r.get(s)): h[dst]=round(float(r[s]),1)
        hs=sum(float(r[c]) for c in ('hydro','stor') if pd.notna(r.get(c)))
        h['hydstor']=round(hs,1) if hs>0 else OTHER_MW
        hours.append(h)
    # 30-day normal hour
    dr=d[d.index>d.index.max()-pd.Timedelta(days=30)].copy(); dr['he']=dr.index.hour+1
    norm={}
    for he,s in dr.groupby('he'):
        norm[str(he)]={'load':round(float(s['load'].median()),1),'wind':round(float(s.wind_a.median()),1),
                       'solar':round(float(s.solar_a.median()),1),'gas':round(float(s.gas.median()),1),
                       'it':round(float(s.ni.median()),1)}
    # analogue library
    dd=d.dropna(subset=['price']).copy(); dd['day']=dd.index.normalize()
    lib=[]
    for day,g in dd.groupby('day'):
        ev=g[g.he.between(17,21)]
        if len(g)<20 or len(ev)<4: continue
        lib.append({'d':str(day.date()),'doy':int(day.dayofyear),
            'f':[float(x) for x in (ev.cush.mean(),g.cush.min(),g.cush.mean(),ev.wind_a.mean(),
                 g.wind_a.mean(),g.wind_a.min(),g['load'].mean(),g['load'].max(),
                 g.solar_a.mean(),g.gas.mean())],
            'px':[round(float(g.price.mean()),2),round(float(g.price.max()),2),
                  int((g.price>100).sum()),int((g.price>300).sum())],
            'prof':[round(float(v),1) for v in g.sort_values('he').price.tolist()]})
    FW=np.array([2.0,1.5,1.0,1.5,1.0,0.8,1.2,1.0,0.8,0.8])
    FN=['cush_ev','cush_min','cush_mean','wind_ev','wind_mean','wind_min','load_mean','load_pk','solar_mean','gas_mean']
    F=np.array([x['f'] for x in lib]); mu,sd=F.mean(0),F.std(0); sd[sd==0]=1
    Z=(F-mu)/sd; doy=np.array([x['doy'] for x in lib])
    hf=pd.DataFrame(hours); hf['dt']=pd.to_datetime(hf.dt); hf['day']=hf.dt.dt.normalize()
    hf['cush']=(hf.cc+hf.sc+hf.cogen+hf.gfs+hf.bio+hf.wind+hf.solar)-(hf.load-hf.bcmatl-hf.sask)
    hf['gasv']=hf.cc+hf.sc+hf.cogen+hf.gfs; hf['he']=hf.dt.dt.hour+1
    analog={}
    for day,g in hf.groupby('day'):
        if (g.status=='FORECAST').sum()<12: continue
        ev=g[g.he.between(17,21)]
        if len(g)<20 or len(ev)<4: continue
        v=np.array([ev.cush.mean(),g.cush.min(),g.cush.mean(),ev.wind.mean(),g.wind.mean(),
                    g.wind.min(),g.load.mean(),g.load.max(),g.solar.mean(),g.gasv.mean()],float)
        dist=np.sqrt((((Z-(v-mu)/sd)*FW)**2).sum(1))
        dz=np.abs(doy-day.dayofyear); dz=np.minimum(dz,365-dz); dist=dist+0.010*dz
        top=np.argsort(dist)[:3]
        analog[str(day.date())]={'target':dict(zip(FN,[round(float(x),1) for x in v])),
            'matches':[{'day':lib[i]['d'],'score':round(float(dist[i]),2),'px_avg':lib[i]['px'][0],
                        'px_max':lib[i]['px'][1],'n100':lib[i]['px'][2],'n300':lib[i]['px'][3],
                        'cush_ev':round(lib[i]['f'][0]),'wind_ev':round(lib[i]['f'][3]),
                        'load_pk':round(lib[i]['f'][7]),'prof':lib[i]['prof']} for i in top]}
    bands=json.loads((root/'model'/'fund_bands.json').read_text())
    itp10=json.loads((root/'model'/'itp10.json').read_text())
    meta={'curve_start':str(tr7.index.min().date()),'curve_end':str(tr7.index.max().date()),
          'pool_days':int(pdays),'data_as_of':str(f.index.max()),'last_settled':str(scored.index.max()),
          'curve_mean':round(float(tr7.price.mean()),2),
          'norm_start':str(dr.index.min().date()),'norm_end':str(dr.index.max().date()),
          'bundle_built':str(pd.Timestamp.now())[:10],'bundle_age_days':0,
          'gates':{str(k):v for k,v in gates.items()}}
    payload={'hours':hours,'grid':grid,'itbands':itb,'meta':meta,'bands':bands,
             'norm':norm,'analog':analog,'itp10':itp10}
    tpl=(root/'model'/'template.html').read_text(encoding='utf-8')
    out=root/'docs'/'index.html'; out.parent.mkdir(exist_ok=True)
    out.write_text(tpl.replace('__DATA__',json.dumps(payload,separators=(',',':'))),encoding='utf-8')
    say(f"{len(hours)} hours, {len(analog)} analogue days -> {out} ({out.stat().st_size/1e6:.2f} MB)")
