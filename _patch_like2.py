from pathlib import Path
p = Path('model/template.html'); s = p.read_text(encoding='utf-8')

# ---------------------------------------------------------------- sub line
old = """  $('ldSub').textContent=`${L.ndays.toLocaleString('en-CA')} days searched, `
    +`${L.span[0]} to ${L.span[1]} — matched on weather only, never on load`;"""
new = """  $('ldSub').textContent=`${L.ndays.toLocaleString('en-CA')} days searched, `
    +`${L.span[0]} to ${L.span[1]} — matched on how the evening is likely to land`;"""
assert s.count(old)==1, 'sub'
s = s.replace(old, new)

# ---------------------------------------------------------------- cards
old_cards = s[s.index("  // ---- one card per match ---"):s.index("  // ---- what it means ---")]
new_cards = r"""  // ---- one card per match -------------------------------------------------
  const dnum=(x,u,dp)=>`${x>0?'+':''}${x.toFixed(dp)}${u}`;
  $('ldBody').innerHTML=`<div class="ana">`+L.matches.map((m,i)=>{
    const dd=new Date(m.d+'T12:00:00');
    const vs=m.vs, pc=m.vspc;
    const cls=Math.abs(pc)<1.5?'ldmid':(vs>0?'ldhi':'ldlo');
    const word=Math.abs(pc)<1.5?'about normal':(vs>0?'ABOVE normal':'BELOW normal');
    const verdict=`Evening peak ran <b class="${cls}">${word}</b>`
      +(Math.abs(pc)<1.5?'':` — <b class="${cls}">${vs>0?'+':''}${vs.toLocaleString('en-CA')} MW (${pc>0?'+':''}${pc.toFixed(1)}%)</b>`)
      +` for a ${dd.toLocaleDateString('en-CA',{weekday:'long'})} that time of year (${mw(m.norm)} MW).`;
    const gap=m.gap;
    const gcls=gap==null?'':(Math.abs(gap)<150?'ldmid':(gap>0?'ldhi':'ldlo'));
    return `<div class="anac" style="border-left:3px solid ${C[i]}">
      <h4>${dd.toLocaleDateString('en-CA',{weekday:'short',month:'short',day:'numeric',year:'numeric'})}</h4>
      <div class="when">${m.back.toLocaleString('en-CA')} days ago · closeness ${m.score.toFixed(2)} — lower is nearer</div>
      <div class="lddt">
        <div><div class="l">Temp</div><div class="v">${dnum(m.dt,'',1)}</div><div class="u">°C vs today</div></div>
        <div><div class="l">Wind</div><div class="v">${dnum(m.dw,'',1)}</div><div class="u">km/h vs today</div></div>
        <div><div class="l">Radiation</div><div class="v">${dnum(m.dr,'',2)}</div><div class="u">kWh/m² vs today</div></div>
        <div><div class="l">Cloud</div><div class="v" style="color:var(--ink-3)">${dnum(m.dc,'',0)}</div><div class="u">pts · not matched</div></div>
      </div>
      <div class="kv"><span>That day's weather</span><b>${m.tmean}°C · ${m.wmean} km/h · ${m.cmean}% cloud</b></div>
      <div class="ldpk">
        <div class="ldpkv">${mw(m.eve)} <span>MW</span></div>
        <div class="ldpkl">evening peak, HE17–21, restated in today's year</div>
        <div class="ldpks">measured ${mw(m.eve_raw)} MW &middot; ${m.lift>=0?'+':''}${mw(m.lift)} MW of load growth since</div>
        ${gap==null?'':`<div class="ldpkg">vs today: <b class="${gcls}">${gap>0?'+':''}${gap.toLocaleString('en-CA')} MW</b></div>`}
      </div>
      <div class="kv"><span>Single peak hour</span><b>${mw(m.pk)} MW</b></div>
      <div class="ldv">${verdict}</div>
      ${m.px?`<div class="out">
        <div class="kv"><span>Evening price HE17–21</span><b>${m.px.eve==null?'—':usd(m.px.eve)}</b></div>
        <div class="kv"><span>Day average</span><b>${usd(m.px.avg)}</b></div>
        <div class="kv"><span>Worst hour</span><b style="color:${m.px.max>=300?'var(--tight)':'var(--ink)'}">${usd(m.px.max)}</b></div>
        <div class="kv"><span>Hours over $100</span><b>${m.px.n100}</b></div></div>`:''}
    </div>`;}).join('')+`</div>`;

"""
s = s.replace(old_cards, new_cards)

