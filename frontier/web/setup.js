/* setup: the race file as a form generated from the parameters' table, built once (the polling refreshes the status
   lines only), saved, started and stopped from here. Shown when the dashboard runs with --race. */
let SU=null,SUN=0;
const suSec=k=>k.split('.')[0],suKey=k=>k.split('.')[1],suObj=o=>o&&typeof o==='object'&&!Array.isArray(o);
const suGet=(o,k)=>suObj(o)&&suObj(o[suSec(k)])?o[suSec(k)][suKey(k)]:undefined;
function suPut(o,k,v){const s=suSec(k),q=suKey(k);if(v!==undefined){if(!suObj(o[s]))o[s]={};o[s][q]=v;return}
 if(suObj(o[s])&&q in o[s]){delete o[s][q];if(!Object.keys(o[s]).length)delete o[s]}}
function suField(p,id,v){const h=`${id}-h`,d=`aria-describedby="${h}"`,help=`<span class="sub" id="${h}">${esc(p.doc)}${p.required?' (required)':''}</span>`,
 def=p.default==null||(Array.isArray(p.default)&&!p.default.length)?'':[].concat(p.default).join(p.kind==='strs'?'\n':', '),ph=def?` placeholder="${esc(def)}"`:'';
 if(p.kind==='strs'&&p.choices)return `<fieldset class="fld" data-key="${esc(p.key)}" ${d}><legend>${esc(p.key)}</legend><div class="su-checks">${p.choices.map((c,i)=>
  `<label><input type="checkbox" id="${id}-${i}" data-c="${esc(c)}" ${[].concat(v||[]).includes(c)?'checked':''}>${esc(c)}</label>`).join('')}</div>${help}</fieldset>`;
 const opts=p.choices&&[...p.choices,...(v!=null&&!p.choices.includes(v)?[v]:[])];
 const ctl=opts?`<select id="${id}" ${d}><option value="">${def?'default: '+esc(def):'not set'}</option>${opts.map(c=>`<option ${c===v?'selected':''}>${esc(c)}</option>`).join('')}</select>`
  :p.kind==='bool'?`<input type="checkbox" id="${id}" ${d} ${(v??p.default)?'checked':''}>`
  :p.kind==='strs'?`<textarea id="${id}" rows="3" ${d}${ph}>${esc([].concat(v??[]).join('\n'))}</textarea>`
  :`<input type="${p.kind==='int'||p.kind==='float'?'number':'text'}" id="${id}" ${d}${ph}${p.minimum!=null&&p.kind!=='numbers'?` min="${p.minimum}"`:''}${p.kind==='float'?' step="any"':''} value="${esc(Array.isArray(v)?v.join(', '):v??'')}">`;
 return `<div class="fld" data-key="${esc(p.key)}"><label for="${id}">${esc(p.key)}</label>${ctl}${help}</div>`}
/* an empty field is not set: the key is left out and the framework's default applies */
function suRead(p,el,had){const c=el.querySelector('input,select,textarea');
 if(p.kind==='strs'&&p.choices){const v=[...el.querySelectorAll('[data-c]')].filter(x=>x.checked).map(x=>x.dataset.c);return v.length?v:undefined}
 if(p.kind==='bool')return c.checked===p.default&&had===undefined?undefined:c.checked;
 const s=c.value.trim();if(!s)return undefined;
 if(p.kind==='int'||p.kind==='float')return Number(s);
 if(p.kind==='strs')return s.split('\n').map(x=>x.trim()).filter(Boolean);
 if(p.kind==='numbers')return s.split(',').map(x=>x.trim()).filter(Boolean).map(x=>isNaN(x)?x:Number(x));
 return s}
/* a control the operator left as built keeps the file's value untouched: only a changed one is read */
const suState=el=>JSON.stringify([...el.querySelectorAll('input,select,textarea')].map(x=>x.type==='checkbox'?x.checked:x.value)),
 suMark=root=>root.querySelectorAll('[data-key],.su-secret').forEach(el=>el._init=suState(el));
