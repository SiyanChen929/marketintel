from copy import deepcopy
import json

import pandas as pd
import pytest

from ai_semi_intel.portfolio import target_archive
from market_intel.rebalance_history import payload


def make_run(history, rid, day, active, methods, prior=None):
    directory = history / 'runs' / rid
    directory.mkdir(parents=True)
    opts = {k:{'weights':pd.Series(w), 'vol':v} for k,(w,v) in methods.items()}
    item=opts[active]; scale=min(1,.25/item['vol'])
    snap={'run_id':rid,'date':day,'asof_session':day,'generated_at':day+'T07:20:00-04:00',
          'construction':active,'vol_target':.25,'weights':(item['weights']*scale).to_dict(),'cash':1-scale,
          'weights_model':item['weights'].to_dict()}
    run={**snap,'approved_prior_run_id':prior,'actionable':True}
    doc=target_archive.build(opts,snap,tuple(methods))
    target_archive.bind_previous(doc,history,prior)
    for name,obj in [('snapshot.json',snap),('run.json',run),('construction_targets.json',doc)]:
        (directory/name).write_text(json.dumps(obj))
    indexpath=history/'runs'/'index.json'
    index=json.loads(indexpath.read_text()) if indexpath.exists() else []
    for old in index:
        old['status']='superseded' if old['date']==day else old['status']
    index.append({**snap,'status':'approved'})
    indexpath.write_text(json.dumps(index))
    return doc


BASE={'risk_parity':({'A':.4,'B':.6},.5),'hrp':({'A':.7,'B':.3},.2)}
EMPTY={'sessions':[],'corrupt_ledgers':[]}


def test_each_method_gets_its_own_volatility_scale_and_cash(tmp_path):
    doc=make_run(tmp_path,'one','2026-09-11','risk_parity',BASE)
    erc,hrp=doc['constructions'].values()
    assert erc['cash']==.5 and hrp['cash']==0
    assert erc['target_weights']['A']==pytest.approx(.2)
    assert hrp['target_weights']['A']==pytest.approx(.7)
    assert erc['comparisons']['target']['target_turnover'] is None
    assert erc['execution_status']=='TARGET_ONLY'


def test_switching_primary_preserves_independent_method_differences(tmp_path):
    make_run(tmp_path,'one','2026-09-11','risk_parity',BASE)
    before={str(p):p.read_bytes() for p in (tmp_path/'runs'/'one').glob('*')}
    changed={'risk_parity':({'A':.5,'B':.5},.5),'hrp':({'A':.6,'B':.4},.2)}
    doc=make_run(tmp_path,'two','2026-09-14','hrp',changed,'one')
    for method,delta in [('risk_parity',.05),('hrp',-.1)]:
        out=payload(tmp_path,EMPTY,construction=method)
        latest=out['items'][0]
        assert latest['prior_run_id']=='one' and latest['previous_construction']==method
        assert next(r for r in latest['targets'] if r['ticker']=='A')['delta']==pytest.approx(delta)
        assert doc['constructions'][method]['comparisons']['target']['actions'][0]['delta']==pytest.approx(delta)
        assert out['canonical_count']==2
    active=payload(tmp_path,EMPTY)['items'][0]
    assert active['construction_changed'] and active['previous_construction']=='risk_parity'
    assert next(r for r in active['targets'] if r['ticker']=='A')['delta']==pytest.approx(.4)
    assert before=={str(p):p.read_bytes() for p in (tmp_path/'runs'/'one').glob('*')}


def test_same_day_rerun_and_later_approval_cannot_rebind_frozen_baseline(tmp_path):
    make_run(tmp_path,'one','2026-09-11','risk_parity',BASE)
    make_run(tmp_path,'two','2026-09-14','risk_parity',BASE,'one')
    make_run(tmp_path,'three','2026-09-14','risk_parity',BASE,'one')
    out=payload(tmp_path,EMPTY,construction='hrp')
    assert out['canonical_count']==2 and out['version_count']==3
    assert out['items'][0]['prior_run_id']=='one'
    changed={'risk_parity':({'A':.2,'B':.8},.5),'hrp':({'A':.1,'B':.9},.2)}
    make_run(tmp_path,'late_old_day','2026-09-11','risk_parity',changed)
    out=payload(tmp_path,EMPTY,construction='hrp')
    latest=next(r for r in out['items'] if r['run_id']=='three')
    assert latest['prior_run_id']=='one' and latest['target_turnover']==0


def test_old_archive_reads_original_model_weights_without_inventing_account_targets(tmp_path):
    make_run(tmp_path,'one','2026-09-11','risk_parity',BASE)
    folder=tmp_path/'runs'/'one'
    (folder/'construction_targets.json').unlink()
    pd.DataFrame({k:w for k,(w,v) in BASE.items()}).to_csv(folder/'portfolio_weights.csv')
    before={p.name:p.read_bytes() for p in folder.glob('*')}
    target=payload(tmp_path,EMPTY,construction='hrp')['items'][0]
    model=payload(tmp_path,EMPTY,construction='hrp',basis='model')['items'][0]
    assert not target['available']
    assert model['cash']==0 and model['targets'][0]['after']==.7
    assert model['execution_status']=='tracking_only'
    assert before=={p.name:p.read_bytes() for p in folder.glob('*')}


def test_alternative_does_not_inherit_active_method_fills(tmp_path):
    make_run(tmp_path,'one','2026-09-11','risk_parity',BASE)
    paper={'sessions':[{'session_id':'s','orders':[{'order_id':'o','run_id':'one','strategy_id':'stock_select'}],
                       'fills':[{'fill_id':'f','order_id':'o','symbol':'A'}]}],'corrupt_ledgers':[]}
    assert payload(tmp_path,paper,construction='risk_parity')['items'][0]['execution_status']=='paper_fills'
    assert payload(tmp_path,paper,construction='hrp')['items'][0]['fills']==[]
    assert payload(tmp_path,paper,construction='risk_parity',basis='model')['items'][0]['fills']==[]


def test_archive_identity_mismatch_fails_closed(tmp_path):
    make_run(tmp_path,'one','2026-09-11','risk_parity',BASE)
    p=tmp_path/'runs'/'one'/'construction_targets.json'; d=json.loads(p.read_text());d['run_id']='wrong';p.write_text(json.dumps(d))
    out=payload(tmp_path,EMPTY,construction='hrp')
    assert not out['items'][0]['available'] and out['warnings']


def test_immutable_run_cannot_be_overwritten(tmp_path):
    from ai_semi_intel import runs
    from ai_semi_intel.utils.io import DataUnavailable
    folder=tmp_path/'runs'/'one';folder.mkdir(parents=True);p=folder/'snapshot.json';p.write_text('original')
    with pytest.raises(DataUnavailable,match='immutable run archive already exists'):
        runs.register({'run_id':'one'},tmp_path,tmp_path)
    assert p.read_text()=='original'


def test_unknown_method_and_basis_are_rejected(tmp_path):
    with pytest.raises(ValueError):payload(tmp_path,EMPTY,construction='../elsewhere')
    with pytest.raises(ValueError):payload(tmp_path,EMPTY,basis='guessed')
