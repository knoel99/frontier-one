/* jury */
const ago=t=>t?dur((Date.parse(S.d.time)-Date.parse(t))/1000)+' ago':'never';
const judgeCol=j=>j&&j.color?j.color[dark()?1:0]:'var(--mute)';
function jline(){const J=S.d.jury||{},v=J.verdicts||[];$('jline').innerHTML=v.length?`Jury: ${num(v.length)} verdicts by ${J.judges.length} judges, the last ${esc(ago(v[v.length-1].t))} · ${money(J.usd)}${J.cap?` of ${J.cap} USD`:''} · <a href="#jury" data-goto="jury">see the jury</a>`:'Jury: no verdict yet.'}
function jury(){const J=S.d.jury||{judges:[],verdicts:[]},names=new Set(shown().map(t=>t.name)),V=J.verdicts.filter(v=>names.has(v.team)),now=Date.parse(S.d.time);
 const snaps=TEAMS.flatMap(t=>t.snapshots||[]),done=snaps.filter(x=>x.quality!=null).length,JB=Object.fromEntries(J.judges.map(j=>[j.provider,j]));
 $('jfacts').innerHTML=[[num(J.verdicts.length),'verdicts'],[`${done} / ${snaps.length}`,'snapshots graded'],[money(J.usd),J.cap?`spent of ${J.cap} USD`:'spent'],
  [J.verdicts.length?ago(J.verdicts[J.verdicts.length-1].t):'–','since the last verdict']].map(([v,l])=>`<div><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`).join('');
 $('jjudges').innerHTML=`<thead><tr><th>Judge</th><th class="n">Verdicts</th><th class="n">Calls</th><th class="n">USD</th><th>Last call</th></tr></thead><tbody>${J.judges.map(j=>`<tr><td><div class="who"><span class="key" style="color:${judgeCol(j)}"></span>${j.logos&&j.logos[0]?`<span class="logos logo1">${icon(j.logos[0])}</span>`:''}<div><b>${esc(j.model)}</b><span class="id">${esc(j.provider)}</span></div></div></td>
 <td class="n">${num(J.verdicts.filter(v=>v.judge===j.provider).length)}</td><td class="n">${num(j.calls)}</td><td class="n">${money(j.usd,'')}</td><td>${esc(ago(j.last))}</td></tr>`).join('')}</tbody>`;
 const win=+$('jwin').value*1000,acts=V.filter(v=>!win||Date.parse(v.t)>=now-win).map(v=>({team:v.team,kind:'verdict',provider:`judge · ${v.model}`,judge:v.judge,label:v.label,
  start:new Date(Date.parse(v.t)-v.seconds*1000).toISOString(),end:v.t,detail:`science ${sc(v.scientific_quality)} · code and data ${sc(v.artifacts)} · ${money(v.usd)}`}));
 gantt($('c-jury'),acts,{by:'provider',win,colorBy:a=>judgeCol(JB[a.judge])});
 const N=25,pages=Math.max(1,Math.ceil(V.length/N));S.jpage=Math.min(S.jpage||0,pages-1);const rows=V.slice().reverse().slice(S.jpage*N,S.jpage*N+N);
 $('jsum').textContent=`${V.length} verdicts on the teams shown, latest first.`;
 $('jtable').innerHTML=`<thead><tr><th>Time</th><th>Judge</th><th>Team graded</th><th>Snapshot</th><th class="n">Science</th><th class="n">Code and data</th><th class="n">Time taken</th><th class="n">USD</th></tr></thead><tbody>${rows.map(v=>`<tr><td>${esc(day(v.t))}</td>
 <td>${JB[v.judge]&&JB[v.judge].logos[0]?`<span class="logos">${icon(JB[v.judge].logos[0])}</span> `:''}${esc(v.model)}</td><td>${BY[v.team]?key(BY[v.team])+' '+esc(BY[v.team].model):esc(v.team)}</td><td>${esc(v.label)}</td>
 <td class="n">${sc(v.scientific_quality)}</td><td class="n">${sc(v.artifacts)}</td><td class="n">${dur(v.seconds)}</td><td class="n">${money(v.usd,'')}</td></tr>`).join('')}</tbody>`;
 $('jpager').innerHTML=`<span>Page ${S.jpage+1} of ${pages}</span><button data-jpg="-1" ${S.jpage?'':'disabled'}>Previous</button><button data-jpg="1" ${S.jpage<pages-1?'':'disabled'}>Next</button>`}
