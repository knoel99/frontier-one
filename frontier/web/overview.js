/* score over time, score against cost, spend over time; one time axis from the start of the race to now */
const graded=t=>(t.snapshots||[]).filter(s=>s.quality!=null);
/* the live score as a last point: now (or at delivery), with the spend so far; the leaderboard shows the same value */
const livePoint=t=>{const sc=t.scores;if(sc.science==null)return [];return [{live:true,labels:['now'],minutes:elapsed(t)/60,llm_usd:t.status.llm.spent_usd,simulation_usd:simUsd(t),
 quality:sc.science,artifacts:sc.artifacts,efficiency:sc.efficiency,final:sc.final}]};
const val=s=>s[METRIC[S.metric][1]];
function scoreChart(id,xOf,{log,xt,xl,lo,xmax}){const el=$('c-'+id),mn=METRIC[S.metric][0];
 const ser=shown().map(t=>({t,pts:graded(t).concat(livePoint(t)).map(s=>({s,x:xOf(s),q:val(s)})).filter(p=>p.q!=null&&(!log||p.x>0))})).filter(x=>x.pts.length);
 if(!ser.length){el.innerHTML=`<div class="empty" style="height:${chartH(id)}px">No graded snapshot yet</div>`;$('t-'+id).innerHTML='';return}
 const xs=ser.flatMap(x=>x.pts.map(p=>p.x)),x0=log?Math.max(lo,Math.min(...xs)):0,x1=Math.max(xmax||0,...xs,x0*10||1);
 el.dataset.label=`${mn} out of 100 ${id==='qt'?'over time':'against cost'}, one line per team`;
 const f=frame(el,chartH(id),{x:(a,b)=>log?logs(x0,x1,a,b):lin(0,x1,a,b),y:(a,b)=>lin(0,100,a,b),xt,yt:v=>v,xl});
 let s=f.s;for(const {t,pts} of ser){const c=col(t),P=pts.map(p=>[p.x,p.q]),last=pts[pts.length-1];
  for(const p of pts.slice(0,-1))s+=`<circle class="ring" cx="${f.X(p.x)}" cy="${f.Y(p.q)}" r="3" fill="${c}" data-team="${esc(t.name)}" data-tip="${esc(ptTip(t,p,mn))}"/>`;
  s+=line(f,t,P,`${t.model} · ${t.harness_title}\n${mn} ${sc(last.q)} ${last.s.live?'now, as in the leaderboard':'at its latest snapshot'}\n${dur(last.s.minutes*60)} after the start · ${money(last.s.llm_usd+last.s.simulation_usd)} spent`)}
 el.innerHTML=s+ends(f)+'</svg>';$('t-'+id).innerHTML=qTable(ser)}
const ptTip=(t,p,mn)=>`${mn} ${sc(p.q)} · ${t.model}\nsnapshot ${p.s.labels.join(', ')}, ${dur(p.s.minutes*60)} after the start\n${money(p.s.llm_usd)} model calls, ${money(p.s.simulation_usd)} simulations`;
function qTable(ser){return `<table><thead><tr><th>Team</th><th>Snapshot</th><th class="n">Time</th><th class="n">Model calls</th><th class="n">Simulations</th><th class="n">Science</th><th class="n">Code and data</th><th class="n">Efficiency</th><th class="n">Global</th></tr></thead><tbody>${
 ser.flatMap(({t,pts})=>pts.filter(p=>!p.s.live).map(({s})=>`<tr><td>${key(t)} ${esc(t.model)}</td><td>${esc(s.labels.join(', '))}</td><td class="n">${dur(s.minutes*60)}</td><td class="n">${money(s.llm_usd)}</td><td class="n">${money(s.simulation_usd)}</td><td class="n">${sc(s.quality)}</td><td class="n">${sc(s.artifacts)}</td><td class="n">${sc(s.efficiency)}</td><td class="n">${sc(s.final)}</td></tr>`)).join('')}</tbody></table>`}
const raceHours=()=>(Date.parse(S.d.time)-T0)/3.6e6;
const chartH=id=>narrow()?240:Math.round(Math.max(id===S.feat?200:130,(innerHeight-500)*(id===S.feat?0.6:0.4)));
// hours as d/h/min: 0.5 → 30 min, 30 → 1 d 6 h
const HT=v=>{const m=Math.round(v*60),d=Math.floor(m/1440),h=Math.floor(m%1440/60),n=m%60;return [d&&d+' d',h&&h+' h',n&&n+' min'].filter(Boolean).join(' ')||'0';};
const chartQT=()=>scoreChart('qt',s=>s.minutes/60,{log:S.ax.qt==='log',lo:5/60,xmax:S.ax.qt==='log'?0:raceHours(),xt:HT,xl:'time since the start'});
const chartQC=()=>scoreChart('qc',s=>costOf(s,S.cost.qc),{log:S.ax.qc==='log',lo:0.01,xt:v=>(v<1?v:NF.format(v))+' $',xl:'USD, '+COSTL[S.cost.qc]});
function spendSeries(t,k,t1){const L=(t.series||[]).map(([x,y])=>[Date.parse(x),y]),M=(t.sim_series||[]).map(([x,y])=>[Date.parse(x),y]);
 if(k==='llm')return L.concat(L.length?[[t1,L[L.length-1][1]]]:[]);
 if(k==='sim')return M;
 const out=[];let i=0,j=0,llm=0;const simAt=x=>{while(j+1<M.length&&M[j+1][0]<=x)j++;if(!M.length||x<M[0][0])return 0;if(j+1>=M.length)return M[j][1];const [a,va]=M[j],[b,vb]=M[j+1];return va+(vb-va)*(x-a)/((b-a)||1)};
 for(const x of [...L.map(p=>p[0]),...M.map(p=>p[0]),t1].sort((a,b)=>a-b)){while(i<L.length&&L[i][0]<=x)llm=L[i++][1];out.push([x,llm+simAt(x)])}return out}
