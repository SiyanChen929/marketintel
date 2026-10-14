"""Independent source-level reconciliation for the frozen four-sleeve preview."""
from pathlib import Path
import hashlib
import io
import json
import numpy as np
import pandas as pd
from market_intel import config
from market_intel.platform.performance import PerformanceStore


def main():
    root=config.PROJECT_ROOT/'outputs/platform_review_20260904'
    store=PerformanceStore(config.DATA_DIR/'platform/performance.sqlite')
    archive=config.DATA_DIR/'platform/performance_sources'
    selected=[]
    for recipe in ('vol','commodity','msci','ps'):
        receipt=json.loads((root/f'{recipe}_import.json').read_text())[0]
        selected.append(store.get(receipt['artifact_id']))
    request={'sleeves':[{'artifact_id':a['artifact_id'],'weight':.25} for a in selected], 'currency_mode':'constant_fx','rebalance':'none'}
    result=store.analyze(request)
    start=pd.Period(result['start'],'M').start_time
    end=pd.Period(result['end'],'M').end_time
    source_paths=[]
    checks=[]
    for a in selected:
        sid=a['strategy_id']
        suffix={'vol_trading':'representative_ledger.parquet','commodity_arbitrage':'stitched_daily.csv','msci_rebalance':'first_minute_alpha_portfolio.csv','pairs_trading':'v10_portfolio_daily.csv.gz'}[sid]
        src=next(s for s in a['sources'] if s['name'].endswith(suffix))
        raw=(archive/src['sha256']).read_bytes();assert hashlib.sha256(raw).hexdigest()==src['sha256']
        d=pd.read_parquet(io.BytesIO(raw)) if suffix.endswith('.parquet') else pd.read_csv(io.BytesIO(raw),compression='gzip' if suffix.endswith('.gz') else None)
        if sid=='vol_trading':d=d[(d.strategy_id=='VB2')&(d.seed_index==0)];date,nav='date','nav'
        elif sid=='pairs_trading':d=d[d.portfolio_id=='joint_max_sharpe_top10'];date,nav='date','equity'
        elif sid=='commodity_arbitrage':date,nav='date','equity_cny'
        else:d=d[d.cost_bps==10];date,nav='effective_close_date','equity'
        d=d.copy();d['date']=pd.to_datetime(d[date]);d=d.sort_values('date')
        opening=float(d.loc[d.date<start,nav].iloc[-1])
        values=[float(d.loc[d.date<=pd.Period(row['month'],'M').end_time,nav].iloc[-1])/opening for row in result['rows']]
        source_paths.append(values)
        index=next(i for i,s in enumerate(result['selected']) if s['strategy_id']==sid)
        actual=[row['sleeve_nav'][index] for row in result['rows']]
        error=float(np.max(np.abs(np.array(values)-actual)))
        assert error<1e-10,(sid,error)
        checks.append({'strategy_id':sid,'source':src,'opening_nav':opening,'final_nav_ratio':values[-1],'max_monthly_nav_error':error})
    independent=np.mean(np.array(source_paths),axis=0)
    error=float(np.max(np.abs(independent-[r['nav'] for r in result['rows']])))
    assert error<1e-10
    evidence={'verdict':'NOT VALIDATED','claim':'Return-level import and capital aggregation reconciled; source trades, PIT membership, fills, borrow, futures rolls and FX not independently validated.',
              'start':result['start'],'end':result['end'],'checks':checks,'joint_nav_max_error':error,'calculator':result['calculator'],
              'source_archive_verified':True,'monthly_event_idle_cash_convention':'MSCI NAV stays constant between source recorded events; not a claim of event-calendar completeness'}
    (root/'manual_reconciliation.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False))
    (root/'four_sleeves_fixed_fx.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
    for name,group,mode,rebalance in [('usd_three_sleeves',[a for a in selected if a['currency']=='USD'],'native','none'),('four_sleeves_monthly',selected,'constant_fx','monthly')]:
        body={'sleeves':[{'artifact_id':a['artifact_id'],'weight':1/len(group)} for a in group],'currency_mode':mode,'rebalance':rebalance,'allocation_cost_bps':5 if rebalance=='monthly' else 0}
        (root/(name+'.json')).write_text(json.dumps(store.analyze(body),indent=2,ensure_ascii=False))
    # Idempotent browser import uses a real, already frozen artifact, not dummy P&L.
    (root/'vol_strategy_import.json').write_text(json.dumps({k:v for k,v in selected[0].items() if k not in ('artifact_id','imported_at')},indent=2,ensure_ascii=False))
    print(json.dumps({'source_nav_reconciliation':checks,'joint_nav_max_error':error,'metrics':result['metrics']},indent=2))

if __name__=='__main__':main()