function suFields(root,o){for(const el of root.querySelectorAll('[data-key]')){const p=SU.byKey[el.dataset.key];if(suState(el)!==el._init)suPut(o,p.key,suRead(p,el,suGet(el.closest('.su-runner')?el.closest('.su-runner')._orig:SU.form.race,p.key)))}}
function suRunner(x){const n=++SUN,P=SU.schema,name=suGet(x,'mission.name'),title=name?`Runner ${name}`:'New runner',
 over=P.filter(p=>p.scope==='race'&&suGet(x,p.key)!==undefined).map(p=>p.key),c=document.createElement('div');
 c.className='su-runner';c._orig=x;c.setAttribute('role','group');c.setAttribute('aria-labelledby',`rh-${n}`);
 c.innerHTML=`<div class="chart-head"><h3 id="rh-${n}">${esc(title)}</h3><button data-rm aria-label="Remove ${esc(title)}">Remove</button></div>
 <div class="su-grid">${P.filter(p=>p.scope==='runner').map(p=>suField(p,`r${n}-${p.key}`,suGet(x,p.key))).join('')}</div>
 ${over.length?`<p class="sub su-over">Overrides of the race's values in the file, kept on save: ${esc(over.map(k=>`${k} = ${JSON.stringify(suGet(x,k))}`).join(', '))}</p>`:''}`;
 c.addEventListener('input',e=>{if(e.target.id.endsWith('-mission.name')){const t=e.target.value.trim()?`Runner ${e.target.value.trim()}`:'New runner';c.querySelector('h3').textContent=t;c.querySelector('[data-rm]').setAttribute('aria-label','Remove '+t)}});
 $('su-runners').append(c);suMark(c);return c}
function suSecret(k,v){const n=++SUN,hand=typeof v==='string'&&/^(cmd|file):/.test(v),row=document.createElement('div');row.className='su-secret';row._orig=[k,v];
 row.innerHTML=`<div class="fld"><label for="s${n}-n">Variable</label><input type="text" id="s${n}-n" data-name value="${esc(k)}" placeholder="OPENAI_API_KEY" ${hand?'readonly':''}></div>`+
  (hand?`<p class="sub">Set in the file (${esc(v.split(':')[0])}:…), sent back untouched.</p>`:
  `<div class="fld"><label for="s${n}-k">Source</label><select id="s${n}-k" data-kind><option value="env">environment variable</option><option value="proxy" ${v==='proxy'?'selected':''}>proxy</option></select></div>
  <div class="fld"><label for="s${n}-e">From variable</label><input type="text" id="s${n}-e" data-env value="${esc(typeof v==='string'&&v.startsWith('env:')?v.slice(4):'')}" placeholder="the same name"></div>`)+
  `<button data-rmsec aria-label="Remove the secret ${esc(k||'')}">Remove</button>`;
 $('su-secrets').append(row);row._init=suState(row);return row}