function chartSP(){const el=$('c-sp'),k=S.cost.sp,t1=Date.parse(S.d.time),ser=shown().map(t=>({t,P:spendSeries(t,k,t1)})).filter(x=>x.P.length);
 if(!ser.length){el.innerHTML=`<div class="empty">No ${COSTL[k]} yet</div>`;return}
 const H=x=>(x-T0)/3.6e6,ymax=Math.max(...ser.map(x=>x.P[x.P.length-1][1]),1);el.dataset.label='Cumulative spend per team: '+COSTL[k];
 const f=frame(el,chartH('sp'),{x:(a,b)=>lin(0,raceHours(),a,b),y:(a,b)=>lin(0,ymax,a,b),xt:HT,yt:v=>v+' $',xl:'time since the start'});
 let s=f.s;for(const {t,P} of ser){const st=Math.max(1,Math.floor(P.length/400)),Q=P.filter((_,i)=>i%st===0||i===P.length-1).map(([x,y])=>[H(x),y]);
  s+=line(f,t,Q,`${t.model} · ${t.harness_title}\n${money(Q[Q.length-1][1])} of ${COSTL[k]}`,k==='llm')}
 s+=ends(f)+`<line class="xhl axis" y1="${f.P.t}" y2="${f.h-f.P.b}" visibility="hidden"/><rect class="xh" x="${f.P.l}" y="${f.P.t}" width="${f.w-f.P.l-f.P.r}" height="${f.h-f.P.t-f.P.b}" fill="transparent"/>`;
 el.innerHTML=s+'</svg>';const svg=el.querySelector('svg'),rect=el.querySelector('.xh'),xl=el.querySelector('.xhl');
 rect.onpointermove=rect.onpointerdown=e=>{const b=svg.getBoundingClientRect(),px=(e.clientX-b.left)*f.w/b.width,h=(px-f.P.l)/(f.w-f.P.l-f.P.r)*raceHours(),tt=T0+h*3.6e6;
  xl.setAttribute('x1',px);xl.setAttribute('x2',px);xl.setAttribute('visibility','visible');
  const rows=ser.map(({t,P})=>{let v=0;for(const [x,y] of P){if(x>tt)break;v=y}return [t.model,v]}).sort((a,b)=>b[1]-a[1]);
  showTip(`${dur(h*3600)} after the start, ${COSTL[k]}\n`+rows.map(([n,v])=>`${money(v)}  ${n}`).join('\n'),e.clientX,e.clientY)};
 rect.onpointerleave=()=>{xl.setAttribute('visibility','hidden');hideTip()}}

/* budget: everything spent against every cap together, model calls and simulations in their own colours everywhere */
function budget(t,short){const s=t.status,c=s.cloud,cap=(s.llm.cap_usd||0)+(c.scaleway.cap||0)+(c.runpod.cap||0),l=s.llm.spent_usd,m=simUsd(t);
 if(!cap)return `<div class="budget"><div class="t">${money(l+m)} · no cap</div></div>`;const pl=Math.min(100,100*l/cap),pm=Math.min(100-pl,100*m/cap);
 return `<div class="budget" role="img" tabindex="${short?0:-1}" aria-label="${money(l)} model calls and ${money(m)} simulations of ${money(cap)} in all" data-tip="${esc(`${Math.round(pl+pm)} % of all the caps (${NF.format(cap)} USD)\n${money(l)} model calls\n${money(m)} simulations`)}"><div class="bar"><i style="width:${pl}%;background:var(--c-llm)"></i><i style="width:${pm}%;background:var(--c-sim)"></i></div>
 <div class="t">${Math.round(pl+pm)} %${short?'':`<span class="babs"> · ${money(l+m,'')} / ${NF.format(cap)} $</span>`}</div></div>`}
const who=(t,st,nokey)=>`<div class="who">${nokey?'':key(t)}${logos(t,'logos logo1',1)}<div><b>${esc(t.model)}</b><span class="id">${esc(t.harness_title)}</span>${st?`<span class="mob">${pill(t)}</span>`:''}</div></div>`;
const metric=(t,k)=>k==='model'?t.model:k==='status'?status(t)[1]:k==='llm'?t.status.llm.spent_usd:k==='sim'?simUsd(t):k==='time'?elapsed(t):k==='spent'?t.status.llm.spent_usd+simUsd(t):t.scores[k];
const order=(list,k,dir)=>list.slice().sort((a,b)=>{const x=metric(a,k),y=metric(b,k);return (x==null)-(y==null)||(typeof x==='string'?x.localeCompare(y):(x-y))*dir});
function board(){const rk=ranks();$('board').innerHTML=`<thead><tr><th class="n">#</th><th>Team</th>${sth()}<th class="n">Global</th><th>Budget</th></tr></thead><tbody>${
 order(shown(),'final',-1).map(t=>`<tr><td class="n">${(rk[t.name]||{}).final??'–'}</td><td>${who(t,false,true)}</td><td class="st">${sicon(t)}</td><td class="n"><b>${sc(t.scores.final)}</b></td><td>${budget(t,true)}</td></tr>`).join('')}</tbody>`}
// the enlarged chart goes first, across the column; the two others share the row below
function feature(){const st=document.querySelector('.stack');document.querySelectorAll('.stack>[data-chart]').forEach(c=>{const on=c.dataset.chart===S.feat;
 c.classList.toggle('big',on);c.querySelector('[data-feat]').setAttribute('aria-pressed',on);if(on&&st.firstElementChild!==c)st.prepend(c)})}
