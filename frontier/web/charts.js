/* tooltip */
const tip=$('tip');
function showTip(text,x,y){tip.textContent='';text.split('\n').forEach((l,i)=>{const e=document.createElement(i?'div':'b');e.textContent=l;tip.appendChild(e)});tip.style.display='block';
 const r=tip.getBoundingClientRect();tip.style.left=Math.max(8,Math.min(innerWidth-r.width-8,x+14))+'px';tip.style.top=Math.min(innerHeight-r.height-8,y+14)+'px'}
const hideTip=()=>{tip.style.display='none';document.querySelectorAll('.chart.hl').forEach(c=>{c.classList.remove('hl');c.querySelectorAll('.ln.on').forEach(l=>l.classList.remove('on'))})};
function hoverAt(e){const el=e.target.closest('[data-tip]');if(!el||e.target.closest('.xh'))return false;showTip(el.dataset.tip,e.clientX,e.clientY);
 const g=el.dataset.team,c=el.closest('.chart');if(g&&c){c.classList.add('hl');c.querySelectorAll('.ln').forEach(l=>l.classList.toggle('on',l.dataset.team===g))}return true}
document.addEventListener('pointermove',e=>{if(!hoverAt(e)&&!e.target.closest('.xh'))hideTip()});
document.addEventListener('pointerdown',e=>{if(e.pointerType!=='mouse'&&!hoverAt(e))hideTip()});  // a tap shows what a hover would
document.addEventListener('focusin',e=>{const el=e.target.closest('[data-tip]');if(el){const r=el.getBoundingClientRect();showTip(el.dataset.tip,r.left,r.bottom)}});
document.addEventListener('focusout',hideTip);

/* scales and frame */
function nice(x){const p=Math.pow(10,Math.floor(Math.log10(x||1))),f=x/p;return (f<1.5?1:f<3?2:f<7?5:10)*p}
function lin(d0,d1,r0,r1){const f=v=>r0+(r1-r0)*(v-d0)/((d1-d0)||1);f.d=[d0,d1];f.ticks=n=>{const st=nice((d1-d0)/n),o=[];for(let v=Math.ceil(d0/st)*st;v<=d1+st*1e-9;v+=st)o.push(+v.toFixed(10));return o};return f}
function logs(d0,d1,r0,r1){const a=Math.log10(d0),b=Math.log10(d1),f=v=>r0+(r1-r0)*(Math.log10(Math.max(v,d0))-a)/((b-a)||1);
 f.ticks=()=>{const o=[];for(let e=Math.floor(a);e<=Math.ceil(b);e++)for(const m of [1,2,5]){const v=m*Math.pow(10,e);if(v>=d0*0.999&&v<=d1*1.001)o.push(v)}return o};f.d=[d0,d1];f.log=1;return f}
// time axes in hours tick on clock steps (5 min, 1 h, 6 h, 1 d…), not on decimal ones
const TSTEPS=[1,5,10,15,30,60,120,180,360,720,1440,2880,4320,10080].map(m=>m/60);
function timeTicks(X,n){const [d0,d1]=X.d;if(X.log)return [5,15,30,60,180,360,720,1440,2880,4320,10080].map(m=>m/60).filter(v=>v>=d0*0.999&&v<=d1*1.001);
 const st=TSTEPS.find(t=>t>=(d1-d0)/n)||TSTEPS.at(-1),o=[];for(let v=Math.ceil(d0/st)*st;v<=d1+1e-9;v+=st)o.push(v);return o}
const W0=el=>Math.max(300,el.clientWidth||600);
function frame(el,h,{x,y,xt,yt,xl}){const w=W0(el),P={l:40,r:46,t:12,b:36},n=w<480?4:6;
 const X=x(P.l,w-P.r),Y=y(h-P.b,P.t);let s=`<svg viewBox="0 0 ${w} ${h}" height="${h}" role="img" aria-label="${esc(el.dataset.label||'')}">`;
 for(const v of Y.ticks(5))s+=`<line class="grid" x1="${P.l}" x2="${w-P.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${P.l-8}" y="${Y(v)+4}" text-anchor="end">${yt(v)}</text>`;
 const xs=xt===HT?timeTicks(X,n):X.ticks(n),step=Math.ceil(xs.length/(n+1));xs.forEach((v,i)=>{if(i%step===0)s+=`<text x="${X(v)}" y="${h-P.b+18}" text-anchor="middle">${xt(v)}</text>`});
 s+=`<line class="axis" x1="${P.l}" x2="${w-P.r}" y1="${h-P.b}" y2="${h-P.b}"/>`;if(xl)s+=`<text x="${w-P.r}" y="${h-3}" text-anchor="end">${esc(xl)}</text>`;
 return {w,h,P,X,Y,s,ends:[]}}
