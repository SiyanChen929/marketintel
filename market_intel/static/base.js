// Shared helpers for Market Intel pages: DOM, fetch (abortable), formatting, nav, theme, async panel states,
// accessible sortable tables (aria-sort, keyboard, column chooser, cards), lazy Plotly, chart takeaways + data tables.
const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));
// The server loads local catalogs first. Standalone/offline test harnesses retain English.
if(typeof globalThis.t!=='function')globalThis.t=(s,v={})=>String(s??'').replace(/\{(\w+)\}/g,(m,k)=>Object.prototype.hasOwnProperty.call(v,k)?String(v[k]??''):m);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function j(url,opts){const r=await fetch(url,opts);if(r.status===401){location.href='/login?next='+encodeURIComponent(location.pathname+location.search);throw new Error('401');}if(!r.ok)throw new Error(r.status+' '+url);return r.json();}
// ---------- aria-live announcer (polite) shared by toasts and panel states
function liveRegion(){let r=document.getElementById('live-status');if(!r&&document.body){r=document.createElement('div');r.id='live-status';r.className='sr-only';r.setAttribute('role','status');r.setAttribute('aria-live','polite');r.setAttribute('aria-atomic','true');document.body.appendChild(r);}return r;}
function announce(m){const r=liveRegion();if(!r)return;r.textContent='';setTimeout(()=>{r.textContent=t(m);},30);}
function toast(m){let el=$('#toast');if(!el){el=document.createElement('div');el.id='toast';el.className='toast';el.setAttribute('aria-hidden','true');document.body.appendChild(el);}el.textContent=t(m);el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2500);announce(m);}
// ---------- fetch with AbortController + sequence guard: a stale response never overwrites a newer one (same key)
const _inflight={};
async function fetchJSON(url,key,opts){key=key||url;const prev=_inflight[key];if(prev&&prev.ctrl)prev.ctrl.abort();const ctrl=new AbortController();const seq=(prev?prev.seq:0)+1;_inflight[key]={ctrl,seq};
 try{const r=await fetch(url,Object.assign({},opts||{},{signal:ctrl.signal}));if(r.status===401){location.href='/login?next='+encodeURIComponent(location.pathname+location.search);throw new Error('401');}
  if(!r.ok){let m='';try{m=(await r.json()).error||'';}catch(e){}throw new Error(r.status+' '+(m||url));}const d=await r.json();if((_inflight[key]||{}).seq!==seq)throw Object.assign(new Error('superseded'),{stale:true});return d;}
 catch(e){if(e&&e.name==='AbortError')throw Object.assign(new Error('superseded'),{stale:true});throw e;}}
// ---------- async panel states: loading | fresh | stale | error | empty (status line in .head, skeleton while loading, Retry on error)
const PANEL_TXT={loading:'loading…',fresh:'',stale:'stale',error:'failed to load',empty:'no data'};
function setPanel(el,state,msg,retry){el=typeof el==='string'?document.querySelector(el):el;if(!el)return;el.dataset.state=state;el.setAttribute('aria-busy',state==='loading'?'true':'false');
 const head=el.querySelector(':scope>.head');let st=head?head.querySelector(':scope>.pstate'):el.querySelector(':scope>.pstate');
 if(!st){st=document.createElement('span');st.className='pstate';if(head)head.appendChild(st);else el.insertBefore(st,el.firstChild);}
 const txt=t(msg!=null?msg:(PANEL_TXT[state]||''));st.innerHTML=txt?`<span class="dot ${state==='fresh'?'ok':state==='error'?'bad':state==='stale'?'warn':''}" aria-hidden="true"></span><span>${esc(txt)}</span>`:'';
 if(state==='error'&&typeof retry==='function'){const b=document.createElement('button');b.type='button';b.className='chip btn';b.textContent=t('Retry');const h=head&&head.querySelector('h2');b.setAttribute('aria-label',t('Retry loading {name}',{name:h?h.textContent.trim():t('this panel')}));b.addEventListener('click',retry);st.appendChild(b);}
 let sk=el.querySelector(':scope>.skel');if(state==='loading'){if(!sk){sk=document.createElement('div');sk.className='skel';sk.setAttribute('aria-hidden','true');sk.innerHTML='<i></i><i></i><i></i>';el.appendChild(sk);}}else if(sk)sk.remove();
 let m=el.querySelector(':scope>.panel-msg');if(state==='error'||state==='empty'){if(!m){m=document.createElement('div');m.className='panel-msg';el.appendChild(m);}m.className='panel-msg'+(state==='error'?' bad':'');m.textContent=txt;}else if(m)m.remove();
 const h2=head&&head.querySelector('h2');if(state==='error')announce((h2?h2.textContent.trim()+': ':'')+txt);}
function hhmm(d){d=d||new Date();return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
function asOfChip(asof,okAt,stale){return `<span class="chip asof${stale?' warnc':''}" title="data as-of: pipeline session the data belongs to · refreshed: last successful fetch by this page"><span>${esc(t('data as-of'))}</span> ${esc(asof||'–')} · <span>${esc(t('refreshed'))}</span> <span>${esc(okAt?hhmm(okAt):t('never'))}</span>${stale?' <span>'+esc(t('(stale)'))+'</span>':''}</span>`;}
function busy(btn,on,msg){if(!btn)return;btn.disabled=!!on;btn.setAttribute('aria-busy',on?'true':'false');if(on){if(!btn.dataset.label)btn.dataset.label=btn.textContent;if(msg)btn.textContent=msg;}else if(btn.dataset.label){btn.textContent=btn.dataset.label;delete btn.dataset.label;}}
// ---------- small shared components: Badge(level[,text]) alert-level pill, Kpi(label,value,sub) tile (value/sub are HTML, callers escape), Toast.ok/bad (toast + aria-live announce)
function Badge(level,text){return level?`<span class="lvl ${esc(level)}">${esc(t(text||level))}</span>`:'';}
function Kpi(label,value,sub){return `<div class="kpi"><div class="l">${esc(t(label))}</div><div class="v">${value==null||value===''?'–':value}</div>${sub?`<div class="d">${sub}</div>`:''}</div>`;}
const Toast={show(m,kind){toast(m);const t=$('#toast');if(t)t.classList.toggle('bad',kind==='bad');},ok(m){Toast.show(m,'ok');},bad(m){Toast.show(m,'bad');}};
// JSON POST helper: FastAPI error bodies ({detail}) become the Error message; 401 redirects to the login page
async function postJSON(url,body,opts){const r=await fetch(url,Object.assign({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body==null?{}:body)},opts||{}));
 if(r.status===401){location.href='/login?next='+encodeURIComponent(location.pathname+location.search);throw new Error('401');}
 if(!r.ok){let m='';try{const j=await r.json();m=j.detail||j.error||'';if(typeof m!=='string')m=JSON.stringify(m);}catch(e){}throw new Error(r.status+' '+(m||url));}return r.json();}
const fmt={pct:(v,d=1)=>v==null||isNaN(v)?'':(v*100).toFixed(d)+'%',num:(v,d=2)=>v==null||isNaN(v)?'':Number(v).toFixed(d),
 big:v=>v==null||isNaN(v)?'':Math.abs(v)>=1e12?(v/1e12).toFixed(2)+'T':Math.abs(v)>=1e9?(v/1e9).toFixed(1)+'B':Math.abs(v)>=1e6?(v/1e6).toFixed(0)+'M':Number(v).toFixed(0),
 x:(v,d=1)=>v==null||isNaN(v)?'':Number(v).toFixed(d)+'x',date:s=>s?String(s).slice(0,10):''};
