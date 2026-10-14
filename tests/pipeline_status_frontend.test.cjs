const test=require('node:test');
const assert=require('node:assert/strict');
const {parts}=require('../market_intel/static/pipeline-status.js');
test('scheduled completion retains its own timestamp after the web process restarts',()=>{
 const view=parts({task_source:'daily',task_status:'TARGET_READY',finished_at:'2026-09-22T11:18:37Z',asof:'2026-09-22',generated_at:'2026-09-22T07:18:36-04:00',next_preparation_at:'2026-09-23T07:15:00-04:00',heartbeat_status:'fresh',platform_sync:{status:'SYNCED',synced_run_id:'run-22'},log_label:'自动日频任务日志',log_updated_at:'2026-09-22T11:18:37Z'});
 assert.equal(view.cards[0][1],'日频更新完成');assert.match(view.cards[0][2],/04:18/);
 assert.match(view.schedule,/04:15.*洛杉矶.*07:15.*美东/);assert.equal(view.cards[3][2],'run-22');assert.equal(view.success,true);
});
test('blocked or interrupted tasks never display a successful finish',()=>{
 for(const task_status of ['TARGET_BLOCKED','FAILED','INTERRUPTED','INVALID'])assert.equal(parts({task_status}).success,false);
});
test('sync failure and stale scheduler remain visible',()=>{
 const view=parts({task_status:'TARGET_READY',heartbeat_status:'stale',platform_sync:{status:'FAILED'},log_error:'日志文件无法读取'});
 assert.equal(view.cards[2][1],'轮询过期');assert.equal(view.cards[3][1],'同步失败');assert.match(view.logMeta,/日志文件无法读取/);
});
test('research remains subscribed while idle and platform refreshes history on changed identity',()=>{
 const fs=require('node:fs');const html=fs.readFileSync('market_intel/static/research.html','utf8');
 assert.match(html,/visiblePoll\(pstat,15000\)/);assert.doesNotMatch(html,/pollP\.stop\(\)/);assert.match(html,/lastPipelineRun!==s.run_id/);
 const platform=fs.readFileSync('market_intel/static/platform.html','utf8');assert.match(platform,/previousRun!==OV.current_run.run_id/);
});
test('risk and portfolio reload only a completed new publication and coalesce overlapping polls',async()=>{
 const fs=require('node:fs'),vm=require('node:vm');const source=fs.readFileSync('market_intel/static/base.js','utf8');
 const fn=source.slice(source.indexOf('function watchPublishedRun('),source.indexOf('\nfunction qs()',source.indexOf('function watchPublishedRun(')));
 let tick,calls=0,current='old',next={run_id:'old',running:false};
 const ctx={visiblePoll:callback=>{tick=callback;return{};},fetchJSON:async()=>next};vm.createContext(ctx);vm.runInContext(fn,ctx);
 ctx.watchPublishedRun(()=>current,async()=>{calls++;current=next.run_id;});
 await tick();assert.equal(calls,0);next={run_id:'new',running:true};await tick();assert.equal(calls,0);
 next.running=false;await Promise.all([tick(),tick()]);assert.equal(calls,1);await tick();assert.equal(calls,1);
 for(const page of ['risk','portfolio'])assert.match(fs.readFileSync('market_intel/static/'+page+'.html','utf8'),/watchPublishedRun\(/);
});
