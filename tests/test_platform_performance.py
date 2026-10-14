from __future__ import annotations
import copy
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from market_intel.platform.performance import PerformanceStore, SCHEMA, metrics
from market_intel.platform.service import PlatformBlocked, PlatformInputError
from scripts.import_platform_research import from_nav, full_months


def payload(sid='strategy_a', returns=(.1, -.1, .05), currency='USD'):
    return dict(schema_version=SCHEMA, strategy_id=sid, name=sid, variant='v1', run_id='run1', currency=currency,
                asset_class='equity', calendar='complete calendar months', costs='source net of costs', clock='close t to open t+1',
                return_construction='flow-adjusted NAV ratios', frequency='monthly', return_basis='net_nav', sample_type='historical_research',
                limitations=['No independent execution verification'], sources=[{'name':'source.csv','sha256':'a'*64}],
                rows=[{'month':f'2020-{i+1:02}', 'return':r} for i,r in enumerate(returns)])


@pytest.fixture
def store(tmp_path):
    return PerformanceStore(tmp_path/'perf.sqlite')


def add(store, sid='strategy_a', returns=(.1,-.1,.05), currency='USD'):
    return store.import_artifact(payload(sid, returns, currency))['artifact_id']


def scenario(*pairs, **kw):
    return dict(sleeves=[{'artifact_id':a,'weight':w} for a,w in pairs], **kw)


def test_independent_capital_and_cash_reconcile_hand_calculation(store):
    a=add(store); b=add(store,'strategy_b',(-.1,.2,0))
    r=store.analyze(scenario((a,.5),(b,.25)))
    # a: .5 -> .55 -> .495 -> .51975; b: .25 -> .225 -> .27 -> .27; cash .25
    assert [x['nav'] for x in r['rows']] == pytest.approx([1.025,1.015,1.03975])
    assert [x['pnl_contribution'] for x in r['selected']] == pytest.approx([.01975,.02])
    assert r['metrics']['total_return']==pytest.approx(.03975)
    assert r['cash_weight']==.25
    assert r['accounting_error']<1e-14
    assert sum(x['risk_contribution'] for x in r['selected'])==pytest.approx(1)
    assert r['metrics']['max_drawdown_monthly']==pytest.approx(1.015/1.025-1)
    assert r['metrics']['cagr'] is None  # never annualize a 3-month profit as a realized CAGR


def test_monthly_allocation_fee_and_risk_contributions(store):
    a=add(store);b=add(store,'strategy_b',(-.1,.2,0))
    r=store.analyze(scenario((a,.5),(b,.5),rebalance='monthly',allocation_cost_bps=10))
    # First month .55/.45 -> target .5/.5, absolute transfer .1, fee .0001
    assert r['rows'][0]['allocation_cost']==pytest.approx(.0001)
    assert r['rows'][0]['nav']==pytest.approx(.9999)
    # Second month .449955/.59994; turnover .149985; fee .000149985
    assert r['rows'][1]['allocation_cost']==pytest.approx(.000149985)
    assert r['rows'][1]['nav']==pytest.approx(1.049745015)
    assert r['rows'][2]['allocation_cost']==0
    assert r['rows'][2]['nav']==pytest.approx(1.049745015*1.025)
    assert r['accounting_error']<1e-14
    assert sum(s['risk_contribution'] for s in r['selected'])+r['allocation_cost_risk_contribution']==pytest.approx(1)


def test_mixed_currencies_fail_closed_until_explicit_scenario(store):
    a=add(store);b=add(store,'strategy_b',currency='CNY')
    with pytest.raises(PlatformBlocked,match='mixed currencies'):
        store.analyze(scenario((a,.5),(b,.5)))
    r=store.analyze(scenario((a,.5),(b,.5),currency_mode='constant_fx'))
    assert 'FIXED FX' in r['assumptions'][0]
    assert r['validation_status']=='NOT VALIDATED'
    assert r['currency_label']=='Fixed FX · normalized capital'


@pytest.mark.parametrize('change',[
    lambda p:p['rows'].__setitem__(1,{'month':'2020-01','return':0}),
    lambda p:p['rows'].__setitem__(1,{'month':'2020-03','return':0}),
    lambda p:p['rows'].__setitem__(1,{'month':'2020-02','return':None}),
    lambda p:p['rows'].__setitem__(1,{'month':'2020-02','return':float('nan')}),
    lambda p:p['rows'].__setitem__(1,{'month':'2020-02','return':float('inf')}),
    lambda p:p['rows'].__setitem__(1,{'month':'2020-02','return':-1}),
    lambda p:p['rows'].__setitem__(1,{'month':'2020-02','return':True}),
    lambda p:p.update(return_basis='fixed_initial_capital'),
    lambda p:p.update(frequency='daily'),
    lambda p:p.update(sample_type='synthetic'),
    lambda p:p.update(sources=[]),
    lambda p:p.update(limitations=[]),
    lambda p:p.update(currency='usd'),
    lambda p:p.update(strategy_id='../../other'),
    lambda p:p['rows'].__setitem__(0,{'month':'2099-01','return':0}),
    lambda p:p['rows'].__setitem__(1,{'month':'2020-02','return':1e100}),
])
def test_invalid_imports_never_enter_catalog(store,change):
    p=payload();change(p)
    with pytest.raises(PlatformInputError): store.import_artifact(p)
    assert not any(s['research_connected'] for s in store.catalog()['strategies'])


