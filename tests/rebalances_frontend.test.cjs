const {test}=require('node:test');
const assert=require('node:assert/strict');
const {filterItems,detailHTML,pct,delta}=require('../market_intel/static/rebalances.js');
const item={run_id:'r1',date:'2026-09-14',available:true,canonical:true,policy:{frequency:'daily'},targets:[{ticker:'A',before:.4,after:.3,delta:-.1,action:'decrease'}],cash:.7,previous_cash:.6,cash_delta:.1,entries:[],exits:[],fills:[],execution_status:'no_fills',reasons:[],comparison_basis:'initial'};
test('filter by date ticker and same-day versions',()=>{
 const old={...item,canonical:false,run_id:'old'};
 assert.equal(filterItems([item,old],{ticker:'a'}).length,1);
 assert.equal(filterItems([item,old],{versions:true}).length,2);
 assert.equal(filterItems([item],{from:'2026-09-15'}).length,0);
});
test('missing values stay unknown and deltas are percentage points',()=>{
 assert.equal(pct(null),'—'); assert.equal(pct(0),'0.00%');
 assert.equal(delta(.1),'+10.00 个百分点');
 assert.match(detailHTML(item),/未记录模拟成交/);
 assert.match(detailHTML(item),/现金/);
});
test('archive and ticker content are escaped',()=>{
 const html=detailHTML({...item,archive_note:'<img src=x onerror=alert(1)>',targets:[{...item.targets[0],ticker:'<script>x</script>'}]});
 assert(!html.includes('<script>')&&!html.includes('<img src=x'));
 assert(html.includes('&lt;img'));
});
test('partial paper fills never claim completed rebalancing',()=>{
 const html=detailHTML({...item,execution_status:'paper_fills',fills:[{session_id:'s',symbol:'A',side:'BUY',quantity:'1',price:'10'}]});
 assert.match(html,/部分成交不代表目标组合已全部完成/);
 assert.match(html,/独立账本检查未确认/);
});
test('method and weight basis are explicit and switching is visible',()=>{
 const html=detailHTML({...item,construction:'hrp',construction_label:'Hierarchical risk parity',weight_basis:'model',execution_status:'tracking_only',construction_changed:true,previous_construction:'risk_parity'});
 assert.match(html,/Hierarchical risk parity/);assert.match(html,/股票内部权重/);
 assert.match(html,/独立目标记录/);assert.match(html,/各方法的独立历史仍保留/);
});
