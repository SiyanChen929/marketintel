from __future__ import annotations
from market_intel.platform.performance import PerformanceStore
from test_platform_api import client, FakePlatformService
from test_platform_performance import payload


def connected(tmp_path,remote=False):
    service=FakePlatformService()
    service.performance=PerformanceStore(tmp_path/'performance.sqlite')
    return client(tmp_path,service,remote=remote), service


def test_import_analyze_export_and_persistent_idempotency(tmp_path):
    from market_intel import identity
    c,s=connected(tmp_path)
    p=payload();r=c.post('/api/v2/platform/performance/import',json=p)
    assert r.status_code==200,r.text
    a=r.json()['artifact_id']
    assert c.post('/api/v2/platform/performance/import',json=p).json()['artifact_id']==a
    p['rows'][0]['return']=.2
    assert c.post('/api/v2/platform/performance/import',json=p).status_code==409
    assert c.get('/api/v2/platform/performance/artifacts/'+a).json()['rows'][0]['return']==.1
    before=s.performance.path.read_bytes()
    r=c.post('/api/v2/platform/performance/analyze',json={'sleeves':[{'artifact_id':a,'weight':1}]})
    assert r.status_code==200,r.text
    assert r.json()['status']=='RESEARCH_SCENARIO'
    assert s.performance.path.read_bytes()==before
    assert any(e['object_type']=='performance_artifact' for e in identity.audit_log())


def test_viewer_can_compare_but_cannot_import_and_origin_is_enforced(tmp_path):
    from market_intel import identity
    c,s=connected(tmp_path,True)
    a=s.performance.import_artifact(payload())['artifact_id']
    _,token=identity.create_account('perf-viewer','Viewer','viewer')
    headers={'Authorization':f'Bearer {token}'}
    assert c.get('/api/v2/platform/performance',headers=headers).status_code==200
    body={'sleeves':[{'artifact_id':a,'weight':1}]}
    assert c.post('/api/v2/platform/performance/analyze',json=body,headers=headers).status_code==200
    assert c.post('/api/v2/platform/performance/import',json=payload(),headers=headers).status_code==403
    assert c.post('/api/v2/platform/performance/analyze',json=body,headers={**headers,'Origin':'https://unrelated.invalid'}).status_code==403


def test_missing_idempotency_and_invalid_request_fail_closed(tmp_path):
    c,s=connected(tmp_path)
    del c.headers['Idempotency-Key']
    assert c.post('/api/v2/platform/performance/import',json=payload()).status_code==400
    assert c.post('/api/v2/platform/performance/analyze',json={'sleeves':[]}).status_code==422
    assert c.get('/api/v2/platform/performance/artifacts/no-such-artifact').status_code==404


def test_download_attachment_matches_displayed_analysis_and_rejects_stale_id(tmp_path):
    import json
    c,s=connected(tmp_path);a=s.performance.import_artifact(payload())['artifact_id']
    result=s.performance.analyze({'sleeves':[{'artifact_id':a,'weight':1}]})
    params={'payload':json.dumps(result['request']),'analysis_id':result['analysis_id']}
    response=c.get('/api/v2/platform/performance/export',params=params)
    assert response.status_code==200
    assert response.json()==result
    assert response.headers['content-disposition']=='attachment; filename="joint-performance-2020-01-2020-03.json"'
    params['analysis_id']='stale'
    assert c.get('/api/v2/platform/performance/export',params=params).status_code==409
