/* team picker: one popover of checkboxes, "only" to keep one */
function picker(){const n=shown().length;$('pk-n').textContent=n===TEAMS.length?`all ${n}`:`${n} of ${TEAMS.length}`;
 $('pk-faces').innerHTML=shown().slice(0,narrow()?5:9).map(t=>t.logos&&t.logos[0]?icon(t.logos[0]):'').join('')+(narrow()&&n>5?`<span class="sub" style="margin:0">+${n-5}</span>`:'');
 if($('picker').open)return;  // never rebuild the list under the reader's pointer
 $('pk-list').innerHTML=TEAMS.map((t,i)=>`<div class="row"><input type="checkbox" id="pk-${i}" data-pick="${esc(t.name)}" ${S.hidden.has(t.name)?'':'checked'}><label for="pk-${i}">${who(t)}</label><button class="only" data-only="${esc(t.name)}" aria-label="Show only ${esc(t.model)}">only</button></div>`).join('')}
function setHidden(h){S.hidden=h;try{localStorage.setItem('fd.hidden',JSON.stringify([...h]))}catch(x){}
 document.querySelectorAll('[data-pick]').forEach(c=>c.checked=!h.has(c.dataset.pick));render()}
function head(){const now=Date.parse(S.d.time),dl=Math.max(...TEAMS.map(t=>Date.parse(t.status.deadline||'')||0));
 $('clock').textContent=T0?`${dur((now-T0)/1000)} into the race${dl?` · ${dur((dl-now)/1000)} to the deadline`:''} · updated ${clock(S.d.time)}`:''}
/* tabs: only the shown one is drawn */
const VIEWS={overview:()=>{feature();board();jline();chartQT();chartQC();chartSP()},teams:()=>{board2();cards()},activity:()=>{chartGantt();chartModels()},subagents:subagents,jury:()=>{panelView();archiveView();jury()},feed:feed,reports:reports,about:()=>{}};
function focusKey(f){if(!f||f===document.body)return null;if(f.id)return '#'+CSS.escape(f.id);if(f.tagName==='SUMMARY'&&f.parentElement.id)return '#'+CSS.escape(f.parentElement.id)+'>summary';
 for(const a of ['sort','pg','jpg','ax','v','only','pick','feat','rteam','dl','judge'])if(f.dataset[a]!=null)return `[data-${a}="${CSS.escape(f.dataset[a])}"]`;return null}
/* a table that can scroll sideways is reachable by keyboard, named after its card */
function scrollables(){document.querySelectorAll('.scroll:not([tabindex])').forEach(e=>{const h=e.closest('.card')&&e.closest('.card').querySelector('h2');
 e.tabIndex=0;e.setAttribute('role','region');e.setAttribute('aria-label',(h?h.textContent:'Table')+', scrollable')})}
function render(){if(!S.d)return;const open=new Set([...document.querySelectorAll('details[id][open]')].map(e=>e.id)),fk=focusKey(document.activeElement),
 scroll=[...document.querySelectorAll('.team details>div,.feed')].map(e=>[e.closest('[id]').id,e.scrollTop]);
 head();picker();VIEWS[S.tab]();scrollables();
 for(const id of open){const e=$(id);if(e)e.open=true}
 for(const [id,y] of scroll){const h=$(id),e=h&&(h.matches('.feed')?h:h.querySelector('div'));if(e)e.scrollTop=y}
 if(fk&&document.activeElement===document.body){const e=document.querySelector(fk);if(e)e.focus({preventScroll:true})}}
/* views: a tab per group, and a sub-tab per view in a group of several */
const GROUPS={overview:['overview'],competitors:['teams'],results:['reports'],evaluation:['jury'],analysis:['activity','subagents','feed'],about:['about']};
const SUBNAME={activity:'Timeline',subagents:'Sub-agents',feed:'Event log'};
const groupOf=tab=>Object.keys(GROUPS).find(g=>GROUPS[g].includes(tab));
function show(tab,focus){S.tab=tab;const g=groupOf(tab),views=GROUPS[g];S.sub=S.sub||{};S.sub[g]=tab;
 document.querySelectorAll('#tabs [role=tab]').forEach(b=>{const on=b.dataset.group===g;b.setAttribute('aria-selected',on);b.tabIndex=on?0:-1;
  b.setAttribute('aria-controls','p-'+(S.sub[b.dataset.group]||GROUPS[b.dataset.group][0]));if(on){if(focus==='top')b.focus();b.scrollIntoView({block:'nearest',inline:'nearest'})}});
 const st=$('subtabs');st.hidden=views.length<2;st.setAttribute('aria-label',$('tab-'+g).textContent+' views');
 st.innerHTML=views.length<2?'':views.map(v=>`<button role="tab" id="sub-${v}" data-sub="${v}" aria-controls="p-${v}" aria-selected="${v===tab}" tabindex="${v===tab?0:-1}">${SUBNAME[v]}</button>`).join('');
 if(focus==='sub')$('sub-'+tab).focus();
 document.querySelectorAll('section[role=tabpanel]').forEach(p=>p.hidden=p.id!=='p-'+tab);if(location.hash!=='#'+tab)history.replaceState(null,'',location.pathname+'#'+tab);render()}
document.addEventListener('keydown',e=>{const list=e.target.closest('[role=tablist]');if(!list||e.target.getAttribute('role')!=='tab')return;
 const tabs=[...list.querySelectorAll('[role=tab]')],i=tabs.indexOf(e.target);
 const j=e.key==='ArrowRight'?(i+1)%tabs.length:e.key==='ArrowLeft'?(i-1+tabs.length)%tabs.length:e.key==='Home'?0:e.key==='End'?tabs.length-1:-1;
 if(j<0)return;e.preventDefault();const b=tabs[j];if(b.dataset.rv){b.click();b.focus();return}b.dataset.group?show(S.sub&&S.sub[b.dataset.group]||GROUPS[b.dataset.group][0],'top'):show(b.dataset.sub,'sub')});
