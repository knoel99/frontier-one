/* activity */
function withRoles(acts){const role={};for(const r of S.d.subagents||[])role[r.agent]=r.role;return acts.map(a=>a.kind==='sub-agent'?{...a,role:role[a.label]||''}:a)}
function chartGantt(){const names=new Set(shown().map(t=>t.name)),k=$('gkind').value;
 gantt($('c-gantt'),withRoles((S.d.activities||[]).filter(a=>names.has(a.team)&&(!k||a.kind===k))),{by:$('gby').value,win:+$('gwin').value*1000,colorBy:a=>BY[a.team]?col(BY[a.team]):'var(--mute)'})}
const FAST=new Set(['fast','priority','ultrafast','highspeed','flashx']);
function chartModels(){const el=$('c-models'),names=new Set(shown().map(t=>t.name)),src=$('msrc').value,md=$('mmode').value,win=+$('mwin').value*1000,now=Date.parse(S.d.time);
 const cs=(S.d.model_calls||[]).filter(c=>c.source!=='launcher'&&names.has(c.team)&&(!src||c.source===src)&&(!md||(md==='fast'?FAST.has(c.mode):md==='flex'?c.mode==='flex':!FAST.has(c.mode)&&c.mode!=='flex'))&&(!win||Date.parse(c.t)>=now-win));
 if(!cs.length){el.innerHTML='<div class="empty">No model call matches</div>';return}
 const t0=win?now-win:Math.min(...cs.map(c=>Date.parse(c.t))),t1=Math.max(now,t0+1000),rows=[...new Set(cs.map(c=>`${c.team}|${c.model}`))].sort();
 const w=Math.max(720,W0(el)),L=280,RH=18,h=rows.length*RH+28,X=t=>L+(w-L-12)*(t-t0)/(t1-t0);
 let s=`<svg viewBox="0 0 ${w} ${h}" height="${h}" role="img" aria-label="Model calls over time">`;
 for(let i=0;i<=6;i++){const tt=t0+(t1-t0)*i/6,x=X(tt);s+=`<line class="grid" x1="${x}" x2="${x}" y1="0" y2="${h-20}"/><text x="${x}" y="${h-5}" text-anchor="middle">${esc(day(tt))}</text>`}
 rows.forEach((r,k)=>{const [tm,m]=r.split('|');s+=`<text class="lab" x="4" y="${k*RH+13}">${esc((BY[tm]?BY[tm].harness_title:tm)+' · '+m.split('/').pop())}</text>`});
 const bucket={};for(const c of cs){const k=rows.indexOf(`${c.team}|${c.model}`),px=Math.round(X(Date.parse(c.t))),f=FAST.has(c.mode)?2:c.mode==='flex'?1:0,id=`${k}:${px}:${f}`;
  const b=bucket[id]||(bucket[id]={k,px,f,n:0,usd:0,team:c.team,model:c.model,mode:c.mode,t:c.t,src:c.source});b.n++;b.usd+=c.usd}
 for(const b of Object.values(bucket)){const c=b.f===2?'var(--bad)':b.f===1?'var(--mute)':BY[b.team]?col(BY[b.team]):'var(--mute)';
  s+=`<rect x="${b.px-1}" y="${b.k*RH+(b.f===2?1:4)}" width="2.5" height="${b.f===2?RH-2:RH-8}" fill="${c}" data-tip="${esc(`${money(b.usd)} · ${b.n} call${b.n>1?'s':''}\n${b.team} · ${b.model}\n${day(b.t)} · mode ${b.mode} · ${b.src==='gateway'?'harness':'sub-agent'}`)}"/>`}
 el.innerHTML=s+'</svg>'}