def test_idempotency_survives_restart_and_hash_mutation_fails_closed(store):
    p=payload();p.update(_operation_id='op1',_request_sha256='request1')
    first=store.import_artifact(p)
    reopened=PerformanceStore(store.path)
    assert reopened.import_artifact(p)==first
    p['rows'][0]['return']=.2
    with pytest.raises(PlatformBlocked):reopened.import_artifact(p)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE performance_artifacts SET payload=replace(payload,'0.1','0.2')")
    with pytest.raises(PlatformBlocked,match='checksum'):reopened.get(first['artifact_id'])


def test_no_duplicate_variants_or_overallocation_or_mixed_live_history(store):
    a=add(store)
    for pairs in [((a,.6),(a,.5)),((a,.5),(a,.5))]:
        with pytest.raises(PlatformInputError):store.analyze(scenario(*pairs))
    with pytest.raises(PlatformInputError):store.analyze(scenario((a,1.01)))
    with pytest.raises(PlatformInputError):store.analyze(scenario((a,0)))
    p=payload('strategy_b');p['sample_type']='live';b=store.import_artifact(p)['artifact_id']
    with pytest.raises(PlatformBlocked,match='live observations'):store.analyze(scenario((a,.5),(b,.5)))


def test_source_validation_not_promoted_and_invalid_cannot_participate(store):
    p=payload();p['source_verdict']='VALIDATED';a=store.import_artifact(p)['artifact_id']
    assert store.get(a)['validation_status']=='NOT VALIDATED'
    p['source_verdict']='INVALID';b=store.import_artifact(p)['artifact_id']
    with pytest.raises(PlatformBlocked,match='INVALID'):store.analyze(scenario((b,1)))


def test_common_months_are_contiguous_and_requested_bounds_not_shortened(store):
    a=add(store,returns=(.1,.1,.1,.1,.1))
    p=payload('strategy_b',(.2,.2,.2));p['rows']=[{'month':f'2020-{i:02}','return':.2} for i in (2,3,4)]
    b=store.import_artifact(p)['artifact_id'];r=store.analyze(scenario((a,.5),(b,.5)))
    assert [x['month'] for x in r['rows']]==['2020-02','2020-03','2020-04']
    assert r['selected'][0]['metrics']['total_return']==pytest.approx(1.1**3-1)
    with pytest.raises(PlatformBlocked,match='precedes'):store.analyze(scenario((a,.5),(b,.5),start='2020-01'))
    with pytest.raises(PlatformBlocked,match='exceeds'):store.analyze(scenario((a,.5),(b,.5),end='2020-05'))


def test_constant_series_and_initial_loss_are_handled(store):
    a=add(store,returns=(0,0,0));r=store.analyze(scenario((a,1)))
    assert r['correlation']==[[None]]
    assert r['metrics']['sharpe_zero_rf'] is None
    assert metrics([-.1,0,0])['max_drawdown_monthly']==pytest.approx(-.1)


def test_fixed_initial_capital_returns_are_reconstructed_from_equity():
    d=pd.DataFrame({'date':['2020-01-01','2020-01-02','2020-01-03'],'equity':[110,120,108]})
    f=from_nav(d,'date','equity',100)
    assert f.platform_return.to_list()==pytest.approx([.1,10/110,-.1])
    assert np.prod(1+f.platform_return)==pytest.approx(1.08)
    # Compounding the fixed-capital .1,.1,-.12 instead would incorrectly report 1.0648.
    assert np.prod(1+f.platform_return)!=pytest.approx(1.1*1.1*.88)


def test_daily_adapter_omits_endpoint_months_and_rejects_missing_returns():
    d=pd.DataFrame({'date':pd.to_datetime(['2020-01-15','2020-02-03','2020-03-03','2020-04-03','2020-05-15']), 'platform_return':[0,.01,.02,.03,0]})
    assert [r['month'] for r in full_months(d)]==['2020-02','2020-03','2020-04']
    d.loc[1,'platform_return']=np.nan
    with pytest.raises(ValueError):full_months(d)


def test_analysis_id_is_reproducible_and_gets_do_not_mutate_database(store):
    a=add(store);before=store.path.read_bytes()
    one=store.analyze(scenario((a,1)));two=store.analyze(scenario((a,1)))
    store.catalog();store.get(a)
    assert one==two and store.path.read_bytes()==before


def test_extreme_rebalanced_growth_fails_closed_instead_of_overflow(store):
    a=add(store,returns=tuple([99,-.99]*6));b=add(store,'strategy_b',tuple([-.99,99]*6))
    with pytest.raises(PlatformBlocked,match='finite range'):
        store.analyze(scenario((a,.5),(b,.5),rebalance='monthly'))