document.addEventListener('click',e=>{const g=e.target.closest('[data-goto]');if(g){e.preventDefault();show(g.dataset.goto);scrollTo(0,0);return}
 const b=e.target.closest('button');
 if(!e.target.closest('#picker')&&$('picker').open)$('picker').open=false;if(!b)return;
 if(b.getAttribute('role')==='tab'&&!b.dataset.rv)b.dataset.group?show(S.sub&&S.sub[b.dataset.group]||GROUPS[b.dataset.group][0]):show(b.dataset.sub);
 else if(b.dataset.only)setHidden(new Set(TEAMS.map(t=>t.name).filter(n=>n!==b.dataset.only)));
 else if(b.id==='pk-all')setHidden(new Set());else if(b.id==='pk-none')setHidden(new Set(TEAMS.map(t=>t.name)));
 else if(b.dataset.ax){S.ax[b.dataset.ax]=b.dataset.v;document.querySelectorAll(`[data-ax="${b.dataset.ax}"]`).forEach(x=>x.setAttribute('aria-pressed',x===b));b.dataset.ax==='qt'?chartQT():chartQC()}
 else if(b.dataset.rv){S.rv=b.dataset.rv;RK='';$('rfile').innerHTML='';$('rerr').textContent='';reports()}
 else if(b.dataset.rteam){S.rteam=b.dataset.rteam;RK='';$('rerr').textContent='';reports()}
 else if(b.dataset.dl)dl(b.dataset.dl,b.dataset.name);
 else if(b.id==='evaluate'){b.disabled=true;fetch('api/evaluate',{method:'POST'}).then(()=>load())}
 else if(b.dataset.judge){const j=b.dataset.judge;S.pj=S.pj.includes(j)?S.pj.filter(x=>x!==j):[...S.pj,j].sort();S.pjDirty=true;panelView()}
 else if(b.id==='panelreset'){S.pjDirty=false;panelView()}
 else if(b.id==='panelsave'){b.disabled=true;fetch('api/panel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({judges:S.pj})}).then(r=>r.json()).then(r=>{if(r.error)$('jwarn').textContent=r.error;else S.pjDirty=false;load()})}
 else if(b.dataset.feat){S.feat=b.dataset.feat;try{localStorage.setItem('fd.feat',S.feat)}catch(e){}VIEWS.overview();b.focus()}
 else if(b.dataset.sort){const k=b.dataset.sort;S.sort={k,dir:S.sort.k===k?-S.sort.dir:(k==='model'||k==='status'?1:-1)};board2()}
 else if(b.dataset.pg){S.page+=+b.dataset.pg;subagents()}
 else if(b.dataset.jpg){S.jpage=(S.jpage||0)+ +b.dataset.jpg;jury()}
 else if(b.id==='pause'){S.paused=!S.paused;b.setAttribute('aria-pressed',S.paused);b.textContent=S.paused?'Resume':'Pause';$('dot').classList.toggle('paused',S.paused)}});
document.addEventListener('change',e=>{const c=e.target;if(c.dataset.pick){const h=new Set(S.hidden);c.checked?h.delete(c.dataset.pick):h.add(c.dataset.pick);setHidden(h)}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){hideTip();if($('picker').open){$('picker').open=false;$('picker').querySelector('summary').focus()}}});
document.querySelectorAll('select[data-cost]').forEach(s=>s.onchange=()=>{S.cost[s.dataset.cost]=s.value;s.dataset.cost==='qc'?chartQC():chartSP()});
$('metric').onchange=e=>{S.metric=e.target.value;chartQT();chartQC()};
for(const id of ['gby','gkind','gwin'])$(id).onchange=chartGantt;for(const id of ['msrc','mmode','mwin'])$(id).onchange=chartModels;
$('tsort').onchange=cards;$('rfile').onchange=()=>{RK='';reports()};
$('jwin').onchange=jury;$('fkind').onchange=feed;for(const id of ['steam','srole'])$(id).onchange=()=>{S.page=0;subagents()};
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',render);
/* theme: auto (the device's), light or dark, kept per browser */
const THEMES={auto:'◐ Auto',light:'☀ Light',dark:'☾ Dark'};
function theme(t){const r=document.documentElement;if(t==='auto')delete r.dataset.theme;else r.dataset.theme=t;try{localStorage.setItem('fd.theme',t)}catch(e){}
 const b=$('theme');b.textContent=THEMES[t];b.setAttribute('aria-label',`Theme: ${t}. Change it`);render()}
$('theme').onclick=()=>{const cur=document.documentElement.dataset.theme||'auto';theme(cur==='auto'?'light':cur==='light'?'dark':'auto')};
theme(document.documentElement.dataset.theme||'auto');
let rs;new ResizeObserver(()=>{clearTimeout(rs);rs=setTimeout(()=>{if(S.d)VIEWS[S.tab]()},150)}).observe($('main'));
document.querySelectorAll('[data-icon]').forEach(e=>e.innerHTML=`<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">${ICONS[e.dataset.icon]}</svg>`);
const first=location.hash.slice(1);if(VIEWS[first])show(first);
load();if(!window.__STATE__)setInterval(()=>{if(!S.paused&&document.visibilityState==='visible')load()},5000);