/* sub-agents and feed */
const ROLEC=['var(--s1)','var(--s2)','var(--s3)','var(--s4)'];
function union(iv){iv.sort((a,b)=>a[0]-b[0]);let tot=0,cur=null;for(const [a,b] of iv){if(!cur||a>cur[1]){if(cur)tot+=cur[1]-cur[0];cur=[a,b]}else cur[1]=Math.max(cur[1],b)}return tot+(cur?cur[1]-cur[0]:0)}
function subagents(){const names=new Set(shown().map(t=>t.name)),tm=$('steam').value,ro=$('srole').value,all=(S.d.subagents||[]).filter(r=>names.has(r.team)&&(!tm||r.team===tm));
 const roles=[...new Set((S.d.subagents||[]).map(r=>r.role))].sort(),rs=$('srole');if(rs.options.length-1!==roles.length)rs.innerHTML='<option value="">all roles</option>'+roles.map(x=>`<option${x===ro?' selected':''}>${esc(x)}</option>`).join('');
 const ts=$('steam');if(ts.options.length-1!==TEAMS.length)ts.innerHTML='<option value="">all teams</option>'+TEAMS.map(t=>`<option value="${esc(t.name)}"${t.name===tm?' selected':''}>${esc(t.model)} · ${esc(t.harness_title)}</option>`).join('');
 const rows=all.filter(r=>!ro||r.role===ro),N=25,pages=Math.max(1,Math.ceil(rows.length/N));S.page=Math.min(S.page,pages-1);
 const by={};for(const r of all){const x=by[r.role]||(by[r.role]={n:0,usd:0});x.n++;x.usd+=r.usd||0}
 $('subsum').textContent=`${all.filter(r=>r.kind==='spawn_agent').length} spawn_agent runs, ${money(all.reduce((a,r)=>a+(r.usd||0),0))}: `+Object.entries(by).map(([k,v])=>`${k} ${v.n}`).join(', ')+'. Native harness sub-agents are paid in the harness traffic. Pick a team to see its simulation time.';
 const now=Date.parse(S.d.time),det=$('sdetail');
 if(tm&&BY[tm]){const t=BY[tm],mach=(S.d.activities||[]).filter(a=>a.team===tm&&a.kind==='machine'),iv=mach.map(a=>[Date.parse(a.start),a.end?Date.parse(a.end):now]),rcol=r=>ROLEC[roles.indexOf(r)%ROLEC.length];
  det.innerHTML=`<div class="facts"><div><div class="v">${dur(union(iv)/1000)}</div><div class="l">with at least one machine running</div></div><div><div class="v">${(iv.reduce((a,[x,y])=>a+y-x,0)/3.6e6).toFixed(1)} h</div><div class="l">machine-hours on ${mach.length} machine${mach.length>1?'s':''}</div></div>
  <div><div class="v"><i class="sw" style="background:var(--c-sim)"></i>${money(simUsd(t))}</div><div class="l">simulation spend</div></div>${Object.entries(by).map(([k,v])=>`<div><div class="v"><span class="key" style="color:${rcol(k)}"></span> ${v.n}</div><div class="l">${esc(k)} · ${money(v.usd)}</div></div>`).join('')}</div><div class="chart scroll" id="c-steam"></div>`;
  gantt($('c-steam'),withRoles((S.d.activities||[]).filter(a=>a.team===tm&&(a.kind==='machine'||a.kind==='sub-agent'))),{by:'role',win:0,colorBy:a=>a.kind==='machine'?'var(--c-sim)':rcol(a.role)})}
 else det.innerHTML='';
 $('subs').innerHTML=`<thead><tr><th>Start</th><th>Team</th><th>Role</th><th>Agent</th><th>Model</th><th>Effort</th><th>Mode</th><th class="n">Time</th><th class="n">Calls</th><th class="n">Tokens in</th><th class="n">Out</th><th class="n">USD</th><th>Status</th><th>Task</th></tr></thead><tbody>${
 rows.slice(S.page*N,S.page*N+N).map(r=>`<tr><td>${esc(r.start?day(r.start):'')}</td><td>${BY[r.team]?key(BY[r.team])+' '+esc(BY[r.team].model):esc(r.team)}</td><td><b>${esc(r.role||'')}</b></td><td class="id">${esc(r.agent)}</td><td>${esc(r.model)}</td><td>${esc(r.effort||'')}</td>
 <td>${FAST.has(r.mode)?`<span class="pill bad">${esc(r.mode)}</span>`:esc(r.mode||'')}</td><td class="n">${dur(r.seconds)}</td><td class="n">${r.calls??'–'}</td><td class="n">${compact(r.input)}</td><td class="n">${compact(r.output)}</td><td class="n">${r.usd==null?'–':money(r.usd,'')}</td>
 <td><span class="pill ${r.status==='done'?'':'good run'}">${esc(r.status)}</span></td><td style="min-width:260px" title="${esc(r.answer||'')}">${esc(r.task)}</td></tr>`).join('')}</tbody>`;
 $('spager').innerHTML=`<span>Page ${S.page+1} of ${pages}</span><button data-pg="-1" ${S.page?'':'disabled'}>Previous</button><button data-pg="1" ${S.page<pages-1?'':'disabled'}>Next</button>`}
function feed(){const names=new Set(shown().map(t=>t.name)),k=$('fkind').value,rows=(S.d.feed||[]).filter(a=>names.has(a.team)&&(!k||a.kind===k));
 const kinds=[...new Set((S.d.feed||[]).map(a=>a.kind))].sort(),sel=$('fkind');if(sel.options.length-1!==kinds.length)sel.innerHTML='<option value="">all</option>'+kinds.map(x=>`<option${x===k?' selected':''}>${esc(x)}</option>`).join('');
 $('feedt').innerHTML=`<thead><tr><th>Time</th><th>Team</th><th>Kind</th><th>Action</th></tr></thead><tbody>${rows.map(a=>`<tr><td>${esc(clock(a.t.slice(0,19)+'Z'))}</td><td>${BY[a.team]?key(BY[a.team])+' '+esc(BY[a.team].model):esc(a.team)}</td><td><span class="kind">${esc(a.kind)}</span></td><td>${esc(a.text)}</td></tr>`).join('')}</tbody>`}
