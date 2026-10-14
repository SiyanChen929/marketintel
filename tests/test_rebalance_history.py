from copy import deepcopy
import json

import pytest

from market_intel.rebalance_history import payload


def archive(tmp_path, rows):
    root = tmp_path / 'runs'
    root.mkdir(parents=True, exist_ok=True)
    index = []
    for rid, day, weights, cash, prior, status in rows:
        folder = root / rid
        folder.mkdir(exist_ok=True)
        common = {'run_id': rid, 'date': day, 'asof_session': day, 'generated_at': day+'T07:20:00-04:00'}
        (folder / 'snapshot.json').write_text(json.dumps({**common, 'weights': weights, 'cash': cash}))
        (folder / 'run.json').write_text(json.dumps({**common, 'approved_prior_run_id': prior, 'actionable': True}))
        index.append({**common, 'status': status})
    (root / 'index.json').write_text(json.dumps(index))
    return tmp_path


ROWS = [('one', '2026-09-11', {'A': .4, 'B': .3}, .3, None, 'approved'),
        ('two', '2026-09-14', {'A': .3, 'C': .5}, .2, 'one', 'pending')]
EMPTY = {'sessions': [], 'corrupt_ledgers': []}


def test_changes_include_exits_and_cash_without_claiming_execution(tmp_path):
    root = archive(tmp_path, ROWS)
    before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    out = payload(root, EMPTY)
    latest, first = out['items']
    assert latest['entries'] == ['C'] and latest['exits'] == ['B']
    assert latest['target_turnover'] == pytest.approx(.5)
    assert latest['cash_delta'] == pytest.approx(-.1)
    assert {r['ticker']: r['after'] for r in latest['targets']} == {'A': .3, 'B': 0, 'C': .5}
    assert latest['execution_status'] == 'no_fills' and latest['fills'] == []
    assert first['target_turnover'] is None and first['targets'][0]['before'] is None
    assert before == {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_same_day_versions_compare_to_prior_date_and_count_once(tmp_path):
    rows = deepcopy(ROWS)
    rows[1] = (*rows[1][:-1], 'superseded')
    rows.append(('three', '2026-09-14', {'A': .2, 'C': .5}, .3, 'one', 'pending'))
    out = payload(archive(tmp_path, rows), EMPTY)
    assert out['version_count'] == 3 and out['canonical_count'] == 2
    assert [i['run_id'] for i in out['items'] if i['canonical']] == ['three', 'one']
    assert out['items'][0]['prior_run_id'] == 'one'


def test_missing_explicit_prior_is_not_replaced(tmp_path):
    rows = deepcopy(ROWS)
    rows[1] = (*rows[1][:4], 'missing', 'pending')
    item = payload(archive(tmp_path, rows), EMPTY)['items'][0]
    assert item['comparison_basis'] == 'missing_prior'
    assert item['target_turnover'] is None
    assert all(r['before'] is None for r in item['targets'])


@pytest.mark.parametrize('paper,state', [(None, 'unavailable'), ({'sessions': [], 'corrupt_ledgers': [{}]}, 'incomplete')])
def test_missing_or_corrupt_ledger_never_means_zero_fills(tmp_path, paper, state):
    out = payload(archive(tmp_path, ROWS), paper)
    assert out['ledger_state'] == state
    assert out['items'][0]['execution_status'] == 'unknown'
    assert out['warnings']


def test_fill_links_through_its_order_run_and_strategy(tmp_path):
    paper = {'corrupt_ledgers': [], 'sessions': [{'session_id': 'paper1', 'external_checkpoint': {'synchronized': False},
        'orders': [{'order_id': 'o1', 'run_id': 'two', 'strategy_id': 'stock_select', 'currency': 'USD'},
                   {'order_id': 'o2', 'run_id': 'one', 'strategy_id': 'another_strategy'}],
        'fills': [{'fill_id': 'f1', 'order_id': 'o1', 'symbol': 'C', 'quantity': '3', 'price': '100'},
                  {'fill_id': 'f2', 'order_id': 'o2', 'symbol': 'A'}]}]}
    out = payload(archive(tmp_path, ROWS), paper)
    item = out['items'][0]
    assert item['execution_status'] == 'paper_fills' and item['order_count'] == 1
    assert item['fills'][0]['session_id'] == 'paper1'
    assert not item['fills'][0]['checkpoint_verified']
    assert out['items'][1]['fills'] == []


def test_broken_archive_is_visible_and_not_replaced_with_latest_weights(tmp_path):
    root = archive(tmp_path, ROWS)
    (root/'runs'/'two'/'snapshot.json').write_text('{}')
    out = payload(root, EMPTY)
    assert out['items'][0]['available'] is False and out['warnings']


def test_history_preserves_generation_verdict_but_shows_expired_clock(tmp_path):
    from datetime import datetime, timezone
    from ai_semi_intel.trading_policy import policy
    root = archive(tmp_path, ROWS)
    for name in ('run.json', 'snapshot.json'):
        path = root / 'runs' / 'two' / name
        record = json.loads(path.read_text())
        record['rebalance_policy'] = policy(record['generated_at'], '2026-09-11')
        path.write_text(json.dumps(record))
    before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result = payload(root, EMPTY, now=datetime(2026, 9, 14, 14, tzinfo=timezone.utc))['items'][0]
    assert result['input_ready'] is True and result['current_target_status']['status'] == 'EXPIRED'
    assert result['current_target_status']['actionable_now'] is False
    assert before == {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('name', ['run.json', 'snapshot.json'])
def test_non_object_archive_is_reported_without_endpoint_crash(tmp_path, name):
    root = archive(tmp_path, ROWS)
    (root / 'runs' / 'two' / name).write_text('["bad record"]')
    result = payload(root, EMPTY)
    assert result['items'][0]['available'] is False and result['warnings']


def test_no_archives_or_traversal(tmp_path):
    assert payload(tmp_path, EMPTY)['items'] == []
    root = tmp_path/'runs'
    root.mkdir()
    (root/'index.json').write_text(json.dumps([{'run_id':'../outside','status':'pending','asof_session':'2026-09-14'}]))
    assert payload(tmp_path, EMPTY)['items'][0]['available'] is False


def test_history_route_is_read_only_and_page_is_navigable(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from market_intel import research
    from market_intel.server import create_app
    from market_intel.store import Store
    from market_intel.entities import Company, EntityIndex
    class Paper:
        def paper(self):
            return EMPTY
    monkeypatch.setattr(research, 'HISTORY', archive(tmp_path/'history', ROWS))
    index=EntityIndex([Company(ticker='A', name='A', segment='Memory', aliases=[], weight=.1, cik=1)])
    with TestClient(create_app(store=Store(tmp_path/'store.sqlite'),index=index,platform_service=Paper()),
                    base_url='http://127.0.0.1',client=('127.0.0.1',50000)) as client:
        response=client.get('/api/research/rebalances')
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert response.json()['canonical_count'] == 2
        assert len(response.json()['methods']) == 14
        assert client.get('/api/research/rebalances?construction=hrp&basis=model').json()['weight_basis'] == 'model'
        assert client.get('/api/research/rebalances?construction=unknown').status_code == 400
        page=client.get('/rebalances')
        assert page.status_code == 200 and '调仓历史' in page.text
        assert '/static/rebalances.js?v=' in page.text
        assert client.post('/api/research/rebalances').status_code == 405