const cls=v=>v>0?'pos':v<0?'neg':'';
// evidence-state banner (S3): the decision score is NOT VALIDATED; weights are model targets; simulations are hindsight illustrations
function statusBanner(run){run=run||{};const v=run.validation||{};const f=(x,d=3)=>x==null||isNaN(x)?'n/a':Number(x).toFixed(d);
 const clock=run.current_target_status||{},expired=clock.status==='EXPIRED',clockInvalid=clock.status==='INVALID_POLICY';
 const lf=v.live_forward||{};const fwdE=Object.entries(lf).filter(([h,n])=>n>0);const fwd=fwdE.length?fwdE.map(([h,n])=>h+': '+n).join(', '):'none yet';
 const reasons=(run.not_actionable_reasons||[]).map(String);
 const ents=(run.data_health||{}).entries||{};const st=(run.data_health||{}).stale||[];
 const bad=run.actionable===false||!!run.blocked||expired||clockInvalid;
 const vs=String(run.validation_status||'NOT VALIDATED · exploratory');
 const tags=(run.blocked?'<span class="rtag bad">RUN BLOCKED</span>':'')+
  (expired?'<span class="rtag bad">TARGET EXPIRED</span>':clockInvalid?'<span class="rtag bad">TARGET CLOCK UNVERIFIED</span>':'')+
  (run.actionable===false?'<span class="rtag bad">NOT ACTIONABLE</span>':'')+
  `<span class="rtag">${esc(vs.split('·')[0].trim())}</span>`;
 // one line that says what an analyst must know; everything else is one click away
 const bits=[];
 if(expired)bits.push('<span>'+esc(t('目标已过期 · 执行日 {session}',{session:clock.execution_session||'–'}))+'</span>');
 else if(clock.status==='PENDING_OPEN')bits.push('<span>'+esc(t('目标等待开盘窗口'))+'</span>');
 else if(clockInvalid)bits.push('<span>'+esc(t('目标执行时间无法核验'))+'</span>');
 if(run.blocked)bits.push('<span>'+esc(t('blocked {time}',{time:(run.blocked.at||'').slice(0,16).replace('T',' ')}))+'</span>');
 if(reasons.length)bits.push('<span translate="no">'+esc(reasons[0])+'</span>'+(reasons.length>1?'<span>'+esc(t(' (+{count} more)',{count:reasons.length-1}))+'</span>':''));
 if(st.length)bits.push('<span>'+esc(t(st.length===1?'{count} stale input':'{count} stale inputs',{count:st.length}))+'</span>');
 bits.push('<span>'+esc(t('model targets, not positions'))+'</span>');
 const chips=st.slice(0,8).map(k=>{const e=ents[k]||{};return `<span class="chip ${e.status==='unavailable'?'bad':'warnc'}" title="${esc(e.stale_reason||'')}${e.effective_at?' · obs '+esc(e.effective_at):''}${e.fetched_at?' · fetched '+esc(String(e.fetched_at).slice(0,16).replace('T',' ')):''}">${esc((e.status||'stale').toUpperCase())} ${esc(k)}</span>`;}).join('')+(st.length>8?`<span class="chip warnc">+${st.length-8} more</span>`:'');
 const det=`<details class="rdet"><summary>Why</summary><div class="rdetb">`+
  (clock.status?`<p><b>${esc(t('Current target clock: {status}',{status:clock.status}))}</b> · <span translate="no">${esc((clock.reasons||[]).join(' · '))}</span>. <span>${esc(t('Generation-time input checks remain archived; this clock does not approve a trade.'))}</span> <a href="/research#readiness-panel">查看运行状态 →</a></p>`:'')+
  (run.blocked?`<p><b>Run blocked</b> ${esc((run.blocked.at||'').slice(0,16).replace('T',' '))} · <span translate="no">${esc((run.blocked.reasons||[]).join(' · '))}</span> · <span>showing the last completed run.</span></p>`:'')+
  (reasons.length?`<p><b>Not actionable</b></p><ul translate="no">${reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>`:'')+
  `<p><b>${esc(t(vs))}</b> — <span>${esc(t('decision score: point-in-time 3m IC {ic3} (HAC t {t3}), walk-forward 3m t {wft3}, 12m IC {ic12} (t {t12}); live forward {snapshots} snapshots, forward returns {returns}.',{ic3:f(v.pit_ic_3m),t3:f(v.pit_t_3m,2),wft3:f(v.wf_t_3m,2),ic12:f(v.pit_ic_12m),t12:f(v.pit_t_12m,2),snapshots:v.live_snapshots||0,returns:fwd}))}</span></p>`+
  `<p>Weights are model targets, not positions (no live ledger). Any simulation on these pages is a hindsight illustration, not a track record.</p>`+
  (chips?`<div class="chips">${chips}</div>`:'')+
  `</div></details>`;
 return `<div class="rstrip${bad?' bad':''}" role="${bad?'alert':'status'}" aria-label="research status">${tags}<span class="rsum">${bits.join(' · ')}</span>${det}</div>`;}
// canonical run chip: every page quotes the same run_id / session / evidence state (S1)
function runChip(run){if(!run||!run.run_id)return '<span class="chip run" title="no canonical run manifest yet">run: none</span>';const g=(run.generated_at||'').slice(0,16).replace('T',' ');const expired=run.current_target_status?.status==='EXPIRED';const act=expired?'TARGET EXPIRED':run.actionable===false?'NOT ACTIONABLE':'';const short=run.run_id.length>28?run.run_id.slice(0,28)+'…':run.run_id;
 return `<span class="chip run${run.actionable===false||expired?' bad':''}" title="${esc(t('canonical run {id} · session {session} · generated {time}{supersedes} · {validation} · executable target weights, no live position ledger',{id:run.run_id,session:run.asof_session||'',time:g,supersedes:run.supersedes?' · supersedes '+run.supersedes:'',validation:run.validation_status||''}))}"><span>${esc(t('run {id} · {session}{action}',{id:short,session:run.asof_session||'',action:''}))}</span>${act?' · <span>'+esc(t(act))+'</span>':''}</span>`;}
const PAGES=[['/','Home'],['/portfolio','Portfolio'],['/universe','Universe'],['/macro','Macro'],['/research','Research'],['/cases','Cases'],['/platform','Platform']];
// Portfolio owns the book, risk review, daily history and construction research.
const SUBNAV={'/portfolio':[['/portfolio','组合总览'],['/risk','风险分析'],['/rebalances','调仓历史'],['/constructions','构建方法比较'],['/bl','Black-Litterman']]};
function subnavFor(path){for(const[parent,kids]of Object.entries(SUBNAV))if(kids.some(([h])=>h===path))return{parent,kids};return null;}
function renderNav(active){const n=$('#nav');if(!n)return;const sub=subnavFor(active);const top=sub?sub.parent:active;
 n.setAttribute('aria-label',t('Pages'));
 n.innerHTML=PAGES.map(([h,l])=>`<a href="${h}" class="${h===top?'on':''}"${h===top?' aria-current="page"':''}>${esc(t(l))}</a>`).join('');
 if(matchMedia('(max-width:760px)').matches)requestAnimationFrame(()=>{const cur=n.querySelector('[aria-current="page"]');if(cur)n.scrollLeft=Math.max(0,cur.offsetLeft-(n.clientWidth-cur.offsetWidth)/2);});
 const bar=$('#subnav');
 if(bar){bar.setAttribute('aria-label',t('Portfolio views'));bar.innerHTML=sub?sub.kids.map(([h,l])=>`<a data-view="${h}" href="${h}" class="${h===active?'on':''}"${h===active?' aria-current="page"':''}>${esc(t(l))}</a>`).join(''):'';}}
// Cross-view links carry the book identity. A hypothetical risk layer never becomes an archived account target.
function portfolioViewLinks({method='recommended',layer=method==='recommended'?'actual_run':'model',primary,win='1y',bench='te_proxy'}={}){
 const modelMethod=method==='recommended'&&layer!=='actual_run'?primary:method;
 const bookMethod=modelMethod||'recommended';
 const actual=method==='recommended'&&layer==='actual_run';
 const risk=new URLSearchParams({method,layer,win,bench});
 const overview=new URLSearchParams({method:bookMethod});
 const archive=new URLSearchParams({construction:actual?'active':bookMethod==='recommended'?'active':bookMethod,basis:actual?'target':'model'});
 return {'/portfolio':'/portfolio?'+overview,'/risk':'/risk?'+risk,'/rebalances':'/rebalances?'+archive};
}
function syncPortfolioLinks(context){const links=portfolioViewLinks(context);
 document.querySelectorAll('#subnav a[data-view]').forEach(a=>{if(links[a.dataset.view])a.href=links[a.dataset.view];});
 for(const[id,path]of [['#method-risk','/risk'],['#method-history','/rebalances']]){const a=$(id);if(a)a.href=links[path];}
}
function themeInit(){try{const t=localStorage.getItem('mi-theme');if(t)document.documentElement.setAttribute('data-theme',t);}catch(e){}
 const b=$('#btn-theme');const sync=()=>{if(!b)return;const d=isDark();b.setAttribute('aria-pressed',d?'true':'false');b.setAttribute('aria-label',t(d?'Switch to light theme':'Switch to dark theme'));b.title=b.getAttribute('aria-label');};sync();document.addEventListener('languagechange',sync);
 if(b)b.addEventListener('click',()=>{const r=document.documentElement;const cur=r.getAttribute('data-theme');const next=cur==='dark'?'light':cur==='light'?'dark':(matchMedia('(prefers-color-scheme: dark)').matches?'light':'dark');r.setAttribute('data-theme',next);try{localStorage.setItem('mi-theme',next);}catch(e){}sync();document.dispatchEvent(new Event('themechange'));});
 uiInit();}