const path=(pts,X,Y,step)=>pts.map((p,i)=>{const x=X(p[0]),y=Y(p[1]);return !i?`M${x},${y}`:step?`H${x}V${y}`:`L${x},${y}`}).join('');
function line(f,t,P,tipText,step){const c=col(t),d=dash(t),pd=path(P,f.X,f.Y,step),last=P[P.length-1];
 f.ends.push({t,c,tip:tipText,x:f.X(last[0]),y:f.Y(last[1])});
 return `<g class="ln" data-team="${esc(t.name)}"><path d="${pd}" fill="none" stroke="${c}" stroke-width="2.2" ${d?`stroke-dasharray="${d}"`:''}/><circle cx="${f.X(last[0])}" cy="${f.Y(last[1])}" r="3.5" fill="${c}"/></g>`+
 `<path class="hit" d="${pd}" data-team="${esc(t.name)}" data-tip="${esc(tipText)}"/>`}
/* company logos in a column right of the plot, at the height of each line's end, spread apart, tied by a dotted leader */
function ends(f){const lo=f.P.t,hi=f.h-f.P.b,B=f.ends.sort((a,b)=>a.y-b.y),gap=Math.min(23,(hi-lo)/Math.max(1,B.length-1)),R=Math.max(6,Math.min(10,gap/2-1));for(const b of B)b.by=b.y;
 for(let i=1;i<B.length;i++)B[i].by=Math.max(B[i].by,B[i-1].by+gap);
 if(B.length&&B[B.length-1].by>hi){B[B.length-1].by=hi;for(let i=B.length-2;i>=0;i--)B[i].by=Math.min(B[i].by,B[i+1].by-gap)}
 const lx=f.w-f.P.r+24;let s='';
 for(const b of B){const n=esc(b.t.name),g=b.t.logos&&b.t.logos[0];
  s+=`<g class="ln" data-team="${n}"><path class="lead" d="M${b.x+5},${b.y}L${lx-R-5},${b.by}"/></g><g data-team="${n}" data-tip="${esc(b.tip)}"><circle class="badge" cx="${lx}" cy="${b.by}" r="${R}" stroke="${b.c}" stroke-width="2"/>`+
   (g?`<use href="#logo-${esc(g)}" x="${lx-R*0.7}" y="${b.by-R*0.7}" width="${R*1.4}" height="${R*1.4}" style="color:var(--ink)"/>`:'')+`<circle cx="${lx}" cy="${b.by}" r="${R+6}" fill="transparent"/></g>`}
 return s}
function gantt(el,acts,{by,win,colorBy}){const now=Date.parse(S.d.time);if(!acts.length){el.innerHTML='<div class="empty">Nothing to show</div>';return}
 const t0=win?now-win:Math.min(...acts.map(a=>Date.parse(a.start))),span=Math.max(now-t0,1000),rows={};
 for(const a of acts){if((a.end?Date.parse(a.end):now)<t0)continue;const r=by==='team'?`${BY[a.team]?BY[a.team].model:a.team} · ${a.kind}`:by==='kind'?a.kind:by==='role'?(a.role||a.kind):a.provider;(rows[r]=rows[r]||[]).push(a)}
 const keys=Object.keys(rows).sort(),w=Math.max(720,W0(el)),L=200,RH=24,h=keys.length*RH+30,X=t=>L+(w-L-12)*(t-t0)/span;
 let s=`<svg viewBox="0 0 ${w} ${h}" height="${h}" role="img" aria-label="Activity timeline"><defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line y2="6" stroke="var(--surface)" stroke-width="2"/></pattern></defs>`;
 for(let i=0;i<=6;i++){const tt=t0+span*i/6,x=X(tt);s+=`<line class="grid" x1="${x}" x2="${x}" y1="0" y2="${h-22}"/><text x="${x}" y="${h-6}" text-anchor="middle">${esc(day(tt))}</text>`}
 keys.forEach((k,r)=>{const y=r*RH;s+=`<text class="lab" x="4" y="${y+16}">${esc(k.length>30?k.slice(0,29)+'…':k)}</text>`;
  for(const a of rows[k]){const x0=X(Math.max(Date.parse(a.start),t0)),x1=X(a.end?Date.parse(a.end):now),c=colorBy(a);
   const d=`${BY[a.team]?BY[a.team].model:a.team} · ${a.kind}${a.role?' ('+a.role+')':''} · ${a.label}\n${day(a.start)} → ${a.end?day(a.end):'running'} (${dur(((a.end?Date.parse(a.end):now)-Date.parse(a.start))/1000)})\n${a.detail||''}`;
   s+=`<rect x="${x0}" y="${y+5}" width="${Math.max(x1-x0-1,3)}" height="${RH-10}" rx="3" fill="${c}" data-tip="${esc(d)}"/>`;
   if(!a.end)s+=`<rect x="${x0}" y="${y+5}" width="${Math.max(x1-x0-1,3)}" height="${RH-10}" rx="3" fill="url(#hatch)" pointer-events="none"/>`}});
 el.innerHTML=s+'</svg>'}
