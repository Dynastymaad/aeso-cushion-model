"""
analog_page.py - Cushion_Analogs.html: find the hours in history whose cushion
looked like the one you are trading, and see how each of those days played out
hour by hour. Built by checklist_ab.py from the same hourly frame as the
History_Hourly tab; opens in any browser, no internet needed.
"""
import json
import numpy as np, pandas as pd

COLS = [  # key in frame, label, decimals
 ('temp', 'Temp C', 1), ('load_tesla', 'Load Tesla fc', 0), ('load_aeso', 'Load AESO fc', 0), ('ail', 'Load actual', 0),
 ('cogen', 'Cogen', 0), ('cc', 'CC', 0), ('gfs', 'Gas steam', 0), ('sc', 'Simple cycle', 0), ('wind', 'Wind', 0), ('solar', 'Solar', 0),
 ('hydro', 'Hydro', 0), ('energy_storage', 'Storage', 0), ('atc', 'Import ATC', 0), ('net_imports_actual_scheduled', 'Net imports', 0),
 ('rem', 'MW before SC', 0), ('cush', 'Cushion', 0),
 ('cush_fc', 'Cushion (model, day before)', 0), ('p10', 'P10', 1), ('p25', 'P25', 1), ('p50', 'P50', 1), ('p75', 'P75', 1), ('p90', 'P90', 1), ('ev', 'Fair value', 1),
 ('price', 'Pool price', 2),
]

def write(H, out, tom, cf_calm=0.16):
    """H: hourly frame indexed by timestamp with columns d, he, wcap and the keys in COLS (missing ones are filled blank)."""
    H = H.sort_index()
    for k, *_ in COLS:
        if k not in H: H[k] = np.nan
    days = []
    for d, g in H.groupby('d'):
        if g.price.notna().sum() < 20: continue
        g = g.set_index('he').reindex(range(1, 25))
        wcf = float(g.wind.mean() / g.wcap.mean()) if g.wcap.notna().any() and g.wcap.mean() > 0 else None
        rows = []
        for k, lab, dec in COLS:
            v = g[k].round(dec)
            rows.append([None if pd.isna(x) else (int(x) if dec == 0 else float(x)) for x in v])
        days.append({'d': d.strftime('%Y-%m-%d'), 'dow': d.strftime('%a'), 'm': int(d.month), 'cf': None if wcf is None else round(100*wcf, 1),
                     'tmax': None if g.temp.isna().all() else round(float(g.temp.max()), 1), 'px': round(float(g.price.mean()), 2), 'pxmax': round(float(g.price.max()), 2),
                     'hl': round(float(g.loc[8:23, 'price'].mean()), 2), 'cmin': None if g.cush.isna().all() else int(g.loc[8:23, 'cush'].min()), 'v': rows})
    meta = {'cols': [[k, lab] for k, lab, _ in COLS], 'tom': tom.strftime('%Y-%m-%d'), 'tom_m': int(tom.month), 'calm': round(100*cf_calm, 1),
            'first': days[0]['d'], 'last': days[-1]['d'], 'n': len(days)}
    html = TEMPLATE.replace('__DATA__', json.dumps(days, separators=(',', ':'))).replace('__META__', json.dumps(meta))
    out.write_text(html, encoding='utf-8')
    return len(days)

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Cushion analogs</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#6b6b6b;--line:#e3e3e3;--acc:#1f5fbf;--hi:#fff3cd;--red:#b42318;--grn:#1a7f37;--pan:#f6f7f9;--bear:#1a7f37;--bull:#b42318}
@media(prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#e6e6e6;--mut:#9a9a9a;--line:#2c3036;--acc:#6ea3f5;--hi:#4a3d12;--red:#f08c84;--grn:#6cc38a;--pan:#1e2126;--bear:#6cc38a;--bull:#f08c84}}
*{box-sizing:border-box}body{margin:0;font:13px/1.4 Arial,Helvetica,sans-serif;background:var(--bg);color:var(--fg)}
header{padding:12px 16px;border-bottom:1px solid var(--line)}h1{font-size:16px;margin:0 0 2px}header p{margin:0;color:var(--mut);font-size:12px}
.wrap{display:grid;grid-template-columns:300px 1fr;min-height:calc(100vh - 60px)}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
aside{padding:12px 16px;border-right:1px solid var(--line);background:var(--pan)}
main{padding:12px 16px;overflow:auto}
label{display:block;font-size:11px;color:var(--mut);margin-top:8px}
input,select{width:100%;padding:5px 6px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg);font:inherit}
.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.sum{margin-top:12px;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);font-size:12px}.sum b{font-size:15px}
.hits{margin-top:10px;max-height:40vh;overflow:auto;border-top:1px solid var(--line);font-size:11px}
.hit{padding:4px 4px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;justify-content:space-between}.hit:hover,.hit.on{background:var(--hi)}
h2{font-size:14px;margin:14px 0 6px}h2:first-child{margin-top:0}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{padding:3px 6px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{position:sticky;top:0;background:var(--pan);font-weight:bold}
th:first-child,td:first-child{text-align:left}
td.bear{color:var(--bear)}td.bull{color:var(--bull)}td.base{font-weight:bold}
tr.m td{background:var(--hi)}
.legend{font-size:11px;color:var(--mut);margin:2px 0 8px}
.pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;background:var(--pan);border:1px solid var(--line);margin-right:4px}
.muted{color:var(--mut)}
button{padding:5px 10px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg);cursor:pointer;margin-top:10px}
.grp td:first-child{font-weight:bold;background:var(--pan)}
.sub{font-size:11px;color:var(--mut)}
details{margin-top:12px}summary{cursor:pointer;font-weight:bold;font-size:13px}
</style></head><body>
<header><h1>Cushion analogs</h1><p id="hdr"></p></header>
<div class="wrap">
<aside>
 <label>Cushion (MW) and +/- band</label>
 <div class="row"><input id="cush" type="number" value="900"><input id="band" type="number" value="150"></div>
 <div class="row"><label>HE from</label><label>HE to</label></div>
 <div class="row"><input id="h0" type="number" min="1" max="24" value="17"><input id="h1" type="number" min="1" max="24" value="21"></div>
 <div class="row"><label>Month</label><label>Months either side</label></div>
 <div class="row"><input id="mo" type="number" min="1" max="12"><input id="mw" type="number" min="0" max="6" value="1"></div>
 <label>Wind on the day</label>
 <select id="wc"><option value="any">Any</option><option value="calm">Calm (capacity factor below 16%)</option><option value="windy">Windy</option></select>
 <label>Bear / bull = 10th / 90th percentile across the matched days, oriented for price: bull = the tight side (high load and price, low wind, solar, thermal, imports, cushion).</label>
 <button id="go">Find</button>
 <div class="sum" id="sum"></div>
 <details open><summary>Days behind the numbers</summary><div class="hits" id="hits"></div></details>