// one-time UI wiring: density preference, live region, delegated row navigation (rows carry an inner <a>, no bare onclick), poll pause/resume
function uiInit(){if(!document.body||document.body.dataset.ui)return;document.body.dataset.ui='1';try{const d=localStorage.getItem('mi-density');if(d)document.documentElement.setAttribute('data-density',d);}catch(e){}liveRegion();
 document.addEventListener('click',e=>{const tr=e.target.closest&&e.target.closest('tr.row');if(!tr||e.target.closest('a,button,input,select,summary,details'))return;const a=tr.querySelector('a.rl[href]');if(a)location.href=a.getAttribute('href');});
 document.addEventListener('visibilitychange',()=>{_polls.slice().forEach(p=>document.hidden?p.pause():p.resume());});}
// ---------- polling that pauses while the tab is hidden
const _polls=[];
function visiblePoll(fn,ms){const p={fn,ms,id:null,paused:false,start(){if(!this.id)this.id=setInterval(()=>{if(!document.hidden)this.fn();},this.ms);},pause(){if(this.id){clearInterval(this.id);this.id=null;this.paused=true;}},resume(){if(this.paused){this.paused=false;this.start();try{this.fn();}catch(e){}}},stop(){if(this.id)clearInterval(this.id);this.id=null;const i=_polls.indexOf(this);if(i>=0)_polls.splice(i,1);}};_polls.push(p);if(document.hidden)p.paused=true;else p.start();return p;}
// ---------- URL state (query params) without navigation
function watchPublishedRun(currentRun,refresh){let checking=false;return visiblePoll(async()=>{
 if(checking)return;checking=true;
 try{const state=await fetchJSON('/api/pipeline/status','published-run');const current=currentRun();
  if(!state.running&&current&&state.run_id&&state.run_id!==current)await refresh();
 }catch(e){/* Keep the displayed archive and retry on the next visible poll. */}finally{checking=false;}
},60000);}

function qs(){return new URLSearchParams(location.search);}
function setQS(obj){const u=new URL(location.href);Object.entries(obj).forEach(([k,v])=>{if(v==null||v===''||v===false)u.searchParams.delete(k);else u.searchParams.set(k,v===true?'1':String(v));});const q=u.searchParams.toString();history.replaceState(null,'',u.pathname+(q?'?'+q:'')+u.hash);}
function isDark(){const t=document.documentElement.getAttribute('data-theme');return t==='dark'||(!t&&matchMedia('(prefers-color-scheme: dark)').matches);}
function pal(){const d=isDark();return {series:d?['#3987e5','#d95926','#199e70','#c98500','#d55181','#008300','#9085e9','#e66767']:['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#4a3aa7','#e34948'],
 pos:d?'#3987e5':'#2a78d6',neg:d?'#e66767':'#e34948',mid:d?'#383835':'#f0efec',ink:d?'#ffffff':'#0b0b0b',ink2:d?'#c3c2b7':'#52514e',muted:d?'#9a9890':'#6b6a65',grid:d?'#2c2c2a':'#e1e0d9',surface:d?'#1a1a19':'#fcfcfb',gray:d?'#8a8983':'#a9a7a0'};}
function layout(extra={}){const p=pal();return Object.assign({paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{family:'system-ui,-apple-system,"Segoe UI","PingFang SC","Hiragino Sans","Microsoft YaHei","Yu Gothic",sans-serif',size:12,color:p.ink2},margin:{l:48,r:16,t:28,b:40},
 xaxis:{gridcolor:p.grid,zerolinecolor:p.grid,linecolor:p.grid,tickfont:{color:p.muted}},yaxis:{gridcolor:p.grid,zerolinecolor:p.grid,linecolor:p.grid,tickfont:{color:p.muted}},
 legend:{orientation:'h',y:1.12,x:0,font:{size:12}},hovermode:'closest',colorway:p.series,hoverlabel:{bgcolor:p.surface,bordercolor:p.grid,font:{color:p.ink,size:12}}},extra);}
const PLOT_CFG={displayModeBar:false,responsive:true};
// ---------- lazy Plotly: inject the script once (partial cartesian bundle first: bar, scatter, heatmap; full bundle as fallback)
let _plotlyP=null;
function ensurePlotly(){if(typeof Plotly!=='undefined')return Promise.resolve(window.Plotly);if(_plotlyP)return _plotlyP;
 const ld=src=>new Promise((res,rej)=>{const s=document.createElement('script');s.src=src;s.async=true;s.onload=()=>typeof Plotly!=='undefined'?res(window.Plotly):rej(new Error('bad bundle '+src));s.onerror=()=>{s.remove();rej(new Error('failed '+src));};document.head.appendChild(s);});
 _plotlyP=ld('/static/plotly-cartesian.min.js').catch(()=>ld('/static/plotly.min.js')).catch(e=>{_plotlyP=null;throw e;});return _plotlyP;}
const _localizedPlots=new Map();
function chartLabel(s){return typeof s==='string'?t(s):s;}
function _localizedChartTitle(title){return typeof title==='string'?chartLabel(title):title&&typeof title==='object'?Object.assign({},title,{text:chartLabel(title.text)}):title;}
function chartLabels(traces,lay){
 const data=traces.map(trace=>Object.assign({},trace,{name:chartLabel(trace.name)}));
 const out=Object.assign({},lay);if(out.title)out.title=_localizedChartTitle(out.title);
 Object.keys(out).filter(k=>/^[xy]axis\d*$/.test(k)).forEach(k=>{out[k]=Object.assign({},out[k]);if(out[k].title)out[k].title=_localizedChartTitle(out[k].title);});
 if(out.annotations)out.annotations=out.annotations.map(a=>Object.assign({},a,{text:chartLabel(a.text)}));
 return {data,lay:out};
}
function plot(id,traces,lay){const el=document.getElementById(id);if(!el)return Promise.resolve(null);if(!el.getAttribute('role')){el.setAttribute('role','img');}
 _localizedPlots.set(id,{traces,lay:lay||{}});
 return ensurePlotly().then(P=>{const labels=chartLabels(traces,lay||{});return P.react(el,labels.data,layout(labels.lay),PLOT_CFG);}).then(()=>el).catch(e=>{el.innerHTML='<div class="empty">'+esc(t('Chart unavailable ({error}). The data table below has the same series.',{error:e.message}))+'</div>';return null;});}
document.addEventListener('languagechange',()=>{for(const[id,p]of _localizedPlots)if(document.getElementById(id))plot(id,p.traces,p.lay);});
// ---------- chart takeaway line + data-table toggle (every .plot gets a text alternative)
function _chartSlot(id,kind){const el=document.getElementById(id);if(!el)return null;let s=document.getElementById(id+'-'+kind);if(s)return s;
 s=document.createElement(kind==='tk'?'p':'div');s.id=id+'-'+kind;s.className=kind==='tk'?'takeaway':kind==='tools'?'chart-tools':'chart-data tblwrap';
 const order=['tk','tools','data'];let after=el;order.slice(0,order.indexOf(kind)).forEach(k=>{const x=document.getElementById(id+'-'+k);if(x)after=x;});after.insertAdjacentElement('afterend',s);return s;}