# ---------------------------------------------------------------- verdict block
old_v = s[s.index("  // ---- what it means ---"):s.index("  ldDrawn=true;")]
new_v = r"""  // ---- what it means -------------------------------------------------------
  // 'Disagree' means genuinely pulling opposite ways. Two below and one flat is
  // a lean, not a conflict, and calling it one throws away the only signal here.
  const withLoad=L.matches.filter(m=>m.vs!=null);
  const n=withLoad.length;
  const above=withLoad.filter(m=>m.vspc>1.5).length, below=withLoad.filter(m=>m.vspc<-1.5).length;
  const mid=n-above-below;
  let read;
  if(!n) read='None of the three has settled load behind it yet.';
  else if(above&&below) read=`They pull opposite ways — ${above} above normal, ${below} below. Weather like today's has not reliably moved the evening peak either way, so do not lean on it.`;
  else if(above===n) read=`All ${n} ran <strong>above</strong> normal. Weather like today's has been pushing the evening peak up.`;
  else if(below===n) read=`All ${n} ran <strong>below</strong> normal. Weather like today's has been holding the evening peak down.`;
  else if(above) read=`${above} of ${n} ran <strong>above</strong> normal, ${mid} sat about normal, none below. A lean up, and a mild one.`;
  else if(below) read=`${below} of ${n} ran <strong>below</strong> normal, ${mid} sat about normal, <strong>none above</strong>. A lean down, and a mild one.`;
  else read=`All ${n} sat about normal.`;
  const gaps=L.matches.map(m=>m.gap).filter(x=>x!=null).map(Math.abs);
  const mg=gaps.length?Math.round(gaps.reduce((a,b)=>a+b,0)/gaps.length):null;
  const tvs=(T.vs==null)?'':` Today is tracking <strong>${T.vs>0?'+':''}${T.vs.toLocaleString('en-CA')} MW</strong> against its own normal of ${mw(T.norm)} MW${T.partial?' (part of today is still AESO forecast)':''}.`;

  $('ldText').innerHTML=`
    <p class="lede">Today: <strong>${T.tmean}°C</strong> average, <strong>${T.wmean} km/h</strong> wind at hub height,
      <strong>${T.rtot} kWh/m²</strong> of radiation, <strong>${T.cmean}%</strong> cloud, evening peak
      <strong>${mw(T.eve)} MW</strong>. ${read}${tvs}</p>
    <p><strong>What these days are matched on.</strong> Not weather resemblance — that version matched days that
      looked alike while their evening peaks landed 370 MW apart. These are matched on <em>how the evening is
      likely to land</em>. A model maps the day's weather to its HE17–21 load, and the search looks for days whose
      predicted evening is closest to today's, with a smaller term keeping the raw temperature, wind and radiation
      shapes recognisably similar. Measured on 250 held-out days, that lands the top three
      <strong>${mg!=null?mg+' MW':'~270 MW'}</strong> from today's evening peak against 369 MW for the old method.</p>
    <p><strong>Every load number is restated in today's year.</strong> Alberta's evening peak grows about
      <strong>${L.growth>0?'+':''}${L.growth} MW a year</strong>. A match from two years back would otherwise read
      roughly 640 MW light purely because it is old — which is what made the earlier cards look so wrong. Each card
      shows what the day actually measured and the growth added back.</p>
    <p><strong>Cloud is shown but not matched on.</strong> Fitted against evening peak load it earns a weight of
      zero: it separates days less well than the other three and has no usable link to the evening. It used to eat
      a quarter of the score. It is on the card because you want to see it, greyed because it did not influence
      the match — expect it to drift further than the other three.</p>
    <p class="warn">The floor for this is about <strong>${L.fit_mae} MW</strong> — that is the error of the
      weather-to-peak model itself, and no matching scheme beats its own inputs. The last ${L.excluded} days are
      excluded, because weather runs in spells and the nearest days would otherwise always be this week. And three
      matches is a sample of three: read the closeness score before the verdict, and remember the single peak hour
      is far less predictable from weather than the HE17–21 average is.</p>`;
"""
s = s.replace(old_v, new_v)

# ---------------------------------------------------------------- css
anchor = """.ldhi{color:var(--tight)} .ldlo{color:var(--s-base)} .ldmid{color:var(--ink-2)}"""
s = s.replace(anchor, anchor + """
.ldpk{border-top:1px solid var(--line);margin-top:9px;padding-top:9px}
.ldpkv{font-size:21px;font-weight:700;color:var(--ink);font-variant-numeric:tabular-nums;line-height:1.1}
.ldpkv span{font-size:11px;font-weight:500;color:var(--ink-3)}
.ldpkl{font-size:10.5px;color:var(--ink-2);margin-top:1px}
.ldpks{font-size:10px;color:var(--ink-3);margin-top:3px}
.ldpkg{font-size:12px;color:var(--ink-2);margin-top:5px}""")

p.write_text(s, encoding='utf-8')
print('template updated for evening peak')