</aside>
<main id="main"></main></div>
<script>
const DATA=__DATA__, META=__META__;
const K=Object.fromEntries(META.cols.map((c,i)=>[c[0],i]));
const LAB=Object.fromEntries(META.cols);
document.getElementById('hdr').textContent=`${META.n} settled days, ${META.first} to ${META.last}. Same feeds and definitions as the cushion model and the History_Hourly tab. Built for delivery day ${META.tom}.`;
document.getElementById('mo').value=META.tom_m;
const $=id=>document.getElementById(id), num=(id,d)=>{const v=parseFloat($(id).value);return isNaN(v)?d:v};
const f0=v=>v==null||isNaN(v)?'':Math.round(v).toLocaleString(), f1=v=>v==null||isNaN(v)?'':(+v).toFixed(1), sg=v=>v==null||isNaN(v)?'':(v>0?'+':'')+Math.round(v).toLocaleString();
// which way is bull (tight) for each variable: +1 = high is bull, -1 = low is bull, 0 = neutral (show low / high)
const DIR={price:1,ail:1,load_tesla:1,load_aeso:1,temp:0,wind:-1,solar:-1,cogen:-1,cc:-1,gfs:-1,sc:-1,hydro:-1,energy_storage:-1,atc:-1,net_imports_actual_scheduled:-1,rem:-1,cush:-1,cush_fc:-1,p10:1,p25:1,p50:1,p75:1,p90:1,ev:1};
const VARS=['price','ail','temp','wind','solar','cogen','cc','gfs','sc','hydro','energy_storage','net_imports_actual_scheduled','atc','rem','cush'];
function monthOk(m,m0,w){const d=Math.abs(((m-m0+6)%12+12)%12-6);return d<=w}
function q(arr,p){const a=arr.filter(x=>x!=null&&!isNaN(x)).sort((x,y)=>x-y);if(!a.length)return null;const i=(a.length-1)*p,lo=Math.floor(i),hi=Math.ceil(i);return a[lo]+(a[hi]-a[lo])*(i-lo)}
function mean(arr){const a=arr.filter(x=>x!=null&&!isNaN(x));return a.length?a.reduce((s,x)=>s+x,0)/a.length:null}
let hits=[], h0=17, h1=21;
function find(){
  const c=num('cush',900), b=num('band',150); h0=Math.max(1,num('h0',17)); h1=Math.min(24,num('h1',21)); if(h1<h0){const t=h0;h0=h1;h1=t}
  const m0=num('mo',META.tom_m), mw=num('mw',1), wc=$('wc').value;
  hits=[];
  for(const d of DATA){
    if(!monthOk(d.m,m0,mw)) continue;
    if(wc==='calm'&&!(d.cf!=null&&d.cf<META.calm)) continue;
    if(wc==='windy'&&!(d.cf!=null&&d.cf>=META.calm)) continue;
    const cu=d.v[K.cush], hs=[];
    for(let he=h0;he<=h1;he++){const v=cu[he-1];if(v!=null&&Math.abs(v-c)<=b)hs.push(he)}
    if(hs.length) hits.push({d,hs});
  }
  hits.sort((a,b)=>b.d.d.localeCompare(a.d.d));
  const px=[];for(const h of hits)for(const he of h.hs){const p=h.d.v[K.price][he-1];if(p!=null)px.push(p)}
  $('sum').innerHTML=hits.length?`<b>${hits.length}</b> days, <b>${px.length}</b> hours matched<br>price in those hours: avg <b>$${f1(mean(px))}</b>, median $${f1(q(px,.5))}<br>bear (p10) $${f1(q(px,.1))} &nbsp; bull (p90) $${f1(q(px,.9))}<br>over $100: ${Math.round(100*px.filter(x=>x>100).length/px.length)}% &nbsp; over $300: ${Math.round(100*px.filter(x=>x>=300).length/px.length)}%`:'No days matched. Widen the band or the months.';
  $('hits').innerHTML=hits.map((h,i)=>`<div class="hit" data-i="${i}"><span>${h.d.d} ${h.d.dow}</span><span class="muted">HE ${h.hs.join(',')} &middot; $${f1(h.d.hl)} HL &middot; ${h.d.cf==null?'':(h.d.cf<META.calm?'calm':'windy')}</span></div>`).join('');
  document.querySelectorAll('.hit').forEach(e=>e.onclick=()=>showDay(+e.dataset.i));
  render();
}
function cell(v,cls,f){return `<td class="${cls}">${f(v)}</td>`}
function tri(vals,dir,f){ // returns bear, base, bull cells
  const lo=q(vals,.1),mi=mean(vals),hi=q(vals,.9);
  if(dir===0) return cell(lo,'',f)+cell(mi,'base',f)+cell(hi,'',f);
  const bear=dir>0?lo:hi, bull=dir>0?hi:lo;
  return cell(bear,'bear',f)+cell(mi,'base',f)+cell(bull,'bull',f);
}
function render(){
  if(!hits.length){$('main').innerHTML='<p class="muted">No days matched.</p>';return}
  const days=hits.map(h=>h.d), n=days.length;
  let t=`<h2>Levels in the matched hours (HE ${h0}-${h1}), across ${n} days</h2><div class="legend">Bear / base / bull = p10 / mean / p90 of the hourly values in the matched hours. Temperature is shown low / mean / high.</div>`;
  t+='<table><thead><tr><th>Variable</th><th>Bear</th><th>Base</th><th>Bull</th><th class="sub">min</th><th class="sub">max</th></tr></thead><tbody>';
  for(const k of VARS){
    const vals=[];for(const h of hits)for(const he of h.hs){const v=h.d.v[K[k]][he-1];if(v!=null)vals.push(v)}
    if(!vals.filter(x=>x!=null).length) continue;
    const f=k==='price'?v=>'$'+f1(v):k==='temp'?f1:f0;
    t+=`<tr><td>${LAB[k]}</td>${tri(vals,DIR[k],f)}<td class="sub">${f(Math.min(...vals))}</td><td class="sub">${f(Math.max(...vals))}</td></tr>`;
  }
  t+='</tbody></table>';
  // ramps over the window, per day
  t+=`<h2>Ramps through the window: HE ${h0} to HE ${h1}, per day</h2><div class="legend">Change = value at HE ${h1} minus value at HE ${h0}. Biggest 1-hour up / down = the largest hour-to-hour move inside the window. Swing = max minus min inside the window. Bear / base / bull oriented as above (for supply, a bigger drop is the bull case).</div>`;
  t+='<table><thead><tr><th>Variable</th><th colspan="3">Change HE '+h0+' &rarr; '+h1+' (bear / base / bull)</th><th colspan="3">Biggest 1-hour move up (bear / base / bull)</th><th colspan="3">Biggest 1-hour move down</th><th colspan="3">Swing max-min</th></tr></thead><tbody>';
  for(const k of VARS){
    const ch=[],up=[],dn=[],sw=[];
    for(const d of days){const v=d.v[K[k]];const a=v[h0-1],b=v[h1-1];if(a!=null&&b!=null)ch.push(b-a);
      let mu=null,md=null,mx=null,mn=null;for(let he=h0;he<=h1;he++){const x=v[he-1];if(x==null)continue;if(mx==null||x>mx)mx=x;if(mn==null||x<mn)mn=x;if(he>h0&&v[he-2]!=null){const dl=x-v[he-2];if(mu==null||dl>mu)mu=dl;if(md==null||dl<md)md=dl}}
      if(mu!=null)up.push(mu);if(md!=null)dn.push(md);if(mx!=null)sw.push(mx-mn)}
    if(!ch.length) continue;
    const f=k==='price'?v=>(v>0?'+$':'-$')+f1(Math.abs(v)):k==='temp'?v=>(v>0?'+':'')+f1(v):sg;
    const fs=k==='price'?v=>'$'+f1(v):k==='temp'?f1:f0;
    t+=`<tr><td>${LAB[k]}</td>${tri(ch,DIR[k],f)}${tri(up,DIR[k],f)}${tri(dn,DIR[k],f)}${tri(sw,DIR[k]===0?0:1,fs)}</tr>`;
  }
  t+='</tbody></table>';
  // full-day profiles
  t+=`<h2>Hour by hour across the matched days</h2><div class="legend">For each variable: the mean across the ${n} days by hour, with bear and bull (p10 / p90) rows. The matched window is shaded.</div>`;
  t+='<table><thead><tr><th>Variable</th>';for(let he=1;he<=24;he++)t+=`<th class="${he>=h0&&he<=h1?'':'sub'}">${he}</th>`;t+='</tr></thead><tbody>';
  for(const k of VARS){
    const f=k==='price'?v=>f0(v):k==='temp'?f1:f0;
    const rows={bear:[],base:[],bull:[]};
    for(let he=1;he<=24;he++){const vals=days.map(d=>d.v[K[k]][he-1]);const lo=q(vals,.1),mi=mean(vals),hi=q(vals,.9);const dir=DIR[k];rows.bear.push(dir===0?lo:dir>0?lo:hi);rows.base.push(mi);rows.bull.push(dir===0?hi:dir>0?hi:lo)}
    if(rows.base.every(x=>x==null)) continue;
    t+=`<tr class="grp"><td>${LAB[k]} <span class="sub">base</span></td>`+rows.base.map((v,i)=>`<td class="base ${i+1>=h0&&i+1<=h1?'':'sub'}">${f(v)}</td>`).join('')+'</tr>';
    t+=`<tr><td class="sub">&nbsp;&nbsp;${DIR[k]===0?'low':'bear'}</td>`+rows.bear.map(v=>`<td class="bear">${f(v)}</td>`).join('')+'</tr>';
    t+=`<tr><td class="sub">&nbsp;&nbsp;${DIR[k]===0?'high':'bull'}</td>`+rows.bull.map(v=>`<td class="bull">${f(v)}</td>`).join('')+'</tr>';
  }
  t+='</tbody></table>';
  t+='<div id="day"></div>';
  $('main').innerHTML=t;
}
function showDay(i){
  document.querySelectorAll('.hit').forEach(e=>e.classList.toggle('on',+e.dataset.i===i));
  const h=hits[i], d=h.d, set=new Set(h.hs);
  let t=`<h2>${d.d} ${d.dow}, hour by hour</h2><div class="legend"><span class="pill">avg $${f1(d.px)}</span><span class="pill">HL $${f1(d.hl)}</span><span class="pill">max hour $${f0(d.pxmax)}</span><span class="pill">tightest cushion HE8-23: ${f0(d.cmin)} MW</span><span class="pill">${d.cf==null?'wind n/a':'wind CF '+d.cf+'% ('+(d.cf<META.calm?'calm':'windy')+')'}</span><span class="pill">max temp ${d.tmax==null?'n/a':d.tmax+' C'}</span></div>`;
  t+='<table><thead><tr><th>HE</th>'+META.cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr></thead><tbody>';
  for(let he=1;he<=24;he++){t+=`<tr class="${set.has(he)?'m':''}"><td>${he}</td>`+META.cols.map((c,j)=>{const v=d.v[j][he-1];const k=c[0];const f=(k==='price'||k==='temp'||k.startsWith('p')||k==='ev')?f1:f0;return `<td>${f(v)}</td>`}).join('')+'</tr>'}
  t+='</tbody></table>';
  $('day').innerHTML=t; $('day').scrollIntoView({behavior:'smooth'});
}
$('go').onclick=find;
document.querySelectorAll('aside input,aside select').forEach(e=>e.addEventListener('keydown',ev=>{if(ev.key==='Enter')find()}));
find();
</script></body></html>
"""
