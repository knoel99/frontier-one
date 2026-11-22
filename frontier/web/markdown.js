/* LaTeX math to MathML, which the browser draws itself: the common commands; an unknown one shows as its name */
const MSYM={alpha:'α',beta:'β',gamma:'γ',delta:'δ',epsilon:'ϵ',varepsilon:'ε',zeta:'ζ',eta:'η',theta:'θ',kappa:'κ',lambda:'λ',mu:'μ',nu:'ν',xi:'ξ',pi:'π',rho:'ρ',sigma:'σ',tau:'τ',phi:'ϕ',varphi:'φ',chi:'χ',psi:'ψ',omega:'ω',
 Gamma:'Γ',Delta:'Δ',Theta:'Θ',Lambda:'Λ',Xi:'Ξ',Pi:'Π',Sigma:'Σ',Phi:'Φ',Psi:'Ψ',Omega:'Ω',partial:'∂',nabla:'∇',infty:'∞',ell:'ℓ',hbar:'ℏ'};
const MOP={cdot:'⋅',times:'×',pm:'±',mp:'∓',leq:'≤',le:'≤',geq:'≥',ge:'≥',neq:'≠',ne:'≠',approx:'≈',sim:'∼',propto:'∝',equiv:'≡',to:'→',rightarrow:'→',leftarrow:'←',Rightarrow:'⇒',mapsto:'↦',
 in:'∈',subset:'⊂',cup:'∪',cap:'∩',sum:'∑',prod:'∏',int:'∫',oint:'∮',ldots:'…',cdots:'⋯',langle:'⟨',rangle:'⟩',lvert:'|',rvert:'|',forall:'∀',exists:'∃',circ:'∘',ast:'∗',star:'⋆',
 log:'log',ln:'ln',exp:'exp',sin:'sin',cos:'cos',tan:'tan',max:'max',min:'min',arg:'arg',lim:'lim',det:'det'};
function tex2mml(src,block){const T=src.match(/\\[A-Za-z]+|\\.|[A-Za-z]|\d+(\.\d+)?|\S/g)||[];let i=0;
 const arg=()=>{if(T[i]==='{'){i++;const r=seq('}');i++;return r}return atom()};
 function atom(){const t=T[i++];if(t===undefined)return '';
  if(t==='{'){const r=seq('}');i++;return `<mrow>${r}</mrow>`}
  if(t==='\\frac'||t==='\\dfrac'||t==='\\tfrac')return `<mfrac><mrow>${arg()}</mrow><mrow>${arg()}</mrow></mfrac>`;
  if(t==='\\sqrt')return `<msqrt><mrow>${arg()}</mrow></msqrt>`;
  if(/^\\(mathrm|text|operatorname|mathbf|mathit|mathcal|mathbb|boldsymbol)$/.test(t)){const v=T[i]==='{'?(i++,(()=>{let w='';while(i<T.length&&T[i]!=='}')w+=T[i++];i++;return w})()):T[i++];
   return t==='\\text'?`<mtext>${esc(v)}</mtext>`:`<mi mathvariant="${t==='\\mathbf'||t==='\\boldsymbol'?'bold':t==='\\mathbb'?'double-struck':t==='\\mathcal'?'script':t==='\\mathit'?'italic':'normal'}">${esc(v)}</mi>`}
  if(t==='\\left'||t==='\\right'||t==='\\,'||t==='\;'||t==='\\!'||t==='\\quad')return t==='\\quad'?'<mspace width="1em"/>':'';
  if(t[0]==='\\'){const n=t.slice(1);return MSYM[n]?`<mi>${MSYM[n]}</mi>`:MOP[n]?`<mo>${MOP[n]}</mo>`:`<mi mathvariant="normal">${esc(n)}</mi>`}
  if(/^\d/.test(t))return `<mn>${t}</mn>`;if(/[A-Za-z]/.test(t))return `<mi>${t}</mi>`;return `<mo>${esc(t)}</mo>`}
 function seq(end){let o='';while(i<T.length&&T[i]!==end){let b=atom();let sub='',sup='';
   while(T[i]==='_'||T[i]==='^'){const k=T[i++];const a=arg();if(k==='_')sub=a;else sup=a}
   o+=sub&&sup?`<msubsup>${b}<mrow>${sub}</mrow><mrow>${sup}</mrow></msubsup>`:sub?`<msub>${b}<mrow>${sub}</mrow></msub>`:sup?`<msup>${b}<mrow>${sup}</mrow></msup>`:b}return o}
 return `<math${block?' display="block"':''}><semantics><mrow>${seq()}</mrow><annotation encoding="application/x-tex">${esc(src)}</annotation></semantics></math>`}
function md(src){const M=[],keep=(x,b)=>`\u0000${M.push(tex2mml(x,b))-1}\u0000`;
 src=src.replace(/\$\$([\s\S]+?)\$\$/g,(_,x)=>keep(x,1)).replace(/\\\[([\s\S]+?)\\\]/g,(_,x)=>keep(x,1)).replace(/\$([^$\n]+?)\$/g,(_,x)=>keep(x,0)).replace(/\\\(([^\n]+?)\\\)/g,(_,x)=>keep(x,0));
 return mdBlocks(src).replace(/\u0000(\d+)\u0000/g,(_,k)=>M[+k])}
function mdBlocks(src){const inl=x=>esc(x).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>').replace(/(^|\W)\*([^*]+)\*/g,'$1<i>$2</i>')
  .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,'<a href="$2" rel="noopener noreferrer" target="_blank">$1</a>');
 const out=[],L=src.split('\n');let i=0;
 while(i<L.length){const l=L[i];let m;
  if(l.startsWith('```')){const b=[];for(i++;i<L.length&&!L[i].startsWith('```');i++)b.push(L[i]);i++;out.push(`<pre><code>${esc(b.join('\n'))}</code></pre>`)}
  else if(m=l.match(/^(#{1,6})\s+(.*)/)){const n=Math.min(6,m[1].length+1);out.push(`<h${n}>${inl(m[2])}</h${n}>`);i++}
  else if(/^\|.*\|\s*$/.test(l)){const R=[];for(;i<L.length&&/^\|/.test(L[i]);i++)if(!/^\|[\s:|-]+\|\s*$/.test(L[i]))R.push(L[i].slice(1,-1).split('|'));
   out.push('<table>'+R.map((r,k)=>'<tr>'+r.map(c=>k?`<td>${inl(c.trim())}</td>`:`<th>${inl(c.trim())}</th>`).join('')+'</tr>').join('')+'</table>')}
  else if(/^\s*([-*]|\d+\.)\s/.test(l)){const it=[],ol=/^\s*\d/.test(l);for(;i<L.length&&/^\s*([-*]|\d+\.)\s/.test(L[i]);i++)it.push(`<li>${inl(L[i].replace(/^\s*([-*]|\d+\.)\s/,''))}</li>`);out.push(`<${ol?'ol':'ul'}>${it.join('')}</${ol?'ol':'ul'}>`)}
  else if(l.trim()){const b=[];for(;i<L.length&&L[i].trim()&&!/^(#|```|\||\s*([-*]|\d+\.)\s)/.test(L[i]);i++)b.push(L[i]);out.push(`<p>${inl(b.join(' '))}</p>`)}
  else i++}
 return out.join('')}
