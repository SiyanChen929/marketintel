(function(root){
'use strict';
const tr=(s,v={})=>typeof globalThis.t==='function'?globalThis.t(s,v):s.replace(/\{(\w+)\}/g,(_,k)=>v[k]??'{'+k+'}');
const labels={TARGET_READY:'日频更新完成',TARGET_BLOCKED:'目标已生成，执行受阻',FINISHED:'手动更新完成',FAILED:'更新失败',INTERRUPTED:'运行中断',INVALID:'运行记录异常',RUNNING:'正在更新',IDLE:'暂无运行记录'};
function time(value,zone='America/Los_Angeles'){
 if(!value)return tr('未知');const date=new Date(value);if(!Number.isFinite(date.getTime()))return tr('未知');
 return new Intl.DateTimeFormat(globalThis.MI_I18N?.language||'zh-CN',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(date);
}
function parts(s){
 const source=tr({daily:'自动日频',web:'手动任务',external:'外部任务'}[s.task_source]||'未知来源');
 const sync=s.platform_sync||{},syncStatus={SYNCED:'已同步',SYNCING:'同步中',WAITING:'等待发布',BLOCKED:'同步受阻',FAILED:'同步失败',ERROR:'同步失败',STOPPED:'已停止',NOT_STARTED:'未启动',IDLE:'等待新版本'}[sync.status]||sync.status||'未知';
 const heartbeat={fresh:'正常轮询',stale:'轮询过期',invalid:'时钟异常',missing:'无心跳'}[s.heartbeat_status]||'未知';
 return {cards:[['最近任务',tr(labels[s.task_status]||s.task_status||'未知'),source+' · '+(s.finished_at?tr('完成 {time}',{time:time(s.finished_at)}):s.started_at?tr('开始 {time}',{time:time(s.started_at)}):tr('时间未记录'))+tr('（洛杉矶）')],
  ['组合日期',s.asof||'未知',tr('生成 {time}（洛杉矶）',{time:time(s.generated_at)})],
  ['自动调度',tr(heartbeat),tr('最近检查 {time}（洛杉矶）',{time:time(s.heartbeat_checked_at)})],
  ['平台历史',tr(syncStatus),sync.last_error||sync.last_synced_run_id||sync.synced_run_id||sync.run_id||'']],
  schedule:s.next_preparation_at?tr('下一次准备：{la} 洛杉矶 / {et} 美东。每 5 分钟检查；电脑须保持唤醒。',{la:time(s.next_preparation_at),et:time(s.next_preparation_at,'America/New_York')}):'下次自动准备时间未知。',
  logMeta:tr(s.log_label||'日志来源未知')+tr(' · 文件更新 {time}（洛杉矶）',{time:time(s.log_updated_at)})+(s.log_error?' · '+s.log_error:''),
  completion:tr(labels[s.task_status]||'任务状态已更新'),success:['TARGET_READY','FINISHED'].includes(s.task_status)};
}
if(typeof module!=='undefined')module.exports={parts,time};
root.PipelineStatusUi={parts,time};
})(typeof window!=='undefined'?window:globalThis);
