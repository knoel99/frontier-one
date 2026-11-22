"use strict";
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const NF=new Intl.NumberFormat('en-US'), CF=new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:1});
const money=(x,cur='USD')=>x==null?'–':(Math.abs(x)<0.01&&x!==0?x.toFixed(4):NF.format(Math.round(x*100)/100))+(cur?' '+cur:'');
const num=x=>x==null?'–':NF.format(x), compact=x=>x==null?'–':CF.format(x), sc=x=>x==null?'–':x.toFixed(1);
function dur(s){if(s==null||isNaN(s))return '–';s=Math.max(0,Math.round(s));const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60),x=s%60;
 if(d)return `${d} d ${h} h ${String(m).padStart(2,'0')} min`;if(h)return `${h} h ${String(m).padStart(2,'0')} min`;if(m)return `${m} min ${String(x).padStart(2,'0')} s`;return `${x} s`}
const clock=t=>{const d=new Date(t);return isNaN(d)?'':d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})};
const day=t=>{const d=new Date(t);return isNaN(d)?'':d.toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false})};
const S={d:null,hidden:new Set(),cost:{qc:'all',sp:'all'},ax:{qt:'lin',qc:'log'},metric:'final',feat:(()=>{try{return localStorage.getItem('fd.feat')||'qt'}catch(e){return 'qt'}})(),sort:{k:'final',dir:-1},page:0,paused:false,tab:'overview'};
try{S.hidden=new Set(JSON.parse(localStorage.getItem('fd.hidden')||'[]'))}catch(e){}
let TEAMS=[], BY={}, T0=0;
const shown=()=>TEAMS.filter(t=>!S.hidden.has(t.name));
const narrow=()=>innerWidth<640;
const dark=()=>document.documentElement.dataset.theme==='dark'||(document.documentElement.dataset.theme!=='light'&&matchMedia('(prefers-color-scheme: dark)').matches);
const FALLBACK=['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#6250d6','#e34948'];
const col=t=>{const c=t.brand&&t.brand.color;return c?c[dark()?1:0]:FALLBACK[TEAMS.indexOf(t)%FALLBACK.length]};
const dash=t=>(t.brand&&t.brand.dash)||'';
const key=t=>`<span class="key${dash(t).startsWith('2')?' dot':dash(t)?' dash':''}" style="color:${col(t)}"></span>`;
const NAMES={'openai':'OpenAI','anthropic':'Anthropic','claude':'Claude','google':'Google','gemini':'Gemini','xai':'xAI','grok':'Grok','mistral-ai':'Mistral AI',
 'deepseek':'DeepSeek','moonshot-ai':'Moonshot AI','kimi':'Kimi','alibaba':'Alibaba','qwen':'Qwen','zai':'Z.ai','glm':'GLM','cerebras':'Cerebras','cursor':'Cursor'};
const icon=l=>`<svg role="img" aria-label="${esc(NAMES[l]||l)}"><title>${esc(NAMES[l]||l)}</title><use href="#logo-${esc(l)}"/></svg>`;
const logos=(t,cls='logos',n=9)=>(t&&t.logos&&t.logos.length)?`<span class="${cls}">${t.logos.slice(0,n).map(icon).join('')}</span>`:'';
function status(t){if(t.delivered)return['good','Delivered'];const l=t.status.llm;if(l.cap_usd&&l.spent_usd>=l.cap_usd*0.999)return['bad','LLM budget spent'];
 if(t.status.hours_left!=null&&t.status.hours_left<=0)return['bad','Deadline passed'];if(t.running)return['good run',`Session ${t.running.n} running`];
 return (t.activities||[]).some(a=>a.kind==='session')?['','Between sessions']:['','Preparing']}
const pill=t=>{const [c,x]=status(t);return `<span class="pill ${c}">${esc(x)}</span>`};
const ICONS={run:'<path d="M5 3l8 5-8 5z" fill="currentColor"/>',
 done:'<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M4.6 8.3l2.2 2.2 4.6-4.7" stroke="var(--surface)" stroke-width="1.8" fill="none" stroke-linecap="round"/>',
 wait:'<rect x="4" y="3" width="3" height="10" rx="1" fill="currentColor"/><rect x="9" y="3" width="3" height="10" rx="1" fill="currentColor"/>',
 prep:'<path d="M4.5 2h7M4.5 14h7M5.5 2c0 3.5 5 3.5 5 6s-5 2.5-5 6M10.5 2c0 3.5-5 3.5-5 6s5 2.5 5 6" stroke="currentColor" fill="none" stroke-width="1.4"/>',
 budget:'<circle cx="8" cy="8" r="6.3" stroke="currentColor" fill="none" stroke-width="1.7"/><path d="M3.6 12.4l8.8-8.8" stroke="currentColor" stroke-width="1.7"/>',
 deadline:'<circle cx="8" cy="8" r="6.3" stroke="currentColor" fill="none" stroke-width="1.6"/><path d="M8 4.6V8l2.6 1.7" stroke="currentColor" fill="none" stroke-width="1.6" stroke-linecap="round"/>'};
const skey=t=>{const x=status(t)[1];return x==='Delivered'?'done':x==='LLM budget spent'?'budget':x==='Deadline passed'?'deadline':x.startsWith('Session')?'run':x==='Preparing'?'prep':'wait'};
const sicon=t=>{const k=skey(t),x=status(t)[1];return `<span class="si si-${k}" role="img" aria-label="${esc(x)}" tabindex="0" data-tip="${esc(x)}"><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">${ICONS[k]}</svg></span>`};
const SLEGEND='Status\n▶ a session is running (it blinks)\n✓ delivered: time and spend frozen\nⅡ between two sessions\n⧗ preparing, no session yet\n⦸ LLM budget spent\n◷ deadline passed';
const sth=()=>`<th class="st"><span tabindex="0" data-tip="${esc(SLEGEND)}" style="border-bottom:1px dotted currentColor;cursor:help">Status</span></th>`;
const elapsed=t=>t.delivered?t.delivered.elapsed_hours*3600:(t.status.elapsed_hours??0)*3600;
const simUsd=t=>(t.status.cloud.scaleway.spent||0)+(t.status.cloud.runpod.spent||0);
const costOf=(s,k)=>k==='llm'?s.llm_usd:k==='sim'?s.simulation_usd:s.llm_usd+s.simulation_usd;
const COSTL={all:'model calls + simulations',llm:'model calls',sim:'simulations'};
const METRIC={final:['global','final'],science:['scientific quality','quality'],artifacts:['code and data','artifacts'],efficiency:['efficiency','efficiency']};
function ranks(){const r={};for(const k of ['final','science','artifacts','efficiency']){const o=TEAMS.filter(t=>t.scores[k]!=null).sort((a,b)=>b.scores[k]-a.scores[k]);o.forEach((t,i)=>{(r[t.name]=r[t.name]||{})[k]=i+1})}return r}
async function load(){try{const d=window.__STATE__||await (await fetch('api/state',{cache:'no-store'})).json();S.d=d;
 TEAMS=d.teams.slice().sort((a,b)=>a.name.localeCompare(b.name));BY=Object.fromEntries(TEAMS.map(t=>[t.name,t]));
 T0=Math.min(...TEAMS.map(t=>Date.parse(t.status.time)-(t.status.elapsed_hours||0)*3.6e6).filter(isFinite));render();
 $('live').textContent=`${TEAMS.length} teams, ${TEAMS.filter(t=>t.delivered).length} delivered`;document.body.classList.remove('stale')}catch(e){document.body.classList.add('stale')}}
