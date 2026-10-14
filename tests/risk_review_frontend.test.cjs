const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const helper = fs.readFileSync(path.join(root, 'market_intel/static/risk-review.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'market_intel/static/risk.html'), 'utf8');
const base = fs.readFileSync(path.join(root, 'market_intel/static/base.js'), 'utf8');
const rules = vm.runInNewContext(helper + '\nRiskReview;');
const view = {layer:'model', win:'1y', bench:'te_proxy'};
const provenance = {data_snapshot_hash:'data', calculator_code_hash:'code', acquired_at:'2026-09-03T13:00:00-04:00', asof_last_confirmed_close:'2026-09-02'};
function pair() {
  return [{run:{run_id:'A'},var:{provenance:{...provenance}}},
    {run:{run_id:'A'},constructions:{x:{layers:{model:{provenance:{...provenance}}}}}}];
}
test('separate requests cannot mix run IDs, price snapshots, calculators, or finality', () => {
  const [book, matrix] = pair();
  assert.equal(rules.mismatch(book, matrix), '');
  matrix.run.run_id='B'; assert.match(rules.mismatch(book,matrix), /Run IDs/);
  matrix.run.run_id='A';
  for (const key of Object.keys(provenance)) {
    matrix.constructions.x.layers.model.provenance[key]='different';
    assert.match(rules.mismatch(book,matrix), new RegExp(key));
    matrix.constructions.x.layers.model.provenance[key]=provenance[key];
  }
  delete book.run.run_id; assert.match(rules.mismatch(book,matrix), /missing/);
});
test('unknown constraints are never counted as passes', () => {
  const result=rules.checks({ok:true,n_failed:0,checks:[{ok:true},{ok:false},{ok:null},{ok:'true'},{}]});
  assert.equal(result.passed,1);assert.equal(result.failed,1);assert.equal(result.unknown,3);
});
test('risk ordering puts largest finite risk first and missing values last without mutation', () => {
  const rows=[{construction:'none',layer_available:true,'1y_available':true,'1y_var99_10d_overlap':null},
    {construction:'low',layer_available:true,'1y_available':true,'1y_var99_10d_overlap':0.1},
    {construction:'high',layer_available:true,'1y_available':true,'1y_var99_10d_overlap':0.2}];
  assert.equal(rules.order(rows,view,'var').map(r=>r.construction).join(','),'high,low,none');
  assert.equal(rows[0].construction,'none');
});
function renderHarness() {
  const elements=new Map();
  const $=key=>{if(!elements.has(key))elements.set(key,{innerHTML:'',textContent:''});return elements.get(key);};
  const win={available:true,start:'START',end:'END',n_sessions_used:252,
    horizons:{'1':{overlapping:{var_99:0.09,var_95:0.07,n_obs:249}},'10':{overlapping:{var_99:0.19,n_obs:240,tail_obs_99:3},non_overlapping_phases:{n_phases:10,n_windows_per_phase_range:[23,25]}}},
    tracking_error:[{benchmark:'mandate',kind:'formal mandate',available:false,reason:'No mandate'},
      {benchmark:'current-universe proxy',kind:'research proxy',available:true,tracking_error_ann:0.11},
      {benchmark:'SOXX index proxy',kind:'research proxy',available:true,tracking_error_ann:0.12}]};
  const layer={available:true,cash:{weight:0},windows:{'1y':win,'3y':{available:false,reason:'SNDK history missing'}},provenance};
  const context=vm.createContext({$,RiskReview:rules,PCNAME:'chosen',PCSEL:{...view},varWin:null,
    t:(source,vars={})=>String(source).replace(/\{(\w+)\}/g,(m,k)=>Object.prototype.hasOwnProperty.call(vars,k)?String(vars[k]??''):m),
    PC:{constructions:{chosen:{layers:{model:layer,actual_run:{available:false,reason:'No published book'}}}}},
    R:{var:{available:true,windows:{'1y':{...win,horizons:{'1':{overlapping:{var_99:0.5}}}}}}},
    RISK_LAYERS:[['model','Model'],['actual_run','Published target']],RISK_BENCH:[['te_proxy','Research proxy']],
    fmt:{pct:(v,d=2)=>v==null?'':(v*100).toFixed(d)+'%'},esc:v=>String(v??''),sgn:v=>String(v),
    kpi:row=>row.join('|'),setPanel:()=>{},rmxCIState:()=>null});
  const script=html.slice(html.indexOf('function renderVar()'),html.indexOf('// ---------- (f) stress'));
  vm.runInContext(script,context);
  return {context,$,render:()=>vm.runInContext('renderVar()',context)};
}
test('VaR uses the selected construction, and TE uses the selected benchmark', () => {
  const h=renderHarness();h.render();
  assert.match(h.$('#var-kpis').innerHTML,/9.00%/);assert.doesNotMatch(h.$('#var-kpis').innerHTML,/50.00%/);
  assert.match(h.$('#var-tbl tbody').innerHTML,/current-universe proxy/);assert.doesNotMatch(h.$('#var-tbl tbody').innerHTML,/SOXX/);
  h.context.PCSEL.bench='te_mandate';h.render();
  assert.match(h.$('#var-tbl tbody').innerHTML,/No mandate/);assert.doesNotMatch(h.$('#var-tbl tbody').innerHTML,/current-universe/);
});
test('switching to unavailable 3Y removes old numbers, notes and intervals with no 1Y fallback', () => {
  const h=renderHarness();h.render();h.context.PCSEL.win='3y';h.render();
  assert.match(h.$('#var-kpis').innerHTML,/SNDK history missing/);
  assert.doesNotMatch(h.$('#var-kpis').innerHTML,/9.00%/);
  assert.equal(h.$('#var-h10 tbody').innerHTML,'');assert.equal(h.$('#var-h10-note').textContent,'');
  assert.equal(h.$('#var-tbl tbody').innerHTML,'');assert.equal(h.context.varWin,'3y');
});
test('a construction with no published book cannot inherit the model or canonical book risk', () => {
  const h=renderHarness();h.render();h.context.PCSEL.layer='actual_run';h.render();
  assert.equal(h.$('#var-kpis').innerHTML,'');assert.equal(h.$('#var-h10 tbody').innerHTML,'');
});
test('phase observations are per partition and an interval shares the matrix state', () => {
  const h=renderHarness();h.context.rmxCIState=()=>({state:'done',row:{'1y_var99_ci_lo':0.08,'1y_var99_ci_hi':0.25,'1y_ci_confidence':0.95,'1y_ci_reps':2000,'1y_ci_block':20}});h.render();
  assert.match(h.$('#var-h10 tbody').innerHTML,/23–25 \/ phase/);
  assert.match(h.$('#var-kpis').innerHTML,/8.00% – 25.00%/);
  h.context.rmxCIState=()=>({state:'stale'});h.render();
  assert.doesNotMatch(h.$('#var-kpis').innerHTML,/8.00% – 25.00%/);assert.match(h.$('#var-kpis').innerHTML,/snapshot changed/);
});
test('legacy service and data gaps remain blocking even when all target checks pass', () => {
  const f=rules.findings({run:{actionable:false,data_health:{price_gaps:{A:['2026-08-28']}}},var:{schema_version:'risk.v3'},compliance:{ok:true,checks:[{ok:true}]},liquidity:{}},{});
  assert.ok(f.some(x=>x.title==='Legacy risk service'&&x.level==='block'));
  assert.ok(f.some(x=>x.title==='Repair price gaps'&&x.level==='block'));
});
test('refresh publishes only the newest complete request pair and hides stale figures on failure', async () => {
  const elements=new Map();const $=key=>{if(!elements.has(key))elements.set(key,{});return elements.get(key);};
  const pending=[];const [r,p]=pair();r.weights={n_names:1};
  const ctx=vm.createContext({document:{body:{dataset:{}}},$,R:null,PC:null,lastOk:null,reviewSequence:0,PANELS:[],diagReady:new Set(),
    _RMXreq:new Map(),_RMXci:new Map(),_RMX:{},RiskReview:rules,
    t:(source,vars={})=>String(source).replace(/\{(\w+)\}/g,(m,k)=>Object.prototype.hasOwnProperty.call(vars,k)?String(vars[k]??''):m),
    fetchJSON:()=>new Promise((resolve,reject)=>pending.push({resolve,reject})),setPanel:()=>{},announce:()=>{},hdr:()=>{},scope:()=>{},renderReview:()=>{},loadMatrix:async()=>{},render:()=>{},renderOpenDiagnostics:()=>{},statusBanner:()=>''});
  vm.runInContext(html.slice(html.indexOf('async function load(){'),html.indexOf('function scope()')),ctx);
  const first=vm.runInContext('load()',ctx),second=vm.runInContext('load()',ctx);
  pending[2].resolve(r);pending[3].resolve(p);await second;assert.equal(ctx.document.body.dataset.reviewState,'ready');
  pending[0].resolve({run:{run_id:'OLD'}});pending[1].resolve({run:{run_id:'OLD'}});await first;assert.equal(ctx.R.run.run_id,'A');
  const third=vm.runInContext('load()',ctx);pending[4].resolve(r);pending[5].reject(new Error('network failure'));await third;
  assert.equal(ctx.document.body.dataset.reviewState,'error');assert.equal(ctx.R,null);assert.equal(ctx.PC,null);
  assert.equal($('#review-export').disabled,true);
});

// Navigation integration must retain model-vs-account identity across views.
const navRules=vm.runInNewContext(base.slice(base.indexOf('const PAGES='),base.indexOf('function themeInit()'))+'\n({PAGES,SUBNAV,subnavFor,portfolioViewLinks});',{URLSearchParams});
test('risk lives under Portfolio and no longer occupies a top-level tab',()=>{
 assert.equal(navRules.PAGES.some(([p])=>p==='/risk'),false);
 assert.equal(navRules.subnavFor('/risk').parent,'/portfolio');
 assert.ok(navRules.SUBNAV['/portfolio'].some(([p])=>p==='/rebalances'));
});
test('canonical account and independent model links retain their matching risk and history basis',()=>{
 const parse=url=>new URL(url,'http://localhost').searchParams;
 const active=navRules.portfolioViewLinks({method:'recommended',primary:'risk_parity'});
 assert.equal(parse(active['/risk']).get('layer'),'actual_run');
 assert.equal(parse(active['/rebalances']).get('construction'),'active');
 assert.equal(parse(active['/rebalances']).get('basis'),'target');
 const hrp=navRules.portfolioViewLinks({method:'hrp'});
 assert.equal(parse(hrp['/portfolio']).get('method'),'hrp');
 assert.equal(parse(hrp['/risk']).get('method'),'hrp');
 assert.equal(parse(hrp['/risk']).get('layer'),'model');
 assert.equal(parse(hrp['/rebalances']).get('basis'),'model');
});
test('a hypothetical scenario cannot link to an archived account target, and aliases resolve to the primary model',()=>{
 const parse=url=>new URL(url,'http://localhost').searchParams;
 for(const layer of ['model','vol_target_scenario']){
  const links=navRules.portfolioViewLinks({method:'recommended',primary:'hrp',layer,win:'3y',bench:'te_index'});
  assert.equal(parse(links['/portfolio']).get('method'),'hrp');
  assert.equal(parse(links['/rebalances']).get('construction'),'hrp');
  assert.equal(parse(links['/rebalances']).get('basis'),'model');
  assert.equal(parse(links['/risk']).get('layer'),layer);
  assert.equal(parse(links['/risk']).get('win'),'3y');
 }
});
