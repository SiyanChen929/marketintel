(function(){
'use strict';
// Translate display text only; archived identifiers, notes and numerical values stay unchanged.
const tr=(s,v={})=>typeof globalThis.t==='function'?globalThis.t(s,v):s.replace(/\{(\w+)\}/g,(_,k)=>v[k]??'{'+k+'}');
const T=(s,v)=>E(tr(s,v));
const E=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
const pct=v=>number(v)?(Number(v)*100).toFixed(2)+'%':'—';
const delta=v=>number(v)?(v>0?'+':'')+tr('{value} 个百分点',{value:(Number(v)*100).toFixed(2)}):'—';
const time=s=>{if(!s)return tr('未记录');const d=new Date(s);return Number.isNaN(d.getTime())?s:new Intl.DateTimeFormat(globalThis.MI_I18N?.language||'zh-CN',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d)+' ET';};
const labels={initial:'初始目标',unknown:'无法比较',entry:'新增',exit:'移出',increase:'上调',decrease:'下调',unchanged:'不变'};
const clockLabels={OPEN:'时窗开放',PENDING_OPEN:'等待开盘',EXPIRED:'已过期',BLOCKED:'生成条件未通过',INVALID_POLICY:'时间无法核验',MISSING:'无记录'};
const execLabels={paper_fills:'有模拟成交',orders_only:'仅有模拟订单',no_fills:'未记录模拟成交',unknown:'成交状态未知',tracking_only:'独立目标记录'};
function filterItems(items,f){return items.filter(r=>(f.versions||r.canonical)&&(!f.from||r.date>=f.from)&&(!f.to||r.date<=f.to)&&(!f.ticker||(r.targets||[]).some(t=>t.ticker.toUpperCase().includes(f.ticker.toUpperCase()))));}
function detailHTML(r){
 if(!r.available)return '<p class="rb-empty">'+E(r.unavailable_reason||'此版本归档缺失或不一致，无法展示明细。')+'</p>';
 const basis=r.prior_date?T('比较基准：{date} 的目标组合',{date:r.prior_date}):T(r.comparison_basis==='initial'?'首条归档，无前一目标可比较':'前一目标归档缺失，变化值不可用');
 const rows=[...(r.targets||[])].sort((a,b)=>Math.abs(b.delta??0)-Math.abs(a.delta??0)||b.after-a.after);
 const body=rows.map(t=>`<tr><td><a href="/ticker/${encodeURIComponent(t.ticker)}">${E(t.ticker)}</a></td><td>${T(labels[t.action]||t.action)}</td><td class="rb-amount">${pct(t.before)}</td><td class="rb-amount">${pct(t.after)}</td><td class="rb-amount ${t.delta>0?'rb-positive':t.delta<0?'rb-negative':''}">${delta(t.delta)}</td></tr>`).join('');
 const fills=r.fills||[];
 const fillHTML=fills.length?`<p class="rb-fill-note">以下为模拟成交记录；部分成交不代表目标组合已全部完成。不同模拟账户分别列示。</p><div class="rb-scroll"><table><thead><tr><th>时间（美东）</th><th>股票 / 方向</th><th>数量</th><th>价格 / 币种</th><th>总执行成本 / 现金佣金</th><th>模拟账户</th></tr></thead><tbody>${fills.map(f=>`<tr><td>${E(time(f.fill_time))}</td><td>${E(f.symbol)} · ${T(f.side)}</td><td>${E(f.quantity)}</td><td>${E(f.price)} ${E(f.currency)}</td><td>${E(f.total_cost??'未记录')}<br><span class="rb-time">${T('佣金 {value}',{value:f.commission??tr('未记录')})}</span></td><td>${E(f.session_id)}<br><span class="rb-time">${T(f.checkpoint_verified?'独立账本检查通过':'独立账本检查未确认')}</span></td></tr>`).join('')}</tbody></table></div>`:`<p class="rb-fill-note">${T(execLabels[r.execution_status]||'成交状态未知')}。${T(r.execution_status==='no_fills'?'当前可读取的模拟账本没有关联此版本的成交。':'目标生成、通过数据检查或版本确认均不代表已成交。')}</p>`;
 return `<div class="chips"><span class="rb-badge">${E(r.construction_label||r.construction||tr('主组合'))}</span><span class="rb-badge">${T(r.weight_basis==='model'?'股票内部权重':'含现金账户目标')}</span><span class="rb-badge">${T(r.canonical?'每日最终版本':'同日旧版本')}</span><span class="rb-badge">${T(execLabels[r.execution_status])}</span><span class="rb-badge">${T(r.policy.frequency==='daily'?'日频目标':'历史目标')}</span></div>
 <div class="rb-meta"><span>${T('生成：{time}',{time:time(r.generated_at)})}</span><span>${T('行情截止：{date}',{date:r.price_session||tr('未记录')})}</span><span>${T('计划执行：{date}',{date:r.policy.execution_session||tr('未记录')})}</span></div>
 <p class="small">${T('原记录执行时窗：{start} → {end}；{status}。仅检查原记录时窗，不代表账户可下单。',{start:time(r.policy.not_before),end:time(r.policy.expires_at),status:tr(clockLabels[r.current_target_status?.status]||'时间未核验')})}</p>
 <p class="small">${basis}${T('。目标调整比例 {turnover}，包含现金变化。',{turnover:pct(r.target_turnover)})}</p>
 ${r.construction_changed?`<p class="rb-warning">${T('主组合在此版本切换方法：{previous} → {current}。各方法的独立历史仍保留。',{previous:r.previous_construction,current:r.construction})}</p>`:''}
 <div class="rb-diff"><span>${T('新增：{names}',{names:r.entries.join('、')||tr('无')})}</span><span>${T('移出：{names}',{names:r.exits.join('、')||tr('无')})}</span></div>
 <div class="rb-scroll"><table><thead><tr><th>股票</th><th>变化</th><th>前次目标</th><th>本次目标</th><th>权重变化</th></tr></thead><tbody>${body}<tr class="rb-cash"><td>${T('现金')}</td><td>${T('目标')}</td><td class="rb-amount">${pct(r.previous_cash)}</td><td class="rb-amount">${pct(r.cash)}</td><td class="rb-amount">${delta(r.cash_delta)}</td></tr></tbody></table></div>
 <h3>模拟成交</h3>${fillHTML}<a class="chip btn" href="/platform#paper-panel">查看模拟账户 →</a>
 <details><summary>归档与数据检查</summary><p>${T('版本：{id}',{id:r.run_id})}</p><p>${T('比较版本：{id}',{id:r.prior_run_id||tr('无')})}</p><p>${T('替代版本：{id}',{id:r.supersedes||tr('无')})}</p><p>${T('生成时数据检查：{status}',{status:tr(r.input_ready===true?'通过':r.input_ready===false?'未通过':'未记录')})}</p>${(r.reasons||[]).map(s=>'<p data-no-i18n>'+E(s)+'</p>').join('')}${r.archive_note?'<p data-no-i18n>'+E(r.archive_note)+'</p>':''}<p>这些状态仅说明目标记录，不能作为交易执行或收益验证。</p></details>`;
}
if(typeof module!=='undefined')module.exports={filterItems,detailHTML,pct,delta};
if(typeof document==='undefined')return;
const $=s=>document.querySelector(s);const initialQuery=new URLSearchParams(location.search);let data={items:[]},selected=initialQuery.get('run'),currentMethod=initialQuery.get('construction')||'active',currentBasis=initialQuery.get('basis')||'target';
function render(){
 const f={versions:$('#rb-versions').checked,from:$('#rb-from').value,to:$('#rb-to').value,ticker:$('#rb-ticker').value.trim()};
 const rows=filterItems(data.items,f);$('#rb-count').textContent=tr('{count} 条',{count:rows.length});
 if(!rows.some(r=>r.run_id===selected))selected=rows[0]?.run_id||null;
 $('#rb-list').innerHTML=rows.length?`<table><thead><tr><th>目标日期 / 生成时间</th><th>新增 / 移出</th><th>目标调整</th><th>模拟成交</th></tr></thead><tbody>${rows.map(r=>`<tr class="${r.run_id===selected?'rb-selected':''}"><td><div class="rb-date"><button class="chip btn" type="button" data-run="${E(r.run_id)}" aria-pressed="${r.run_id===selected}">${E(r.date)}</button><span class="rb-time">${E(time(r.generated_at).split(' ').slice(1).join(' '))} · ${T(r.canonical?'最终版本':'旧版本')}</span></div></td><td>${r.available?`${r.entries.length} / ${r.exits.length}`:T('归档缺失')}</td><td>${pct(r.target_turnover)}</td><td><span class="rb-badge">${T(execLabels[r.execution_status]||'无法核对')}</span></td></tr>`).join('')}</tbody></table>`:'<p class="rb-empty">没有符合筛选条件的记录。<br>尚未生成目标的日期不会出现在历史中。</p>';
 const r=rows.find(r=>r.run_id===selected);$('#rb-detail-title').textContent=r?tr('{date} · 目标变化',{date:r.date}):tr('选择一条记录');
 $('#rb-detail').innerHTML=r?detailHTML(r):'<p class="rb-empty">生成并归档目标后，可在这里查看逐股变化。</p>';
 const u=new URL(location.href);if(selected)u.searchParams.set('run',selected);else u.searchParams.delete('run');u.searchParams.set('construction',currentMethod);u.searchParams.set('basis',currentBasis);history.replaceState(null,'',u.pathname+u.search);
}
async function load(){
 busy($('#rb-refresh'),true,'读取中…');setPanel('#rb-list-panel','loading');
 try{data=await fetchJSON('/api/research/rebalances?construction='+encodeURIComponent(currentMethod)+'&basis='+encodeURIComponent(currentBasis),'rebalances');
  $('#rb-method').innerHTML=data.methods.map(m=>`<option value="${E(m.id)}">${E(m.label)}</option>`).join('');$('#rb-method').value=data.construction;$('#rb-basis').value=data.weight_basis;
  if(data.items.some(r=>r.run_id===selected&&!r.canonical))$('#rb-versions').checked=true;
  const latest=data.items.find(r=>r.canonical&&r.available);
  $('#rb-kpis').innerHTML=Kpi('本口径已归档日期',E(data.canonical_count),T('共 {count} 个版本',{count:data.version_count}))+Kpi('最近目标',E(latest?.date||'暂无'),'按生成日期展示')+Kpi('最新现金目标',pct(latest?.cash),currentBasis==='model'?'股票内部权重不配置现金':'随各方法风险水平更新')+Kpi('有模拟成交的版本',E(data.items.filter(r=>r.fills?.length).length),data.ledger_state==='inspected'?'当前可读取的模拟账本':'账本未完整读取');
  $('#rb-method-note').textContent=currentMethod==='active'?'主组合保留当时实际选用的方法及切换前后的目标；其他方法在各自历史中连续追踪。':'此方法与自身上一期同口径目标比较。账户目标采用各自波动率缩放；旧日期未归档的账户目标不补写，可查看股票内部权重。';
  $('#rb-warnings').innerHTML=data.warnings.map(s=>'<div class="rb-warning" data-no-i18n>'+E(s)+'</div>').join('');
  setPanel('#rb-list-panel','fresh');render();
 }catch(e){if(!e.stale){setPanel('#rb-list-panel','error','历史记录读取失败，请重试。',load);$('#rb-warnings').textContent='当前显示内容可能过期。';}}
 finally{busy($('#rb-refresh'),false);}
}
renderNav('/rebalances');themeInit();
$('#rb-refresh').addEventListener('click',load);
for(const id of ['#rb-method','#rb-basis'])$(id).addEventListener('change',()=>{currentMethod=$('#rb-method').value;currentBasis=$('#rb-basis').value;load();});
for(const id of ['#rb-from','#rb-to','#rb-versions'])$(id).addEventListener('change',render);
$('#rb-ticker').addEventListener('input',render);
$('#rb-list').addEventListener('click',e=>{const b=e.target.closest('[data-run]');if(b){selected=b.dataset.run;render();if(matchMedia('(max-width:1000px)').matches)$('#rb-detail-title').focus();}});
document.addEventListener('languagechange',()=>{if(data.items)render();});
load();
})();
