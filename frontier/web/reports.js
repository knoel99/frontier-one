/* reports: fetched for the team shown only, while the tab is open */
let RQ=null,RK='';
async function dl(url,name){$('rerr').textContent='Compiling…';try{const r=await fetch(url,{cache:'no-store'});
 if(!r.ok){$('rerr').textContent=(await r.text()).trim();return}const a=document.createElement('a');a.href=URL.createObjectURL(await r.blob());a.download=name;a.click();
 setTimeout(()=>URL.revokeObjectURL(a.href),10000);$('rerr').textContent=''}catch(e){$('rerr').textContent='The PDF could not be fetched.'}}
const enc=p=>p.split('/').map(encodeURIComponent).join('/');
// the three formats: Markdown rendered, LaTeX as source, and PDFs (the LaTeX compiled on request, the team's own, the milestones')
function pdfList(r,cur){const T=encodeURIComponent(cur);return [
 ...(r.files||[]).filter(f=>f.kind==='tex').map(f=>({id:'tex:'+f.path,label:`compiled from ${f.path}`,url:`texpdf/${T}/${enc(f.path.slice(0,-4))}.pdf`,f})),
 ...(r.pdfs||[]).filter(p=>!p.startsWith('snapshots/')).map(p=>({id:p,label:p,url:`report/${T}/${enc(p)}`})),
 ...(r.pdfs||[]).filter(p=>p.startsWith('snapshots/')).map(p=>({id:p,label:`milestone ${p.slice(10,-4)}: what the jury read`,url:`report/${T}/${enc(p)}`}))]}
function reports(){const rt=$('rteam'),cur=S.rteam&&BY[S.rteam]?S.rteam:(TEAMS[0]||{}).name,mode=S.rv||'md';S.rteam=cur;
 if(rt.childElementCount!==TEAMS.length)rt.innerHTML=TEAMS.map(t=>`<button data-rteam="${esc(t.name)}" aria-pressed="false" aria-label="${esc(t.model)} · ${esc(t.harness_title)}" data-tip="${esc(t.model)}\n${esc(t.harness_title)}">${t.logos&&t.logos[0]?icon(t.logos[0]):''}<span>${esc(t.model)}</span></button>`).join('');
 rt.querySelectorAll('[data-rteam]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.rteam===cur));
 document.querySelectorAll('.rtabs [role=tab]').forEach(b=>{const on=b.dataset.rv===mode;b.setAttribute('aria-selected',on);b.tabIndex=on?0:-1});$('rbody').setAttribute('aria-labelledby','rv-'+mode);
 if(!cur||RQ)return;
 RQ=fetch('api/report?team='+encodeURIComponent(cur),{cache:'no-store'}).then(r=>r.json()).then(r=>{RQ=null;if(cur!==S.rteam||mode!==(S.rv||'md'))return reports();
  const rf=$('rfile'),f0=rf.value,b=$('rbody');
  const items=mode==='pdf'?pdfList(r,cur):(r.files||[]).filter(f=>mode==='tex'?f.kind==='tex':f.kind!=='tex').map(f=>({id:f.path,label:f.path,f}));
  if(rf.options.length!==items.length||[...rf.options].some((o,k)=>o.value!==items[k].id))rf.innerHTML=items.map(x=>`<option value="${esc(x.id)}"${x.id===f0?' selected':''}>${esc(x.label)}</option>`).join('');
  $('rfilel').hidden=items.length<2;const it=items.find(x=>x.id===rf.value)||items[0],f=it&&it.f;
  $('rmeta').textContent=!it?'':mode==='pdf'?(f?`LaTeX written ${ago(f.written)} · `+(f.compiled?`PDF compiled ${ago(f.compiled.at)}`+(f.compiled.current?'':', from an earlier version; updating…'):'PDF compiling…'):it.label):`${f.path} · written ${ago(f.written)} · ${num(f.text.length)} characters`;
  $('rpdf').innerHTML=it&&mode==='pdf'?`<a href="${it.url}" target="_blank" rel="noopener">Open in a new tab</a> <button data-dl="${it.url}" data-name="${esc(cur)}-${esc(it.url.split('/').pop())}">Download</button>`:'';
  const key=[cur,mode,it&&it.id,f&&f.written].join('|');if(key===RK){if(mode==='pdf'&&f&&!(f.compiled&&f.compiled.current))setTimeout(()=>{if(S.tab==='reports')reports()},1500);return}RK=key;
  if(!it){b.innerHTML=`<div class="empty">${mode==='pdf'?'No PDF yet: no LaTeX report and no milestone PDF.':mode==='tex'?'No LaTeX report yet.':'No Markdown report yet: the agent has not written one.'}</div>`;return}
  if(mode==='pdf'){const fr=b.querySelector('iframe')||(b.innerHTML='<iframe title="The report as a PDF"></iframe>',b.querySelector('iframe'));
   fr.onload=()=>{if(S.tab==='reports')reports()};fr.src=it.url;return}
  const top=b.scrollTop;b.innerHTML=mode==='tex'?`<pre>${esc(f.text)}</pre>`:`<div class="md">${md(f.text)}</div>`;b.scrollTop=top}).catch(()=>{RQ=null})}
