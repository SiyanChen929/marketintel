const test=require('node:test');
const assert=require('node:assert/strict');
const {parts}=require('../market_intel/static/research-readiness.js');
test('unknown refresh evidence stays unknown and cannot claim no refreshes',()=>{
 const view=parts({strategic:{assessed_count:null,runtime:{client_available:false}},forward:{n_registered_versions:0,n_registration_dates:0}});
 assert.match(view.forward,/复评数量：未知/);assert.match(view.forward,/当前不可调用/);
});
test('the view separates provenance coverage from economic validation and escapes evidence text',()=>{
 const view=parts({run_id:'<script>bad</script>',operation:{missed_sessions:['2026-09-15'],current_target_status:{status:'EXPIRED'}},
  checks:[{title:'<img src=x onerror=bad>',detail:'a&b',status:'gap',href:'/rebalances'}],
  forward:{groups:{llm:{ready:1,provenance_incomplete:3}},integrity_errors:[{}]}});
 assert.match(view.cards,/&lt;img/);assert.doesNotMatch(view.cards,/<img/);
 assert.match(view.timing,/&lt;script/);assert.equal(view.clock,'目标已过期');assert.equal(view.gapCount,1);
 assert.match(view.groupTable,/LLM 判断<\/td><td>1<\/td><td>3<\/td><td>4/);
 assert.match(view.forward,/无法作为验证证据/);
});
test('an E2 record without paired evidence cannot show a shortlist improvement',()=>{
 const view=parts({candidates:{items:[{status:'evidence_incomplete',protocol_id:'E2',n_rows:15,n_paired_scores:0,selection:{added_to_paired_selection:['invented']}}]}});
 assert.match(view.candidates,/暂不能比较/);assert.match(view.candidates,/0 \/ 15/);assert.doesNotMatch(view.candidates,/invented/);
});
test('candidate selection differences expose incomplete comparison coverage and escape names',()=>{
 const view=parts({candidates:{items:[{status:'collecting',protocol_id:'E2',n_rows:15,n_paired_scores:2,selection:{added_to_paired_selection:['<b>X</b>'],full_universe_comparison_available:false}}]}});
 assert.match(view.candidates,/覆盖：不完整/);assert.match(view.candidates,/&lt;b&gt;X/);assert.doesNotMatch(view.candidates,/<b>X/);
});