function suBuild(){const d=SU,r=suObj(d.race)?d.race:{},R=suObj(r.race)?r.race:{},svc=new Set(d.services),race=d.schema.filter(p=>p.scope==='race'),
 tiers=SU.byKey['harness.tier'].choices,fld=(id,k,doc,ph)=>`<div class="fld"><label for="${id}">race.${k}</label><input type="text" id="${id}" value="${esc(R[k]??'')}" ${ph?`placeholder="${esc(ph)}"`:''} aria-describedby="${id}-h"><span class="sub" id="${id}-h">${esc(doc)}</span></div>`;
 $('p-setup').innerHTML=`<div class="card"><div class="chart-head"><div><h2>Setup</h2><p class="sub">The race file <code>${esc(d.file)}</code>. An empty field is not set: the framework's default, shown greyed, applies. Saving rewrites the file: its comments are lost.</p></div>
 <div class="ctrls"><button id="su-save">Save</button><button id="su-start">Launch</button><button id="su-stop">Stop</button></div></div>
 <div id="su-msg"></div><ul class="su-status" id="su-status" aria-label="Runners"></ul></div>
 <div class="card"><h2>Race</h2><div class="su-grid">${fld('su-name','name',"folder of the race: letters, digits, '.', '-' and '_'")}${fld('su-root','root',"folder of the runners' states and workspaces, relative to the race file",'~/.frontier/races/<name>')}</div>
 ${[...new Set(race.map(p=>suSec(p.key)))].map(s=>`<details class="su-sec" ${s in r||s==='mission'||s==='llm'?'open':''}><summary>[${esc(s)}]</summary>${svc.has(s)?`<label class="su-on"><input type="checkbox" data-on="${esc(s)}" ${s in r?'checked':''}> use ${esc(s)} in this race</label>`:''}
 <div class="su-grid">${race.filter(p=>suSec(p.key)===s).map(p=>suField(p,'f-'+p.key,suGet(r,p.key))).join('')}</div></details>`).join('')}</div>
 <div class="card"><div class="chart-head"><div><h2>Runners</h2><p class="sub">One per team: what differs from the race, merged over it key by key.</p></div><button id="su-add">Add runner</button></div><div id="su-runners"></div></div>
 <div class="card"><div class="chart-head"><div><h2>Secrets</h2><p class="sub">Where each key is read on this machine when a runner starts; no value is ever written here.</p></div><button id="su-addsec">Add secret</button></div><div id="su-secrets"></div></div>
 <details class="card" id="su-fw"><summary><h2>Framework configuration</h2></summary><p class="sub">Read-only: config/providers.toml and config/harnesses.toml.</p>
 <div class="scroll"><table><thead><tr><th>Provider</th>${tiers.map(t=>`<th>${esc(t)}</th>`).join('')}</tr></thead><tbody>${Object.entries(d.providers).map(([n,t])=>`<tr><td>${esc(n)}</td>${tiers.map(x=>`<td>${esc(t[x]||'–')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
 <p class="sub">Harnesses that can be launched: ${esc(SU.byKey['harness.name'].choices.join(', '))}.</p></details>`;
 SU.form={race:d.race,version:d.version};suMark($('p-setup'));
 (Array.isArray(r.runners)?r.runners:[]).forEach(x=>suRunner(suObj(x)?x:{}));Object.entries(suObj(r.secrets)?r.secrets:{}).forEach(([k,v])=>suSecret(k,v));suStatus()}
function suCollect(){const r=structuredClone(suObj(SU.form.race)?SU.form.race:{}),runners=[...$('su-runners').children].map(c=>{const x=structuredClone(c._orig);suFields(c,x);return x});
 delete r.runners;r.race=suObj(r.race)?r.race:{};for(const k of ['name','root']){const el=$('su-'+k),v=el.value.trim();if(el.value===el.defaultValue)continue;if(v)r.race[k]=v;else delete r.race[k]}
 for(const s of $('p-setup').querySelectorAll('.su-sec'))suFields(s,r);
 for(const c of $('p-setup').querySelectorAll('[data-on]'))if(c.checked)r[c.dataset.on]=suObj(r[c.dataset.on])?r[c.dataset.on]:{};else delete r[c.dataset.on];
 const sec={};for(const row of $('su-secrets').children){const k=row.querySelector('[data-name]').value.trim();if(suState(row)===row._init){if(row._orig[0])sec[row._orig[0]]=row._orig[1];continue}
  if(k)sec[k]=row.querySelector('[data-kind]').value==='proxy'?'proxy':'env:'+(row.querySelector('[data-env]').value.trim()||k)}
 if(Object.keys(sec).length)r.secrets=sec;else delete r.secrets;r.runners=runners;return r}
function suSay(problems,ok){$('su-msg').innerHTML=problems.length?`<ul class="su-problems">${problems.map(p=>`<li>${esc(p)}</li>`).join('')}</ul>`:`<p class="sub">${esc(ok)}</p>`;
 $('live').textContent=problems.length?`${problems.length} problem${problems.length>1?'s':''}: ${problems.join(' ')}`:ok}
function suStatus(){const st=Object.entries(SU.status||{});$('su-status').innerHTML=st.length?st.map(([n,x])=>`<li><b>${esc(n)}</b>: ${x.running?`running (pid ${x.pid})`:'not running'}${x.delivered?', delivered':''}</li>`).join(''):'<li>No runner in the saved file.</li>'}
const suPost=(path,body)=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}).then(async r=>[r.status,await r.json()]);
const suTake=d=>{SU=Object.assign(d,{byKey:SU.byKey,form:SU.form});suStatus()};
async function suClick(e){const b=e.target.closest('button');if(!b)return;
 if(b.id==='su-add')suRunner({}).querySelector('input,select').focus();
 else if(b.dataset.rm!=null){b.closest('.su-runner').remove();$('su-add').focus()}
 else if(b.id==='su-addsec')suSecret('','env:').querySelector('input').focus();
 else if(b.dataset.rmsec!=null){b.closest('.su-secret').remove();$('su-addsec').focus()}
 else if(b.id==='su-save'){b.disabled=true;try{const [code,d]=await suPost('api/setup',{race:suCollect(),version:SU.form.version});if(code===200){suTake(d);suBuild();$('su-save').focus();suSay([],'Saved')}else suSay(d.problems||[`refused (${code})`])}finally{b.disabled=false}}
 else if(b.id==='su-start'){const R=SU.race;if(!suObj(R)||SU.problems.length)return suSay(SU.problems.length?SU.problems:['Save the race first.']);
  const each=(Array.isArray(R.runners)?R.runners:[]).map(x=>[suGet(x,'mission.name'),suGet(x,'llm.budget_usd')??suGet(R,'llm.budget_usd')]),total=each.reduce((a,[,u])=>a+(u||0),0);
  if(!confirm(`Launch the saved race: ${each.length} runner${each.length===1?'':'s'}, LLM budget ${each.map(([n,u])=>`${n} ${u==null?'no cap':money(u)}`).join(', ')}; ${money(total)} in total.`))return;
  const [code,d]=await suPost('api/race/start');if(d.status){SU.status=d.status;suStatus()}
  suSay(code===200?[]:d.problems,code===200?Object.entries(d.result).map(([n,v])=>`${n}: ${v}`).join('; '):'')}
 else if(b.id==='su-stop'){if(!confirm('Stop every runner? Each launcher ends its session and deletes the machines it left alive.'))return;
  const [code,d]=await suPost('api/race/stop');if(d.status){SU.status=d.status;suStatus()}
  suSay(code===200?[]:d.problems,code===200?Object.entries(d.result).map(([n,v])=>`${n}: ${v}`).join('; '):'')}}
function setupView(){if(!$('su-name'))suBuild();fetch('api/setup',{cache:'no-store'}).then(r=>r.json()).then(suTake).catch(()=>{})}
if(window.__SETUP__)fetch('api/setup',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(d=>{if(!d)return;SU=d;SU.byKey=Object.fromEntries(d.schema.map(p=>[p.key,p]));
 $('tabs').insertAdjacentHTML('beforeend','<button role="tab" id="tab-setup" data-group="setup" aria-controls="p-setup" aria-selected="false" tabindex="-1">Setup</button>');
 $('main').insertAdjacentHTML('beforeend','<section role="tabpanel" id="p-setup" aria-labelledby="tab-setup" hidden></section>');
 $('p-setup').addEventListener('click',suClick);GROUPS.setup=['setup'];VIEWS.setup=setupView;if(location.hash==='#setup')show('setup')}).catch(()=>{});
