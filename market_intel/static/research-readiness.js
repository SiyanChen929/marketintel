(function(){
'use strict';
const tr=(s,v={})=>typeof globalThis.t==='function'?globalThis.t(s,v):s.replace(/\{(\w+)\}/g,(_,k)=>v[k]??'{'+k+'}');
const T=(s,v)=>esc(tr(s,v));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names={OPEN:'目标时窗开放',PENDING_OPEN:'等待开盘',EXPIRED:'目标已过期',BLOCKED:'已阻断',MISSING:'无目标',INVALID_POLICY:'时间无法核验',fresh:'正在轮询',stale:'心跳过期',missing:'无心跳',invalid:'记录无效',recorded:'已记录',gap:'有缺口',unknown:'未知',complete:'完整',inconsistent:'记录不一致',unavailable:'暂无证据',blocked:'尚未接通',UNAVAILABLE:'尚未开始',INVALID_ARTIFACT:'账本无效',REPLAY_VERIFIED:'本地重放一致'};
const groups={numerical:'数值基线 A',fixed_text_rule:'A + 固定文本规则',llm:'A + LLM 判断'};
const value=v=>v==null?tr('未知'):String(v);
function parts(data){
 const op=data.operation||{},clock=op.current_target_status||{},st=data.strategic||{},f=data.forward||{},runtime=st.runtime||{};
 const cards=(data.checks||[]).map(c=>`<article class="readiness-check"><b>${T(c.title)}</b> · <span>${T(names[c.status]||c.status)}</span><p data-no-i18n>${esc(c.detail)}</p><a href="${esc(c.href)}">查看 →</a></article>`).join('');
 const coverage=Object.entries(groups).map(([k,label])=>{const counts=f.groups?.[k]||{};const total=Object.values(counts).reduce((a,v)=>a+(Number.isFinite(v)?v:0),0);return `<tr><td>${T(label)}</td><td>${counts.ready||0}</td><td>${total-(counts.ready||0)}</td><td>${total}</td></tr>`;}).join('');
 const next=op.next_preparation||tr('未知');
 const candidate=data.candidates||{},candidateNames={not_started:'本次尚无登记',error:'本次登记失败',integrity_error:'记录无法核验',stale_run:'属于其他运行',diagnostic_replay:'仅诊断回放',evidence_incomplete:'证据不足，暂不能比较',collecting:'正在积累前向观察'};
 const candidateView=(candidate.items||[]).length?(candidate.items||[]).map(c=>`<p><b>${esc(c.protocol_id||'E2')}</b> · ${T(candidateNames[c.status]||c.status)}${T('；可比较评分 {paired} / {rows}。登记于 {time}。',{paired:value(c.n_paired_scores),rows:value(c.n_rows),time:c.registered_at||tr('未知')})}</p>${c.n_paired_scores>0?`<p>${T('同一可比范围内，加入：{added}；移出：{removed}。全候选池覆盖：{coverage}。',{added:(c.selection?.added_to_paired_selection||[]).join('、')||tr('无'),removed:(c.selection?.removed_from_paired_selection||[]).join('、')||tr('无'),coverage:tr(c.selection?.full_universe_comparison_available?'完整':'不完整')})}</p>`:''}`).join(''):`<p>${T(candidateNames[candidate.status]||'本次尚无登记')}${T('。缺失的证据不会补写为历史评分。')}</p>`;
 return {cards,
  candidates:candidateView,
  timing:T('下次准备时间（美东）：{time}。本次读取固定到 {run}。',{time:next,run:data.run_id||tr('无运行记录')}),
  detail:`<p>${T('检查范围起点：{value}',{value:op.coverage_start||tr('未知')})}</p><p>${T('未记录：{value}',{value:(op.missed_sessions||[]).join('、')||tr('未发现')})}</p><p>${T('失败：{value}',{value:(op.failed_sessions||[]).join('、')||tr('未发现')})}</p><p>${T('不完整：{value}',{value:(op.incomplete_sessions||[]).join('、')||tr('未发现')})}</p>${(op.warnings||[]).map(s=>'<p data-no-i18n>'+esc(s)+'</p>').join('')}<p>这些状态不会补写历史目标或创建成交。</p>`,
  forward:`<p>${T('已登记 {versions} 个版本，覆盖 {dates} 个实际登记日。首次登记：{first}。',{versions:value(f.n_registered_versions),dates:value(f.n_registration_dates),first:f.first_registered_at||tr('尚未开始')})}</p><p>${T('战略模型配置：{availability}；该归档记录的复评数量：{count}。监控新闻所用的模型配置单独管理。',{availability:tr(runtime.client_available===true?'可尝试调用（未验证请求）':runtime.client_available===false?'当前不可调用':'未知'),count:value(st.assessed_count)})}</p>${f.integrity_errors?.length?'<p role="alert">前向登记存在完整性错误，无法作为验证证据。</p>':''}${f.last_evaluation?.status==='error'?'<p role="alert">最近一次前向登记或评价失败，请检查运行日志。</p>':''}`,
  groupTable:`<table><caption>各组评分记录的来源完整性（证券 × 版本）</caption><thead><tr><th>研究组</th><th>来源齐备</th><th>缺少证据 / 不可用</th><th>总数</th></tr></thead><tbody>${coverage}</tbody></table>`,
  clock:tr(names[clock.status]||'未知'), heartbeat:tr(names[op.heartbeat_status]||'未知'), constructions:`${value(data.constructions?.count)} / ${value(data.constructions?.expected)}`,
  gapCount:(op.missed_sessions||[]).length+(op.failed_sessions||[]).length+(op.incomplete_sessions||[]).length};
}
if(typeof module!=='undefined')module.exports={parts};
if(typeof document==='undefined')return;
let cached=null;
function render(data){const view=parts(data);
 $('#readiness-at').textContent=tr('检查于 {time}',{time:data.checked_at});
 $('#readiness-kpis').innerHTML=Kpi('当前目标',esc(view.clock),'仅时窗与归档输入检查')+Kpi('后台任务',esc(view.heartbeat),'只读轮询心跳')+Kpi('方法归档',esc(view.constructions),'独立目标与现金')+Kpi('日频缺口',esc(view.gapCount),'未记录 / 失败 / 不完整');
 $('#readiness-timing').innerHTML=view.timing;$('#readiness-checks').innerHTML=view.cards;$('#readiness-detail').innerHTML=view.detail;
 $('#forward-summary').innerHTML=view.forward;$('#forward-groups').innerHTML=view.groupTable;$('#candidate-summary').innerHTML=view.candidates;
}
async function load(){
 setPanel('#readiness-panel','loading');setPanel('#forward-panel','loading');
 try{const data=await fetchJSON('/api/research/readiness','research-readiness');cached=data;render(data);
  setPanel('#readiness-panel','fresh');setPanel('#forward-panel','fresh');
 }catch(e){if(e.stale)return;$('#readiness-kpis').innerHTML='';$('#readiness-checks').innerHTML='';$('#readiness-timing').textContent='';$('#readiness-detail').textContent='';$('#forward-summary').textContent='证据无法读取；请刷新重试。';$('#forward-groups').innerHTML='';$('#candidate-summary').textContent='评分对照无法读取。';
  setPanel('#readiness-panel','error','运行状态读取失败',load);setPanel('#forward-panel','error','前向证据读取失败',load);}
}
document.addEventListener('languagechange',()=>{if(cached)render(cached);});
document.addEventListener('research:refresh',load);visiblePoll(load,60000);load();
})();
