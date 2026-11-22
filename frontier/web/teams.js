const COLS=[['model','Team'],['status','Status','st'],['final','Global','n'],['science','Science','n'],['artifacts','Code and data','n'],['efficiency','Efficiency','n'],['spent','Budget'],['time','Time','n']];
function board2(){const {k,dir}=S.sort,rk=ranks();
 $('board2').innerHTML=`<thead><tr><th class="n">#</th>${COLS.map(([c,l,n])=>`<th class="${n||''}" ${k===c?`aria-sort="${dir>0?'ascending':'descending'}"`:''}><button data-sort="${c}" ${c==='status'?`data-tip="${esc(SLEGEND)}"`:''}>${l}</button></th>`).join('')}</tr></thead><tbody>${
 order(shown(),k,dir).map(t=>{const s=t.scores,r=rk[t.name]||{};
 return `<tr><td class="n">${r.final??'–'}</td><td>${who(t)}</td><td class="st">${sicon(t)}</td><td class="n"><b>${sc(s.final)}</b></td><td class="n">${sc(s.science)}</td><td class="n">${sc(s.artifacts)}</td><td class="n">${sc(s.efficiency)}</td><td>${budget(t)}</td><td class="n">${dur(elapsed(t))}</td></tr>`}).join('')}</tbody>`}

/* team cards */
function meter(label,spent,committed,cap,cur,colr){if(cap==null)return `<div class="meter"><div class="row"><b>${label}</b><span>${money(spent,cur)} · no cap</span></div></div>`;
 const p=Math.min(100,100*spent/cap),c=Math.min(100-p,100*(committed||0)/cap),warn=p+c>90?'near the cap':p+c>70?'70 % used':'';
 return `<div class="meter"><div class="row"><b><i class="sw" style="background:${colr}"></i>${label}</b><span>${money(spent,cur)} of ${NF.format(cap)} ${cur} · ${Math.round(p)} %${committed?` · ${money(committed,cur)} committed`:''}${warn?` · <strong style="color:${p+c>90?'var(--bad)':'var(--warn)'}">${warn}</strong>`:''}</span></div>
 <div class="track" role="img" aria-label="${label}: ${Math.round(p)} % spent${committed?`, ${Math.round(c)} % committed`:''}"><i style="width:${p}%;background:${colr}"></i>${c?`<i class="c" style="width:${c}%;background:${colr}"></i>`:''}</div></div>`}
function card(t,rk){const s=t.status,l=s.llm,c=s.cloud,tk=t.tokens,id=t.name.replace(/\W/g,'_'),r=rk[t.name]||{},sx=t.scores;
 const subs=(t.subagents||[]).filter(x=>x.kind==='spawn_agent').length,models=Object.entries(l.by_model).sort((a,b)=>b[1].usd-a[1].usd);
 const runners=(t.runners||[]).slice().sort((a,b)=>(a.state==='deleted')-(b.state==='deleted'));
 const stat=(v,lab,rank)=>`<div class="stat"><div class="v">${v}</div><div class="l">${lab}${rank?` · #${rank}`:''}</div></div>`;
 const panel=(what,label,body)=>`<details id="d-${id}-${what}"><summary>${label}</summary><div tabindex="0" role="region" aria-label="${esc(label)} of ${esc(t.model)}">${body}</div></details>`;
 return `<article class="card team"><div class="team-h"><div>${logos(t,'team-logos')}<h3>${key(t)}${esc(t.model)}</h3></div>${pill(t)}</div>
 <p class="meta">${esc(t.harness_title)} · ${esc(t.provider)} · ${esc(t.tier)} tier, reasoning ${esc(t.reasoning)} · ${dur(elapsed(t))} ${t.delivered?'to delivery':'elapsed'}${t.running?` · session ${t.running.n}`:''} · ${subs} sub-agents · ${Object.values(s.ledgers_intact||{}).every(v=>v===true)?'ledgers intact':'<strong style="color:var(--bad)">ledger tampered</strong>'}</p>
 <div class="stats">${stat(sc(sx.final),'global',r.final)}${stat(sc(sx.science),'science',r.science)}${stat(sc(sx.artifacts),'code and data',r.artifacts)}${stat(sc(sx.efficiency),'efficiency',r.efficiency)}</div>
 ${meter('Model calls',l.spent_usd,l.in_flight_usd,l.cap_usd,'USD','var(--c-llm)')}${meter('Scaleway',c.scaleway.spent,c.scaleway.committed,c.scaleway.cap,'EUR','var(--c-sim)')}${meter('Runpod',c.runpod.spent,c.runpod.committed,c.runpod.cap,'USD','var(--c-sim)')}
 <p class="meta" style="margin:10px 0">${compact(tk.input)} tokens in (${tk.input?Math.round(100*tk.cached/tk.input):0} % cached) · ${compact(tk.output)} out (${compact(tk.reasoning)} reasoning)${s.hours_left!=null&&!t.delivered?` · ${dur(s.hours_left*3600)} left`:''}</p>
 ${panel('models',`Models (${models.length})`,`<table><thead><tr><th>Model</th><th class="n">Calls</th><th class="n">In</th><th class="n">Out</th><th class="n">USD</th></tr></thead><tbody>${
  models.map(([m,v])=>`<tr><td>${esc(m)}</td><td class="n">${num(v.calls)}</td><td class="n">${compact(v.input)}</td><td class="n">${compact(v.output)}</td><td class="n">${money(v.usd,'')}</td></tr>`).join('')}</tbody></table>`)}
 ${panel('machines',`Machines (${t.alive.length} running, ${runners.length} in all)`,runners.length?`<table><thead><tr><th>Machine</th><th>State</th><th class="n">Time</th><th class="n">Cost</th><th class="n">Commands</th></tr></thead><tbody>${
  runners.map(m=>`<tr><td>${esc(m.provider)} ${esc(m.type||'')}<div class="id" title="${esc(m.id)}">${esc(m.id.slice(0,12))}${m.id.length>12?'…':''}</div></td><td><span class="pill ${m.state==='deleted'?'':'good run'}">${esc(m.state||'')}</span></td><td class="n">${dur(m.hours*3600)}</td><td class="n">${money(m.cost,m.currency)}</td><td class="n">${m.commands}</td></tr>`).join('')}</tbody></table>`:'<div class="empty">No machine</div>')}
 ${panel('actions','Latest actions',`<table><tbody>${t.actions.slice(-40).reverse().map(a=>`<tr><td><span class="kind">${esc(a.kind)}</span></td><td>${esc(a.text)}</td></tr>`).join('')}</tbody></table>`)}</article>`}
function cards(){const k=$('tsort').value,rk=ranks();$('tcards').innerHTML=order(shown(),k,k==='model'?1:-1).map(t=>card(t,rk)).join('')}
