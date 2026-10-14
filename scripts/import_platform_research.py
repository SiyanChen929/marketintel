#!/usr/bin/env python3
"""Freeze reviewed local research outputs into the platform return contract.

Usage: python -m scripts.import_platform_research --recipe ps --root OUTPUT_DIR
Recipes read only their explicit root. They never run/refit the source strategy.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

from market_intel.platform.performance import PerformanceStore, SCHEMA


class Snapshot:
    def __init__(self, root, archive):
        self.root, self.archive, self.sources = Path(root), Path(archive), []
        self.archive.mkdir(parents=True, exist_ok=True)

    def read(self, name, expected=None):
        raw = (self.root / name).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if expected is not None and sha != expected:
            raise ValueError(f"source manifest mismatch: {name}")
        target = self.archive / sha
        if not target.exists():
            target.write_bytes(raw)
        elif hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            raise ValueError("frozen source archive checksum mismatch")
        self.sources.append({"name": str(self.root / name), "sha256": sha})
        return raw

    def json(self, name):
        return json.loads(self.read(name))

    def frame(self, name, expected=None):
        raw = self.read(name, expected)
        return pd.read_parquet(io.BytesIO(raw)) if name.endswith('.parquet') else pd.read_csv(io.BytesIO(raw), compression='gzip' if name.endswith('.gz') else None)


def from_nav(frame, date, nav, initial):
    d = frame.copy()
    d["date"] = pd.to_datetime(d[date])
    if d["date"].duplicated().any() or not d["date"].is_monotonic_increasing:
        raise ValueError("source dates must be sorted and unique")
    levels = d[nav].to_numpy(float)
    if not np.isfinite(levels).all() or (levels <= 0).any():
        raise ValueError("source NAV is missing, nonfinite or insolvent")
    previous = np.r_[initial, levels[:-1]]
    d["platform_return"] = levels / previous - 1
    return d


def full_months(frame):
    """Conservatively omit both endpoint months, regardless of apparent coverage.

    Month-end NAV captures cumulative economics. Daily calendar completeness is
    source-declared, remains a limitation, and is never promoted to validated.
    """
    d = frame.copy()
    periods = pd.to_datetime(d.date).dt.to_period("M")
    if d.date.duplicated().any() or not pd.to_datetime(d.date).is_monotonic_increasing:
        raise ValueError("source dates must be sorted and unique")
    if not np.isfinite(d.platform_return).all() or (d.platform_return <= -1).any():
        raise ValueError("invalid source returns")
    out = d.assign(month=periods.astype(str)).groupby("month", sort=True).platform_return.apply(lambda r: float(np.prod(1 + r) - 1))
    out = out.iloc[1:-1]
    if len(out) < 3:
        raise ValueError("insufficient complete interior months")
    return [{"month": m, "return": r} for m, r in out.items()]


def artifact(snapshot, sid, name, variant, currency, asset_class, rows, costs, clock, construction, limitations):
    return dict(schema_version=SCHEMA, strategy_id=sid, name=name, variant=variant, run_id=snapshot.root.name,
                currency=currency, asset_class=asset_class, calendar="Source-declared sessions aggregated to calendar months; endpoint months omitted",
                costs=costs, clock=clock, return_construction=construction, frequency="monthly", return_basis="net_nav",
                sample_type="historical_research", source_verdict="NOT VALIDATED", limitations=limitations + [
                    "Daily session completeness and source fills are not independently re-audited by this import. First and last source months are excluded conservatively."],
                sources=list(snapshot.sources), rows=rows)


def build(recipe, root, archive):
    s = Snapshot(root, archive)
    if recipe == "vol":
        m = s.json("manifest.json")
        item = next(x for x in m['artifacts'] if x['name'] == 'representative_ledger.parquet')
        d = s.frame('representative_ledger.parquet', item['sha256'])
        if set(d.seed_index.unique()) != {0}:
            raise ValueError("expected predetermined representative seed 0; never select by observed performance")
        for variant in ("VB2", "VB1", "BUY_HOLD"):
            f = from_nav(d[d.strategy_id == variant], 'date', 'nav', 1.0)
            if not np.allclose(f.platform_return, f.daily_return, rtol=0, atol=1e-10):
                raise ValueError("Vol Trading source returns do not reconcile with NAV")
            yield artifact(s, 'vol_trading', 'Vol Trading', f'{variant} · representative seed 0', 'USD', 'equity', full_months(f),
                           '5 bps one-way source execution cost; fractional shares; cash 0%',
                           'Source lagged completed daily observations; source execution rules preserved',
                           'Monthly geometric link of reconstructed net NAV returns; selected seed is source representative, not best seed',
                           m['limitations'] + ['Current randomized equity-volatility sleeve; derivatives strategies are not represented by this NAV.'])
    elif recipe == "ps":
        m = s.json('data/reveal_manifest.json')
        d = s.frame('data/v10_portfolio_daily.csv.gz', m['output_files']['portfolio_daily']['sha256'])
        s.read('data/v10_quality_checks.json', m['output_files']['quality_checks']['sha256'])
        s.read('data/protocol_freeze_manifest.json', m['freeze_manifest']['sha256'])
        for variant in ('joint_max_sharpe_top10', 'matched_max_expected_return_top10', 'strict_all_eligible_equal_weight', 'v9_score_top10_reference'):
            f = from_nav(d[d.portfolio_id == variant], 'date', 'equity', 1.0)
            if not np.allclose(f.platform_return, f.portfolio_return, rtol=0, atol=1e-10):
                raise ValueError("PS Trading source returns do not reconcile with NAV")
            yield artifact(s, 'pairs_trading', 'PS Trading', variant, 'USD', 'equity_long_short', full_months(f),
                           'Source transaction and borrow costs embedded in equity; original costs unchanged',
                           'Close[t] signal; Close[t+1] fill; first P&L Close[t+1] to Close[t+2]',
                           'Continuous portfolio equity with initial cash; monthly geometric link of NAV ratios',
                           ['Retrospective allocator design; this history is not independent OOS.', 'Ex-post fixed 114-pair universe; historical PIT membership and executable borrow are incomplete.',
                            'Full historical capital path retains initial idle cash. Fresh-cash variants are not spliced into this history.'])
    elif recipe == "commodity":
        m = s.json('manifest.json')
        h = next(x['sha256'] for x in m['files'] if x['path'] == 'stitched_daily.csv')
        d = s.frame('stitched_daily.csv', h)
        s.json('decision.json')
        initial = float(d.initial_capital_cny.iloc[0])
        if not np.allclose(d.equity_cny, initial + d.net_pnl_cny.cumsum(), rtol=0, atol=1e-6):
            raise ValueError("commodity stitched NAV does not reconcile with cumulative net P&L")
        f = from_nav(d, 'date', 'equity_cny', initial)
        yield artifact(s, 'commodity_arbitrage', 'Commodity Arbitrage', 'Nested max-Sharpe 10 · v1', 'CNY', 'future', full_months(f),
                       'Source commission, slippage and roll costs embedded; 1 bp research convention',
                       'Fold-local inner selection frozen into annual outer folds; source next-session rules unchanged',
                       'Reconstructed equity_cny[t] / equity_cny[t-1] - 1. Original net_return uses fixed initial capital and is NOT compounded.',
                       ['Continuous-dominant futures P&L proxy; dated contracts, executable rolls and effective-dated margin are incomplete.',
                        'Protocol designed after earlier outer results were inspected; selection risk remains.',
                        'Independent capital sleeve: futures gross notional is not treated as funded capital or reused elsewhere.'])
    elif recipe == "msci":
        s.json('first_minute_alpha_run_manifest.json')
        d = s.frame('first_minute_alpha_portfolio.csv')
        for cost in (10.0, 5.0, 20.0):
            f = d[d.cost_bps == cost].copy()
            f = from_nav(f, 'effective_close_date', 'equity', 1.0)
            if not np.allclose(f.platform_return, f.net_return, rtol=0, atol=1e-10):
                raise ValueError("MSCI event equity mismatch")
            # Sparse event NAV only: explicitly reserved capital earns 0 between
            # the source's recorded events. No event is inferred from missing prices.
            month = f.date.dt.to_period('M')
            event = f.assign(month=month).groupby('month').platform_return.apply(lambda r: float(np.prod(1 + r) - 1))
            calendar = pd.period_range(month.min() + 1, month.max() - 1, freq='M')
            rows = [{'month': str(m), 'return': float(event.get(m, 0.0))} for m in calendar]
            yield artifact(s, 'msci_rebalance', 'MSCI Rebalance', f'15:49 → 15:50 proxy · {cost:g} bps', 'USD', 'equity_event_long_short', rows,
                           f'{cost:g} bps source round-trip scenario; gross exposure 1; reserved cash earns 0%',
                           '15:49 start-stamped bar open to 15:50 start-stamped bar close; source event clock',
                           'Source recorded-event NAV; non-event months explicitly modeled as idle cash, 0% return, between known coverage boundaries',
                           ['Post-hoc discovery and window selection; not independent OOS.', 'Minute-bar proxy; NBBO, executable short availability and auction fills are not established.',
                            'Event calendar is source-declared. The scenario includes recorded events only; completeness has not been independently certified.',
                            'Capital remains reserved between events; no cross-strategy reuse of event idle cash.'])
    else:
        raise ValueError('unknown recipe')


def main():
    from market_intel import config
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--recipe', choices=['vol', 'ps', 'commodity', 'msci'], required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--state-dir', type=Path, default=config.DATA_DIR / 'platform')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    store = PerformanceStore(a.state_dir / 'performance.sqlite')
    records = []
    for payload in build(a.recipe, a.root, a.state_dir / 'performance_sources'):
        result = store.import_artifact(payload)
        records.append({**result, 'variant': payload['variant'], 'source_files': payload['sources'], 'first_month': payload['rows'][0]['month'], 'last_month': payload['rows'][-1]['month']})
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    print(json.dumps([{k: v for k, v in r.items() if k != 'source_files'} for r in records], indent=2))

if __name__ == '__main__':
    main()