function archiveView(){const m=S.d.archive;$('archdl').hidden=!m;
 $('archline').textContent=!m?'No archive yet: the first is taken at the first evaluation.':
  `Latest: ${m.archive}, ${m.race_time?dur((Date.parse(m.race_time)-T0)/1000)+' into the race':''}, after ${m.evaluations.snapshots} snapshots and ${m.evaluations.grades} grades · ${num(m.files)} files, ${(m.bytes/1e6).toFixed(1)} MB · checked`+
  (Object.keys(m.chains).length?` · ${Object.keys(m.chains).length} of ${m.chains_checked} ledgers do not verify: ${Object.entries(m.chains).slice(0,3).map(([k,v])=>k+' '+v).join('; ')}`:` · its ${m.chains_checked} ledgers verify`)+
  (m.skipped.length?` · left out, over 50 MB: ${m.skipped.slice(0,3).join(', ')}`:'')}
/* the jury's panel and on-demand evaluation: the page prices it from the judges' cost per verdict */
function panelView(){const P=S.d.panel,E=S.d.evaluate;if(!P)return;const C=Object.fromEntries(P.candidates.map(c=>[c.provider,c]));
 if(!S.pj||!S.pjDirty)S.pj=P.judges.slice();const sel=new Set(S.pj),pk=$('jpick');
 if(pk.childElementCount!==P.candidates.length)pk.innerHTML=P.candidates.map(c=>`<button data-judge="${esc(c.provider)}" aria-pressed="false" aria-label="${esc(NAMES[c.logos[0]]||c.provider)}, ${esc(c.model)}" data-tip="${esc(c.model)}\n${money(c.usd)} per verdict, ${c.observed?`the mean of its ${c.observed} calls`:'from the price list'}">${c.logos[0]?icon(c.logos[0]):''}<span>${esc(c.model)}</span><span class="usd">${money(c.usd)}</span></button>`).join('');
 pk.querySelectorAll('[data-judge]').forEach(b=>b.setAttribute('aria-pressed',sel.has(b.dataset.judge)));
 const changed=S.pj.join()!==P.judges.join();$('panelsave').disabled=!changed||!S.pj.length;$('panelreset').disabled=!changed;
 $('jwarn').textContent=!S.pj.length?'Pick at least one judge.':changed?`Not applied yet. Grades given by another panel are not strictly comparable: snapshots graded before and after the change mix the two panels' medians.`:'';
 $('jhist').textContent=P.history.length>1?'Panel changes: '+P.history.map(h=>`${h.at?dur((Date.parse(h.at)-T0)/1000)+' into the race':'at the start'}: ${h.judges.join(', ')}`).join(' → '):`The panel the race started with: ${P.judges.join(', ')}.`;
 const running=TEAMS.filter(t=>!t.delivered),v=running.flatMap(t=>S.pj.filter(j=>j!==t.provider)),usd=v.reduce((a,j)=>a+(C[j]?C[j].usd:0),0);
 $('evhelp').textContent=E?`Takes a snapshot of each running team now, then each judge of the panel grades it, as at a milestone: ${running.length} teams, ${v.length} verdicts, about ${money(usd)} of the jury's budget${S.d.jury&&S.d.jury.cap?` (${money(S.d.jury.usd)} of ${S.d.jury.cap} USD spent so far)`:''}. The grades join the charts as an "on demand" point once the judges have answered, a few minutes of race later. A team in its first 15 minutes has no report yet and gets no grade.`
  :'Not available in this race: its jury grades at the milestones only. A replayed race (tools/fake_race.py --replay) takes on-demand evaluations.';
 const b=$('evaluate');b.disabled=!E||!!(E&&E.pending.length)||!S.pj.length||changed;
 $('evline').textContent=E&&E.pending.length?'Asked: every running team is snapshotted now; its grades come when the judges answer.':changed?'Apply the panel first.':''}