function takeaway(id,text){const s=_chartSlot(id,'tk');if(s)s.textContent=t(text||'');const el=document.getElementById(id);if(el&&text)el.setAttribute('aria-label',t(text));return s;}
function chartTable(id,columns,rows){const el=document.getElementById(id);if(!el)return null;const tools=_chartSlot(id,'tools');const data=_chartSlot(id,'data');
 if(!data.dataset.init){data.dataset.init='1';data.hidden=true;const b=document.createElement('button');b.type='button';b.className='chip btn';b.id=id+'-tbtn';b.setAttribute('aria-expanded','false');b.setAttribute('aria-controls',id+'-data');b.textContent='Data table';b.setAttribute('aria-label','Show the plotted series as a table');
  b.addEventListener('click',()=>{const open=data.hidden;data.hidden=!open;b.setAttribute('aria-expanded',open?'true':'false');b.textContent=open?'Hide table':'Data table';});tools.appendChild(b);}
 const cell=v=>v==null?'':typeof v==='number'?(Number.isInteger(v)?String(v):fmt.num(v,3)):esc(v);const numc=v=>typeof v==='number'?' class="num"':'';
 data.innerHTML=`<table><caption class="sr-only">${esc(t('Data for chart {name}',{name:id}))}</caption><thead><tr>${columns.map((c,i)=>`<th scope="col"${rows.length&&typeof rows[0][i]==='number'?' class="num"':''}>${esc(t(c))}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(r=>'<tr>'+r.map(v=>`<td translate="no"${numc(v)}>${cell(v)}</td>`).join('')+'</tr>').join(''):`<tr><td colspan="${columns.length}" class="empty">${esc(t('no data'))}</td></tr>`}</tbody></table>`;return data;}
function chartNote(id,text,columns,rows){takeaway(id,text);if(columns)chartTable(id,columns,rows||[]);}
// ---------- accessible sortable tables: aria-sort, Enter/Space, sticky sort state per table, optional card view (narrow screens)
function _cardsHost(t){const w=t.closest('.tblwrap')||t;w.classList.add('cardable');let c=w.nextElementSibling;if(!c||!c.classList.contains('cards')){c=document.createElement('div');c.className='cards';c.setAttribute('role','list');w.insertAdjacentElement('afterend',c);}return c;}
function sortable(tableId,rows,render,defaultKey,desc=true,opts={}){const t=document.getElementById(tableId);if(!t)return()=>{};if(!t._sort)t._sort={key:defaultKey,desc};const state=t._sort;
 function draw(){const r=[...rows].sort((a,b)=>{const x=a[state.key],y=b[state.key];if(x==null&&y==null)return 0;if(x==null)return 1;if(y==null)return -1;return (typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y)))*(state.desc?-1:1);});
  const n=t.querySelectorAll('thead th').length;t.querySelector('tbody').innerHTML=r.length?r.map(render).join(''):`<tr><td colspan="${n||1}" class="empty">${esc(globalThis.t(opts.emptyText||'No rows match the current filters.'))}</td></tr>`;
  t.querySelectorAll('th[data-k]').forEach(th=>th.setAttribute('aria-sort',th.dataset.k===state.key?(state.desc?'descending':'ascending'):'none'));
  if(t._applyCols)t._applyCols();if(opts.card){const c=_cardsHost(t);c.innerHTML=r.length?r.map(opts.card).join(''):`<div class="empty">${esc(globalThis.t(opts.emptyText||'No rows match the current filters.'))}</div>`;}
  if(opts.onDraw)opts.onDraw(r);}
 t.querySelectorAll('th[data-k]').forEach(th=>{if(th.dataset.bound)return;th.dataset.bound='1';th.tabIndex=0;th.setAttribute('scope','col');if(!th.title)th.title=globalThis.t('Sort by {name}',{name:th.textContent.trim()||th.dataset.k});
  const go=()=>{const s=t._sort;if(s.key===th.dataset.k)s.desc=!s.desc;else{s.key=th.dataset.k;s.desc=true;}t._draw();announce(globalThis.t(s.desc?'Sorted by {name}, descending':'Sorted by {name}, ascending',{name:th.textContent.trim()||th.dataset.k}));};
  th.addEventListener('click',go);th.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}});});
 t._draw=draw;draw();return draw;}
function rowLink(ticker,name,extraCls){const t=String(ticker||'');return `<td class="t${extraCls?' '+extraCls:''}"><a class="rl" href="/ticker/${encodeURIComponent(t)}" aria-label="${esc(globalThis.t('{name} detail',{name:t+(name?' · '+name:'')}))}"><span translate="no">${esc(t)}</span></a>${name?`<span class="nm" translate="no">${esc(name)}</span>`:''}</td>`;}
function cardRow(r,spec){const t=r[spec.ticker||'ticker'];const nm=spec.name?r[spec.name]:'';const seg=spec.segment?r[spec.segment]:'';const href=spec.href?spec.href(r):'/ticker/'+encodeURIComponent(t);
 return `<div class="card" role="listitem"><div class="ct"><a href="${esc(href)}" translate="no">${esc(t)}</a><span class="cs" translate="no">${esc(seg||'')}</span></div>${nm?`<div class="cn" translate="no">${esc(nm)}</div>`:''}<div class="cm">${(spec.metrics||[]).map(([l,f])=>{const v=f(r);return v==null||v===''?'':`<b>${esc(globalThis.t(l))}</b><span>${v}</span>`;}).join('')}</div></div>`;}
function cardsFromRows(rows,spec){return rows.map(r=>cardRow(r,spec)).join('');}
// ---------- column chooser (remembered per table) + density toggle, desktop only
function tableTools(tableId,opts={}){const t=document.getElementById(tableId);if(!t||t._applyCols)return;const key='mi-cols-'+tableId;let hidden=[];try{hidden=JSON.parse(localStorage.getItem(key)||'[]');}catch(e){hidden=[];}
 const ths=Array.from(t.querySelectorAll('thead th'));const labels=ths.map((th,i)=>th.textContent.trim()||th.title||th.dataset.k||('column '+(i+1)));
 t._applyCols=()=>{const set=new Set(hidden);t.querySelectorAll('tr').forEach(tr=>{if(tr.children.length!==ths.length)return;Array.from(tr.children).forEach((c,i)=>{c.hidden=set.has(i);});});};
 const tools=document.createElement('div');tools.className='tbl-tools';const det=document.createElement('details');det.className='colchooser';det.innerHTML=`<summary class="chip btn" role="button" aria-label="${esc(globalThis.t('Choose visible columns for {name}',{name:opts.label||tableId}))}">Columns</summary><div></div>`;
 const box=det.querySelector('div');box.innerHTML=labels.map((l,i)=>`<label><input type="checkbox" data-i="${i}"${hidden.includes(i)?'':' checked'}> ${esc(l)}</label>`).join('')+`<button type="button" class="chip btn" data-reset aria-label="Show all columns">Show all</button>`;
 box.addEventListener('change',e=>{const i=+e.target.dataset.i;if(isNaN(i))return;hidden=e.target.checked?hidden.filter(x=>x!==i):[...new Set([...hidden,i])];try{localStorage.setItem(key,JSON.stringify(hidden));}catch(err){}t._applyCols();});
 box.querySelector('[data-reset]').addEventListener('click',()=>{hidden=[];try{localStorage.removeItem(key);}catch(err){}box.querySelectorAll('input').forEach(i=>i.checked=true);t._applyCols();});
 document.addEventListener('click',e=>{if(det.open&&!det.contains(e.target))det.open=false;});
 const dens=document.createElement('button');dens.type='button';dens.className='chip btn';const syncD=()=>{const c=document.documentElement.getAttribute('data-density')==='comfortable';dens.setAttribute('aria-pressed',c?'true':'false');dens.textContent=c?'Comfortable':'Compact';dens.setAttribute('aria-label','Row density: '+(c?'comfortable':'compact')+'; activate to switch');};syncD();
 dens.addEventListener('click',()=>{const c=document.documentElement.getAttribute('data-density')==='comfortable';const next=c?'compact':'comfortable';document.documentElement.setAttribute('data-density',next);try{localStorage.setItem('mi-density',next);}catch(err){}document.querySelectorAll('.tbl-tools [aria-pressed]').forEach(b=>{if(b!==dens)b.dispatchEvent(new Event('syncd'));});syncD();});
 dens.addEventListener('syncd',syncD);tools.appendChild(det);tools.appendChild(dens);const w=t.closest('.tblwrap')||t;w.insertAdjacentElement('beforebegin',tools);t._applyCols();}
// ---------- minimal markdown (headings, bullets, bold, code, pipe tables) for methodology text
function mdLite(md){const out=[];let list=null,tbl=null,para=[];const inl=s=>esc(s).replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>').replace(/`([^`]+)`/g,'<code>$1</code>');
 const flushP=()=>{if(para.length){out.push('<p>'+inl(para.join(' '))+'</p>');para=[];}};const flushL=()=>{if(list){out.push('<ul>'+list.map(x=>'<li>'+inl(x)+'</li>').join('')+'</ul>');list=null;}};
 const flushT=()=>{if(tbl){const rows=tbl.filter(r=>!/^\s*\|?\s*:?-{2,}/.test(r));const cells=r=>r.replace(/^\s*\|/,'').replace(/\|\s*$/,'').split('|').map(c=>c.trim());const h=rows.length?cells(rows[0]):[];
  out.push('<div class="tblwrap"><table><thead><tr>'+h.map(c=>`<th scope="col">${inl(c)}</th>`).join('')+'</tr></thead><tbody>'+rows.slice(1).map(r=>'<tr>'+cells(r).map(c=>`<td${/^-?[\d.]+%?$/.test(c)?' class="num"':''}>${inl(c)}</td>`).join('')+'</tr>').join('')+'</tbody></table></div>');tbl=null;}};
 String(md||'').split(/\r?\n/).forEach(line=>{const l=line.trimEnd();if(/^\s*\|/.test(l)){flushP();flushL();(tbl=tbl||[]).push(l);return;}flushT();
  const h=/^(#{1,6})\s+(.*)$/.exec(l);if(h){flushP();flushL();const n=Math.min(3,h[1].length);out.push(`<h${n}>${inl(h[2])}</h${n}>`);return;}
  const b=/^\s*[-*]\s+(.*)$/.exec(l);if(b){flushP();(list=list||[]).push(b[1]);return;}if(!l.trim()){flushP();flushL();return;}flushL();para.push(l.trim());});
 flushP();flushL();flushT();return '<div translate="no">'+out.join('')+'</div>';}


// ----------------------------------------------------------------------------- period windows (3M / 1Y / 3Y / ...)
// Charting software lets you re-cut the record over standard horizons; a single since-inception number hides
// whether an edge is recent or ancient. Metrics are recomputed from the daily wealth path, client side.
const PERIODS=[['3M',63],['6M',126],['YTD','ytd'],['1Y',252],['3Y',756],['5Y',1260],['ALL','all']];
function periodStartIndex(dates,key){const n=dates.length;if(!n)return 0;
 if(key==='all')return 0;
 if(key==='ytd'){const y=dates[n-1].slice(0,4);const i=dates.findIndex(d=>d.slice(0,4)===y);return i<0?0:Math.max(0,i-1);}
 return Math.max(0,n-1-Number(key));}
function windowMetrics(dates,cum,i0,rf){
 // cum is a cumulative wealth path (1.0 = start). Rebase to the window and recompute from daily returns.
 const c=cum.slice(i0).filter(v=>v!=null&&isFinite(v));if(c.length<5)return null;
 const base=c[0];if(!base)return null;
 const reb=c.map(v=>v/base);const r=[];for(let i=1;i<reb.length;i++)r.push(reb[i]/reb[i-1]-1);
 const n=r.length,yrs=n/252;const mean=r.reduce((a,b)=>a+b,0)/n;
 const sd=Math.sqrt(r.reduce((a,b)=>a+(b-mean)**2,0)/Math.max(n-1,1));
 const vol=sd*Math.sqrt(252);const total=reb[reb.length-1]-1;
 const cagr=yrs>0?Math.pow(reb[reb.length-1],1/yrs)-1:null;
 let peak=-Infinity,mdd=0;for(const v of reb){if(v>peak)peak=v;const d=v/peak-1;if(d<mdd)mdd=d;}
 const neg=r.filter(x=>x<0);const dsd=neg.length>2?Math.sqrt(neg.reduce((a,b)=>a+b*b,0)/neg.length)*Math.sqrt(252):null;
 return {cagr,vol,sharpe:vol?((cagr-rf)/vol):null,sortino:dsd?((cagr-rf)/dsd):null,max_drawdown:mdd,
  total_return:total,calmar:mdd?cagr/Math.abs(mdd):null,n_days:n,rebased:reb,
  drawdown:(()=>{let pk=-Infinity;return reb.map(v=>{if(v>pk)pk=v;return v/pk-1;});})()};}
function renderPeriodBar(el,active,onPick,nDays){if(!el)return;
 // dim horizons the record cannot cover, rather than silently clamping them to the full window
 const avail=k=>(nDays==null||k==='all'||k==='ytd')?true:Number(k)<=nDays-5;
 el.innerHTML=PERIODS.map(([l,k])=>{const ok=avail(k);
  return `<button type="button" class="chip btn ${String(k)===String(active)?'on':''}" data-p="${k}" ${ok?'':'disabled'} aria-pressed="${String(k)===String(active)}" title="${ok?'':'not enough history in this record'}">${l}</button>`;}).join('');
 el.querySelectorAll('button:not([disabled])').forEach(b=>b.addEventListener('click',()=>onPick(b.dataset.p)));}
function periodLabel(key){const f=PERIODS.find(([,k])=>String(k)===String(key));return f?f[0]:String(key);}

/* ============================================================================
   Risk-by-construction matrix - shared by /risk and /constructions.

   One component, because two pages must not be able to disagree about what a
   construction's VaR is.

   Two-tier loading, by design:
     * ONE automatic all-construction request, always with reps=0. That tier is
       deterministic - VaR, ES and tracking error are exact for the sample and need
       no resampling - so every row is on screen immediately.
     * The bootstrap interval is expensive, so it is fetched per row, only for the
       construction the user selects, and only for the layer and window in view.
   A row never blanks while its interval is computing, and an interval never wears
   the "official" badge unless the payload itself says the tier was official.
   ============================================================================ */
const RISK_LAYERS=[['model','Model (fully invested)'],
                   ['vol_target_scenario','Common-vol-target scenario (hypothetical)'],
                   ['actual_run','Actual landed run book']];
const RISK_WINDOWS=[['1y','1Y'],['3y','3Y']];
const RISK_BENCH=[['te_mandate','Formal mandate benchmark'],
                  ['te_proxy','Equal-weight research proxy'],
                  ['te_index','SOXX index proxy']];
let _RMX=null,_RMXerr=null;
const _RMXci=new Map();        // key -> {state:'pending'|'done'|'unavailable'|'error'|'stale', row?, error?, tier?}
const _RMXreq=new Map();       // key -> AbortController; ALSO the ownership token for that key

/* Ownership, not generations.
   An interval depends on construction + layer + window. It does NOT depend on which benchmark the tracking-
   error column happens to be showing, so the benchmark is deliberately absent from the key - and a benchmark
   change must never strand an in-flight request. Every mutation of _RMXci is guarded by "am I still the
   controller registered for this key", which makes latest-wins correct without a global counter that a
   benchmark change could trip. */
function rmxCIState(name,layer,win){return _RMXci.get(rmxKey(name,layer,win))||null;}

/* Lineage the selected payload must share with the deterministic row it will sit beside.
   config_hash is deliberately excluded: the two requests legitimately differ in reps and window count, so
   their config hashes always differ and comparing them would either never fire or fire always. */
const RMX_LINEAGE=['data_snapshot_hash','portfolio_weights_hash','calculator_hash','asof','acquired_at'];
function rmxLineageMismatch(det,sel,wk){
 if(!det||!sel)return null;
 const diffs=[];
 RMX_LINEAGE.forEach(f=>{const a=det[f],b=sel[f];if(a!=null&&b!=null&&a!==b)diffs.push(f);});
 const f=wk+'_calc_sample_hash',a=det[f],b=sel[f];
 if(a!=null&&b!=null&&a!==b)diffs.push(f);
 return diffs.length?diffs:null;
}

function rmxKey(name,layer,win){return `${name}|${layer}|${win}|${(_RMX&&_RMX.config_hash)||'-'}`;}
function rmxNum(v,d=2){return (v===null||v===undefined||Number.isNaN(v))?null:(v*100).toFixed(d)+'%';}

function rmxCell(v,unavailable,reason){
 if(unavailable)return `<td class="num"><span class="na" title="${esc(reason||'unavailable')}">unavailable</span></td>`;
 const s=rmxNum(v);
 return s===null?`<td class="num"><span class="na" title="not computable for this row">n/a</span></td>`
                :`<td class="num">${s}</td>`;
}

/* The CI cell is the only part of a row that changes after first paint. */
function rmxCIContent(r,sel){
 const wk=sel.win;
 if(!r.layer_available)return `<span class="na">–</span>`;
 if(!r[wk+'_available'])
  return `<span class="na" title="${esc(r[wk+'_reason']||'')}">unavailable</span>`;
 const key=rmxKey(r.construction,sel.layer,wk);
 const st=_RMXci.get(key);
 if(st&&st.state==='pending')
  return `<span class="rmx-pending" role="status">computing…</span>`;
 if(st&&st.state==='stale')
  // the selected payload was computed against different data than the row it would sit beside; showing the
  // interval anyway would silently mix two snapshots in one line
  return `<span class="na" title="lineage differs from the deterministic row: ${esc(st.error||'')}. `+
         `Showing this interval beside the current point estimates would mix two snapshots.">snapshot changed — reload</span> `+
         `<button type="button" class="linkish" onclick="location.reload()" `+
         `aria-label="${esc(t('Reload to get a consistent snapshot for {name}',{name:r.construction}))}">reload</button>`;
 if(st&&st.state==='error')
  return `<span class="na" title="${esc(st.error||'')}">failed</span> `+
         `<button type="button" class="linkish rmx-ci-btn" data-c="${esc(r.construction)}" `+
         `aria-label="${esc(t('Retry official confidence interval for {name}',{name:r.construction}))}">retry</button>`;
 const src=(st&&(st.state==='done'||st.state==='unavailable')&&st.row)?st.row:r;
 const lo=src[wk+'_var99_ci_lo'],hi=src[wk+'_var99_ci_hi'];
 if(lo==null||hi==null){
  const why=src[wk+'_ci_reason']||'not computed in the deterministic tier; the point estimates on this row are exact for this sample';
  if(st&&st.state==='unavailable')
   return `<span class="na" title="${esc(why)}">unavailable</span>`;
  return `<span class="na" title="${esc(why)}">not computed</span> `+
         `<button type="button" class="linkish rmx-ci-btn" data-c="${esc(r.construction)}" `+
         `aria-label="${esc(t('Compute official confidence interval for {name}',{name:r.construction}))}">compute</button>`;
 }
 const ciTier=src[wk+'_ci_tier']||'';
 const conf=src[wk+'_ci_confidence'];
 const tip=`${conf!=null?(conf*100).toFixed(0)+'% ':''}interval around the ESTIMATE (not the VaR confidence level)`+
   ` · ${src[wk+'_ci_method']||'bootstrap'} · ${src[wk+'_ci_reps']} reps · block ${src[wk+'_ci_block']} · ${ciTier} tier`;
 // the badge is read from the payload, never assumed: only an exactly-official tier is styled as official
 const badge=`<span class="badge ${ciTier==='official'?'pass':'na'}" title="${esc(tip)}">${esc(ciTier||'unlabelled')}</span>`;
 return `<span title="${esc(tip)}">${rmxNum(lo)} – ${rmxNum(hi)}</span> ${badge}`;
}

function rmxPaintCI(id,r,sel){
 const tbl=document.getElementById(id+'-tbl');
 if(!tbl)return;
 const tr=tbl.querySelector(`tr[data-c="${(window.CSS&&CSS.escape)?CSS.escape(r.construction):r.construction}"]`);
 if(!tr)return;
 const td=tr.querySelector('td.rmx-ci');
 if(!td)return;
 const st=_RMXci.get(rmxKey(r.construction,sel.layer,sel.win));
 td.innerHTML=rmxCIContent(r,sel);
 td.setAttribute('aria-busy',st&&st.state==='pending'?'true':'false');
}

/** The single state-transition notifier: every pending / done / error / stale / aborted transition goes
    through here, so the matrix cell and any listening detail panel can never disagree about a cell's state.
    Currency is decided from layer|window alone - the pair the interval actually depends on. */
function rmxNotify(id,r,view,curTag,onci){
 if(curTag&&curTag()!==`${view.layer}|${view.win}`)return;  // another layer/window owns the screen
 rmxPaintCI(id,r,view);
 if(onci)onci(r.construction,view,_RMXci.get(rmxKey(r.construction,view.layer,view.win))||null);
}

async function rmxFetchCI(id,r,sel,curTag,onci){
 // The view this request belongs to is captured now: `sel` is a live object the control handlers mutate,
 // so reading it after the await would attribute the reply to whatever the user has since selected.
 const name=r.construction,layer=sel.layer,win=sel.win,view={layer,win,bench:sel.bench};
 const key=rmxKey(name,layer,win);
 const done=_RMXci.get(key);
 if(done&&(done.state==='done'||done.state==='unavailable'))return; // terminal for this data/config snapshot
 const prev=_RMXreq.get(key);
 if(prev)prev.abort();                                      // latest wins for this cell
 const ctrl=new AbortController();
 _RMXreq.set(key,ctrl);
 _RMXci.set(key,{state:'pending'});
 rmxNotify(id,r,view,curTag,onci);                          // pending is published BEFORE any await
 announce(t('Computing official confidence interval for {name}',{name}));
 let outcome=null;                                          // an object to store, or 'aborted'
 try{
  const url=`/api/research/risk/constructions/${encodeURIComponent(name)}?reps=2000`+
            `&layer=${encodeURIComponent(layer)}&window=${encodeURIComponent(win)}`;
  const resp=await fetch(url,{signal:ctrl.signal});
  if(!resp.ok)throw new Error('HTTP '+resp.status);
  const d=await resp.json();
  if(!d||!d.comparison)
   throw new Error('this build of the API returned no per-construction comparison row '+
                   '(the running service may predate the selected-CI endpoint)');
  const bad=rmxLineageMismatch(r,d.comparison,win);
  const row=d.comparison;
  const intervalAvailable=row[win+'_var99_ci_lo']!=null&&row[win+'_var99_ci_hi']!=null;
  outcome=bad?{state:'stale',error:bad.join(', ')}
             :intervalAvailable?{state:'done',row:row,tier:d.precision_tier}
                                :{state:'unavailable',row:row,tier:d.precision_tier,
                                  error:row[win+'_ci_reason']||'official interval unavailable for this sample'};
 }catch(e){
  outcome=(e.name==='AbortError')?'aborted':{state:'error',error:e.message};
 }
 // Only the request still registered for this key may write or clear it. A superseded request finishing
 // late - including one rejecting with AbortError - must not touch the newer request's pending state.
 if(_RMXreq.get(key)!==ctrl)return;
 _RMXreq.delete(key);
 if(outcome==='aborted'){
  // An abandoned request must not leave the cell asserting work that is no longer happening. Back to idle,
  // so returning to this view offers "compute" again instead of a frozen "computing…".
  if((_RMXci.get(key)||{}).state==='pending')_RMXci.delete(key);
 }else{
  _RMXci.set(key,outcome);
 }
 // Repaint whenever THIS layer|window is the one on screen - including after abort cleanup, which is how a
 // fast 1Y -> 3Y -> 1Y round trip gets its cell reset rather than left pending.
 rmxNotify(id,r,view,curTag,onci);
 const st=_RMXci.get(key);
 if(outcome!=='aborted')
  announce(t(st&&st.state==='done'?'Confidence interval ready for {name}':'Confidence interval unavailable for {name}',{name}));
}

function riskMatrixControls(id,sel){
 const g=(name,items,cur,label)=>
   `<span class="rmx-field"><label class="rmx-lab" for="${id}-${name}">${label}</label>`+
   `<select id="${id}-${name}" class="rmx-sel">`+
   items.map(([k,t])=>`<option value="${k}"${k===cur?' selected':''}>${esc(t)}</option>`).join('')+
   `</select></span>`;
 return `<div class="rmx-ctl" role="group" aria-label="Risk matrix view">`+
   g('w',RISK_WINDOWS,sel.win,'Window')+g('l',RISK_LAYERS,sel.layer,'Layer')+g('b',RISK_BENCH,sel.bench,'Benchmark')+
   `</div><p class="small" id="${id}-status" role="status" aria-live="polite"></p>`;
}

function riskMatrixCoreRender(host,id,sel,rows,onpick,curTag,onci,opts,ind,benchLabel){
 const wk=sel.win;
 const na=reason=>`<span class="na" title="${esc(reason||'unavailable')}">unavailable</span>`;
 const value=(v,available,reason)=>available?(rmxNum(v)||'<span class="na">n/a</span>'):na(reason);
 const rowParts=rows.map(r=>{
  const flags=[];
  if(r.is_alias)flags.push(`<span class="badge na" title="${esc(r.alias_basis||'')}">alias of ${esc(r.alias_of)}</span>`);
  else if(r.layer_equivalent_to)flags.push(`<span class="badge na" title="${esc(r.layer_equivalent_basis||'')}">≡ ${esc(r.layer_equivalent_to)}</span>`);
  if(r.is_canonical_executable)flags.push(`<span class="badge na">${sel.layer==='actual_run'?'published target book':'canonical source'}</span>`);
  if(r.is_hypothetical)flags.push('<span class="badge na" title="recalculated here; not a book any run executed">hypothetical</span>');
  const flagHtml=flags.join(' ');
  const layerAvailable=!!r.layer_available;
  const available=layerAvailable&&!!r[wk+'_available'];
  const reason=layerAvailable?(r[wk+'_reason']||''):(r.layer_reason||'layer unavailable');
  const limiting=r[wk+'_limiting_holding'];
  const hasGaps=(r[wk+'_n_failed_1d']||0)>0||(r[wk+'_n_failed_10d']||0)>0;
  const status=available?`<span class="badge na">${esc(wk.toUpperCase())} ${hasGaps?'sample gaps':'available'}</span>`
   :`<span class="badge na" title="${esc(reason)}">${layerAvailable?esc(wk.toUpperCase())+' unavailable':'layer unavailable'}${limiting?' · '+esc(limiting):''}</span>`;
  const teStatus=r[`${wk}_${sel.bench}_status`];
  const teValue=!available?na(reason):(teStatus&&teStatus!=='available')
   ?`<span class="na" title="no formal mandate benchmark is defined; a research proxy is not a substitute">${esc(teStatus)}</span>`
   :(rmxNum(r[`${wk}_${sel.bench}`])||'<span class="na">n/a</span>');
  const nameButton=`<button type="button" class="linkish rmx-pick" data-c="${esc(r.construction)}" `+
   `aria-label="Select ${esc(r.construction)} and compute its official confidence interval">${esc(r.construction)}</button>`;
  const statusWithFlags=`${status}${flagHtml?'<span class="rmx-inline-flags">'+flagHtml+'</span>':''}`;
  const table=`<tr class="row" data-c="${esc(r.construction)}" aria-selected="${r.construction===opts.selectedName}">`+
   `<th scope="row" class="rmx-name">${nameButton}</th><td>${statusWithFlags}</td>`+
   `<td class="num">${value(r[wk+'_var99_1d'],available,reason)}</td>`+
   `<td class="num">${value(r[wk+'_var99_10d_overlap'],available,reason)}</td>`+
   `<td class="num">${value(r[wk+'_es975_10d_overlap'],available,reason)}</td>`+
   `<td class="num">${teValue}</td>`+
   `<td class="rmx-extra">${flagHtml||'<span class="na">–</span>'}</td>`+
   `<td class="num rmx-extra">${value(r[wk+'_es975_1d'],available,reason)}</td>`+
   `<td class="num rmx-ci rmx-extra">${available?rmxCIContent(r,sel):na(reason)}</td>`+
   `<td class="num small rmx-extra">${available?`${r[wk+'_n_obs_1d']??'–'} / ${r[wk+'_n_obs_10d_overlap']??'–'}`:na(reason)}</td>`+
   `<td class="small rmx-extra">${available?esc(r[wk+'_end']||''):na(reason)}</td>`+
   `<td class="num rmx-extra">${r.cash_weight===null||r.cash_weight===undefined?'<span class="na">–</span>':(r.cash_weight*100).toFixed(1)+'%'}</td></tr>`;
  const card=`<article class="rmx-card" role="listitem" data-c="${esc(r.construction)}" aria-selected="${r.construction===opts.selectedName}">`+
   `<div class="rmx-card-head"><b>${nameButton}</b>${status}</div>`+
   `${flagHtml?'<div class="rmx-card-flags">'+flagHtml+'</div>':''}`+
   `<dl><dt>VaR99 1D</dt><dd>${value(r[wk+'_var99_1d'],available,reason)}</dd>`+
   `<dt>VaR99 10D</dt><dd>${value(r[wk+'_var99_10d_overlap'],available,reason)}</dd>`+
   `<dt>ES97.5 1D</dt><dd>${value(r[wk+'_es975_1d'],available,reason)}</dd>`+
   `<dt>ES97.5 10D</dt><dd>${value(r[wk+'_es975_10d_overlap'],available,reason)}</dd>`+
   `<dt>TE (${esc(benchLabel)})</dt><dd>${teValue}</dd></dl></article>`;
  return {table,card};
 });
 const full=!!opts.fullDetail;
 const head=`<tr><th scope="col">Construction</th><th scope="col">Status</th>`+
  `<th scope="col" class="num">VaR99 1D</th><th scope="col" class="num">VaR99 10D</th>`+
  `<th scope="col" class="num">ES97.5 10D</th><th scope="col" class="num">TE (${esc(wk.toUpperCase())})</th>`+
  `<th scope="col" class="rmx-extra">Flags</th><th scope="col" class="num rmx-extra">ES97.5 1D</th>`+
  `<th scope="col" class="num rmx-extra">VaR99 10D CI</th><th scope="col" class="num rmx-extra">Obs 1D/10D</th>`+
  `<th scope="col" class="rmx-extra">As-of</th><th scope="col" class="num rmx-extra">Cash</th></tr>`;
 const foot=[
  'Losses are positive fractions of NAV, frozen positions, historical simulation.',
  'A window a book cannot support is reported unavailable with the limiting holding named — never shortened, never zero.',
  '"alias" = same published weights under another name; "≡" = identical only within this layer.',
  'The CI is an interval around the ESTIMATE, not the VaR confidence level.'
 ].map(s=>'<span>'+esc(t(s))+'</span>').join(' ')+(ind.note?' <span translate="no">'+esc(ind.note)+'</span>':'');
 host.innerHTML=`<div class="rmx-core-actions"><button type="button" class="chip btn sm rmx-detail-toggle" `+
  `aria-expanded="${full}">${full?'Show core metrics':'Show full detail'}</button></div>`+
  `<div class="rmx-core-shell${full?' rmx-full':''}"><div class="tblwrap rmx-core-table-wrap">`+
  `<table id="${id}-tbl" class="rmx-tbl rmx-core-tbl"><caption class="sr-only">Risk by construction: core VaR, `+
  `Expected Shortfall and tracking error. Use Show full detail for flags, one-day Expected Shortfall, confidence intervals, `+
  `sample sizes, dates and cash.</caption><thead>${head}</thead><tbody>${rowParts.map(x=>x.table).join('')}</tbody></table></div>`+
  `<div class="rmx-cards" role="list">${rowParts.map(x=>x.card).join('')}</div></div>`+
  `<details class="rmx-foot"><summary>Metric notes and availability</summary><p class="small" id="${id}-foot">${foot}</p></details>`;
 const shell=host.querySelector('.rmx-core-shell');
 const toggle=host.querySelector('.rmx-detail-toggle');
 toggle.addEventListener('click',()=>{
  opts.fullDetail=!opts.fullDetail;
  shell.classList.toggle('rmx-full',opts.fullDetail);
  toggle.setAttribute('aria-expanded',String(opts.fullDetail));
  toggle.textContent=opts.fullDetail?'Show core metrics':'Show full detail';
 });
 const pick=el=>{const n=el.dataset.c;const r=rows.find(x=>x.construction===n);if(!r)return;
  if(onpick)onpick(n,sel);
  if(r.layer_available&&r[wk+'_available'])rmxFetchCI(id,r,sel,curTag,onci);};
 host.querySelectorAll('.rmx-pick,.rmx-ci-btn').forEach(b=>b.addEventListener('click',()=>pick(b)));
}

function riskMatrixRender(root,id,sel,onpick,curTag,onci,opts={}){
 const d=_RMX;
 const st=document.getElementById(id+'-status');
 const host=document.getElementById(id+'-body');
 if(!host)return;
 if(!d||!d.available){
 host.innerHTML=`<p class="small na">${esc(t('Risk by construction is unavailable: {reason}',{reason:(d&&d.reason)||_RMXerr||'no data'}))}</p>`;
  if(st)st.textContent='';
  return;
 }
 const sourceRows=d['comparison_'+sel.layer]||[];
 const rows=opts.orderRows?opts.orderRows(sourceRows,sel):sourceRows;
 const wk=sel.win;
 const ind=(d.independence||{})[sel.layer]||{};
 const acq=(d.acquisition||{}).acquired_at||'';
 const asof=(rows.find(r=>r.asof)||{}).asof||'';
 if(!rows.length){
  host.innerHTML=`<p class="small na">No constructions in this run.</p>`;
  if(st)st.textContent='';
  return;
 }
 const indTxt=ind.n_unavailable_labels
   ?t('{available} with a book in this layer → {independent} independent · {unavailable} unavailable',{available:ind.n_available_labels,independent:ind.n_independent_portfolios,unavailable:ind.n_unavailable_labels})
   :t('{count} independent portfolios',{count:ind.n_independent_portfolios||'?'});
 if(st)st.innerHTML=`<span>${esc(t('{count} labels · {independence} · confirmed through {asof}',{count:rows.length,independence:indTxt,asof}))}</span>`+
   `${acq?'<span>'+esc(t(' (snapshot acquired {time})',{time:acq}))+'</span>':''} · <b>${esc(d.precision_tier||'')}</b> <span>point estimates</span> · `+
   `<span>intervals are computed per row on request</span>`;
 const benchLabel=(RISK_BENCH.find(b=>b[0]===sel.bench)||[])[1]||sel.bench;
 if(opts.coreView){
  riskMatrixCoreRender(host,id,sel,rows,onpick,curTag,onci,opts,ind,benchLabel);
  return;
 }
 const head=`<tr><th scope="col">Construction</th><th scope="col">Flags</th><th scope="col">Status</th>`+
   `<th scope="col" class="num">VaR99 1D</th><th scope="col" class="num">VaR99 10D</th>`+
   `<th scope="col" class="num">ES97.5 1D</th><th scope="col" class="num">ES97.5 10D</th>`+
   `<th scope="col" class="num">VaR99 10D CI</th>`+
   `<th scope="col" class="num">TE (${esc(wk.toUpperCase())}, ${esc(benchLabel)})</th>`+
   `<th scope="col" class="num">Obs 1D/10D</th><th scope="col">As-of</th><th scope="col" class="num">Cash</th></tr>`;
 const body=rows.map(r=>{
  const flags=[];
  if(r.is_alias)flags.push(`<span class="badge na" title="${esc(r.alias_basis||'')}">alias of ${esc(r.alias_of)}</span>`);
  else if(r.layer_equivalent_to)flags.push(`<span class="badge na" title="${esc(r.layer_equivalent_basis||'')}">≡ ${esc(r.layer_equivalent_to)}</span>`);
  if(r.is_canonical_executable)flags.push('<span class="badge na">run book</span>');
  if(r.is_hypothetical)flags.push('<span class="badge na" title="recalculated here; not a book any run executed">hypothetical</span>');
  const nameCell=`<th scope="row" class="rmx-name"><button type="button" class="linkish rmx-pick" data-c="${esc(r.construction)}" `+
    `aria-label="Select ${esc(r.construction)} and compute its official confidence interval">${esc(r.construction)}</button></th>`;
  if(!r.layer_available){
   return `<tr class="row" data-c="${esc(r.construction)}">${nameCell}<td>${flags.join(' ')}</td>`+
     `<td colspan="10"><span class="na" title="${esc(r.layer_reason||'')}">layer unavailable — ${esc((r.layer_reason||'').slice(0,110))}</span></td></tr>`;
  }
  const avail=r[wk+'_available'];
  const reason=r[wk+'_reason']||'';
  const lim=r[wk+'_limiting_holding'];
  const status=avail?`<span class="badge pass">${esc(wk.toUpperCase())} available</span>`
   :`<span class="badge na" title="${esc(reason)}">${esc(wk.toUpperCase())} unavailable${lim?' · '+esc(lim):''}</span>`;
  const teStat=r[`${wk}_${sel.bench}_status`];
  const teCell=(!avail)?`<td class="num"><span class="na" title="${esc(reason)}">unavailable</span></td>`
   :(teStat&&teStat!=='available')
    ?`<td class="num"><span class="na" title="no formal mandate benchmark is defined; a research proxy is not a substitute">${esc(teStat)}</span></td>`
    :rmxCell(r[`${wk}_${sel.bench}`],false);
  return `<tr class="row" data-c="${esc(r.construction)}">${nameCell}`+
   `<td>${flags.join(' ')}</td><td>${status}</td>`+
   rmxCell(r[wk+'_var99_1d'],!avail,reason)+
   rmxCell(r[wk+'_var99_10d_overlap'],!avail,reason)+
   rmxCell(r[wk+'_es975_1d'],!avail,reason)+
   rmxCell(r[wk+'_es975_10d_overlap'],!avail,reason)+
   `<td class="num rmx-ci">${rmxCIContent(r,sel)}</td>`+
   teCell+
   `<td class="num small">${avail?`${r[wk+'_n_obs_1d']??'–'} / ${r[wk+'_n_obs_10d_overlap']??'–'}`:'<span class="na">–</span>'}</td>`+
   `<td class="small">${esc(avail?(r[wk+'_end']||''):'')}</td>`+
   `<td class="num">${r.cash_weight===null||r.cash_weight===undefined?'<span class="na">–</span>':(r.cash_weight*100).toFixed(1)+'%'}</td></tr>`;
 }).join('');
 host.innerHTML=`<div class="tblwrap scroll"><table id="${id}-tbl" class="rmx-tbl">`+
  `<caption class="sr-only">Risk by construction: VaR, Expected Shortfall, estimate confidence interval and `+
  `tracking error per construction for the selected window, layer and benchmark. Point estimates load with the `+
  `page; confidence intervals are computed per row on request.</caption>`+
  `<thead>${head}</thead><tbody>${body}</tbody></table></div>`+
  `<p class="small" id="${id}-foot">`+[
   'Losses are positive fractions of NAV, frozen positions, historical simulation.',
   'A window a book cannot support is reported unavailable with the limiting holding named — never shortened, never zero.',
   '"alias" = same published weights under another name; "≡" = identical only within this layer.',
   'The CI column is an interval around the ESTIMATE, not the VaR confidence level, and is badged with the tier that produced it.'
  ].map(s=>'<span>'+esc(t(s))+'</span>').join(' ')+(ind.note?' <span translate="no">'+esc(ind.note)+'</span>':'')+'</p>';
 const pick=el=>{const n=el.dataset.c;const r=rows.find(x=>x.construction===n);if(!r)return;
   if(onpick)onpick(n,sel);
   if(r.layer_available&&r[wk+'_available'])rmxFetchCI(id,r,sel,curTag,onci);};
 host.querySelectorAll('.rmx-pick,.rmx-ci-btn').forEach(b=>b.addEventListener('click',()=>pick(b)));
}

async function riskMatrix(root,opts={}){
 const id=opts.id||'rmx';
 const sel={win:opts.win||'1y',layer:opts.layer||'model',bench:opts.bench||'te_proxy'};
 // currency is layer|window ONLY - the pair an interval depends on. Benchmark is excluded on
 // purpose: changing which tracking-error column is shown must never strand a running request.
 const curTag=()=>`${sel.layer}|${sel.win}`;
 root.innerHTML=riskMatrixControls(id,sel)+
   `<div id="${id}-body"><p class="small" role="status">Loading risk by construction…</p></div>`;
 const rerender=()=>riskMatrixRender(root,id,sel,opts.onpick,curTag,opts.onci,opts);
 document.addEventListener('languagechange',rerender);
 if(opts.bindRender)opts.bindRender(rerender);
 ['w','l','b'].forEach(k=>{
  const e=document.getElementById(id+'-'+k);
  e.addEventListener('change',()=>{
   const prevLayer=sel.layer,prevWin=sel.win;
   sel[{w:'win',l:'layer',b:'bench'}[k]]=e.value;
   if(sel.layer!==prevLayer||sel.win!==prevWin){
    // ONLY a layer or window change invalidates an in-flight interval. A benchmark change does not enter
    // the interval at all, so it neither aborts a request nor suppresses its repaint.
    _RMXreq.forEach((c,key)=>{if(!key.includes(`|${sel.layer}|${sel.win}|`))c.abort();});
   }
   rerender();
   if(opts.onchange)opts.onchange(sel);
  });
 });
 if(opts.data)_RMX=opts.data;
 if(!_RMX){
  try{
   // the ONLY automatic all-construction request, and it is always the deterministic tier
   const r=await fetch('/api/research/risk/constructions?reps=0');
   if(!r.ok)throw new Error('HTTP '+r.status);
   _RMX=await r.json();
  }catch(e){
   _RMXerr=e.message;
   const body=document.getElementById(id+'-body');
   if(body)body.innerHTML=
     `<p class="small na" role="alert">${esc(t('Could not load risk by construction: {error}.',{error:e.message}))} `+
     `<button type="button" class="linkish" onclick="location.reload()">retry</button></p>`;
   return null;
  }
 }
 rerender();
 if(opts.onchange)opts.onchange(sel);
 return _RMX;
}

/* Sharpe with its disclosure attached.
   Two pages both say "Sharpe": /portfolio's is an ex-ante forecast on today's book, /constructions' is a
   historical walk-forward ratio on a monthly-changing book. Different portfolio, window, constraints,
   risk-free rate and estimator. Printing the bare number invites a comparison that is not valid, so the
   number never appears without the terms that produced it. */
function sharpeTip(d){
 if(!d)return 'no disclosure attached to this Sharpe';
 const s=d.sample||{},c=d.constraints||{};
 return [d.estimator&&('estimator: '+d.estimator),
  d.formula&&('formula: '+d.formula),
  d.risk_free!=null&&('risk-free: '+(d.risk_free*100).toFixed(2)+'%'),
  d.horizon&&('horizon: '+d.horizon),
  (s.n_days||s.start)&&('sample: '+(s.n_days||'?')+' days'+(s.start?' ('+s.start+' → '+s.end+')':'')+(s.n_rebalances!=null?', '+s.n_rebalances+' rebalances':'')),
  d.portfolio&&('portfolio: '+d.portfolio),
  d.portfolio_layer&&('layer: '+d.portfolio_layer),
  d.objective&&('objective: '+d.objective+(d.objective_reads_expected_return===false?' (risk-only: ignores expected return)':'')),
  (c.max_weight!=null)&&('constraints: min '+((c.min_weight||0)*100).toFixed(0)+'% / max '+(c.max_weight*100).toFixed(0)+'%'),
  d.costs&&('costs: '+d.costs),
  d.rebalancing&&('rebalancing: '+d.rebalancing),
  '',d.comparability].filter(v=>v!==false&&v!=null).join('\n');
}
function sharpeCell(st,digits=2){
 if(!st)return '<span class="na">n/a</span>';
 const held=(st.withheld||{}).sharpe;
 const direct=st.sharpe;
 const v=(direct!==undefined&&direct!==null)?direct:held;
 if(v===undefined||v===null||Number.isNaN(v))return '<span class="na" title="not computable">n/a</span>';
 const diag=(direct===undefined||direct===null);
 const tip=sharpeTip(st.sharpe_disclosure);
 return `<span title="${esc(tip)}" style="border-bottom:1px dotted var(--ink2);cursor:help">${Number(v).toFixed(digits)}</span>`+
  (diag?` <span class="badge na" title="the point-in-time data contract is not satisfied, so this is a mechanism diagnostic, not a track record">diag</span>`:'');
}
