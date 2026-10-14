"""Bounded exposure/cost sensitivity of the frozen MSCI event candidate.

This does not discover a window, alter signals, run a source strategy, or prove
that scaled notional is executable. Event dates remain the resampling unit.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

from market_intel import config
from market_intel.platform.performance import PerformanceStore


DEFAULT_ARTIFACT = "perf_1cdf5f218f24c7f249199d3ead0d6108a0653a342b7235115a1cc1d7e969ccb9"
SEED = 20260904
WINDOWS = (("full_54_months", "2022-02", "2026-07", 54),
           ("walkforward_18_months", "2025-02", "2026-07", 18))


class Evidence:
    def __init__(self, archive, output):
        self.archive, self.output, self.sources = Path(archive), Path(output), []

    def read(self, path, expected=None, scope="frozen artifact or frozen-manifest anchor"):
        path = Path(path)
        if any(part.lower().startswith("sealed") for part in path.parts):
            raise ValueError("sealed sources cannot enter this revealed research assessment")
        cache = self.archive / expected if expected else None
        raw = cache.read_bytes() if cache and cache.exists() else path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if expected and checksum != expected:
            raise ValueError(f"source hash mismatch: {path.name}")
        target = self.output / "sources" / checksum
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
            raise ValueError("audit source snapshot was corrupted")
        if not target.exists():
            target.write_bytes(raw)
        item = {"name": str(path), "sha256": checksum, "scope": scope,
                "snapshot": str(target.resolve())}
        if item not in self.sources:
            self.sources.append(item)
        return raw


def scaled_returns(gross, exposure, multiplier, extra_bps):
    gross, exposure = np.asarray(gross, float), np.asarray(exposure, float)
    if gross.shape != exposure.shape or not np.isfinite(gross).all() or not np.isfinite(exposure).all() or (exposure < 0).any():
        raise ValueError("gross returns and gross exposures must be finite, matched arrays")
    result = multiplier * (gross - exposure * (10 + extra_bps) / 10000)
    if (result <= -1).any():
        raise ValueError("scaled scenario is insolvent and requires default accounting")
    return result


def describe(returns):
    r = np.asarray(returns, float)
    path = np.r_[1.0, np.cumprod(1 + r)]
    return {"mean_event_return": float(r.mean()), "event_return_sample_std": float(r.std(ddof=1)) if len(r) > 1 else None,
            "worst_event_return": float(r.min()), "positive_events": int((r > 0).sum()),
            "total_return": float(path[-1] - 1),
            "max_event_end_drawdown": float(np.min(path / np.maximum.accumulate(path) - 1))}


def leave_one_out(returns, dates):
    totals = [float(np.prod(1 + np.delete(returns, i)) - 1) for i in range(len(returns))]
    return {"unit": "one entire review date removed; no securities removed individually",
            "min_total_return": min(totals), "max_total_return": max(totals),
            "positive_total_return_count": sum(x > 0 for x in totals), "replicates": len(totals),
            "rows": [{"omitted_event_date": day, "total_return": total} for day, total in zip(dates, totals)]}


def resample_indices(n, samples, seed, block_length=1):
    rng = np.random.default_rng(seed)
    if block_length == 1:
        return rng.integers(0, n, size=(samples, n))
    starts = rng.integers(0, n, size=(samples, int(np.ceil(n / block_length))))
    return ((starts[..., None] + np.arange(block_length)) % n).reshape(samples, -1)[:, :n]


def bootstrap(returns, indices, label):
    sampled = np.asarray(returns)[indices]
    means = sampled.mean(axis=1)
    totals = np.prod(1 + sampled, axis=1) - 1
    result = {"method": label, "samples": len(indices), "event_dates_per_resample": indices.shape[1],
              "scope": "Descriptive percentile intervals conditional on this retrospective sample; no correction for window/strategy selection, regime change, execution error or unobserved tail losses."}
    for name, q in (("90", [.05, .95]), ("95", [.025, .975])):
        result[f"mean_event_return_interval_{name}"] = np.quantile(means, q).tolist()
        result[f"same_event_count_total_return_interval_{name}"] = np.quantile(totals, q).tolist()
    return result


def event_aggregate(ledger, column):
    records = []
    for date, group in ledger.dropna(subset=[column]).groupby("effective_close_date"):
        adds = group.loc[group.action.eq("add"), column]
        deletes = group.loc[group.action.eq("delete"), column]
        gross = .5 * (float(adds.mean()) if len(adds) else 0) + .5 * (float(deletes.mean()) if len(deletes) else 0)
        exposure = .5 * bool(len(adds)) + .5 * bool(len(deletes))
        records.append({"date": date, "gross_return": gross, "gross_exposure": exposure,
                        "net_return_10bps": gross - exposure * .001, "security_events": len(group)})
    return records


def hand_checks():
    actual = scaled_returns([.01, -.005], [1, .5], 2, 5)
    assert np.allclose(actual, [.017, -.0115], atol=1e-14, rtol=0)
    assert np.isclose(np.prod(1 + actual) - 1, .0053045, atol=1e-14)
    loo = leave_one_out(np.array([.1, -.1, 0]), ["a", "b", "c"])
    assert np.allclose([r["total_return"] for r in loo["rows"]], [-.1, .1, -.01], atol=1e-14)
    assert loo["positive_total_return_count"] == 1
    indices = resample_indices(3, 20, SEED)
    check = bootstrap(np.array([.01] * 3), indices, "check")
    assert np.allclose(check["mean_event_return_interval_95"], [.01, .01])
    assert np.allclose(check["same_event_count_total_return_interval_95"], [1.01 ** 3 - 1] * 2)
    assert resample_indices(6, 100, SEED, 4).shape == (100, 6)
    return [{"check": "2x exposure scales gross and per-gross cost, then event returns compound", "passed": True,
             "gross": [.01, -.005], "exposure": [1, .5], "extra_bps": 5, "expected_net_returns": [.017, -.0115], "expected_total_return": .0053045},
            {"check": "Leave-one-out removes full event dates and retains compounding", "passed": True},
            {"check": "Constant-return event bootstrap has degenerate correct intervals; block draws preserve event count", "passed": True}]


def assess(artifact, evidence, samples):
    if artifact["strategy_id"] != "msci_rebalance" or not artifact["variant"].endswith("· 10 bps"):
        raise ValueError("assessment requires the frozen MSCI 10 bps baseline")
    originals = {Path(s["name"]).name: (s, evidence.read(s["name"], s["sha256"])) for s in artifact["sources"]}
    manifest = json.loads(originals["first_minute_alpha_run_manifest.json"][1])
    root = Path(originals["first_minute_alpha_run_manifest.json"][0]["name"]).parent
    portfolio = pd.read_csv(io.BytesIO(originals["first_minute_alpha_portfolio.csv"][1]))
    portfolio = portfolio.loc[portfolio.cost_bps.eq(10)].copy()
    if portfolio.empty or portfolio.effective_close_date.duplicated().any() or not portfolio.effective_close_date.is_monotonic_increasing:
        raise ValueError("event dates must be present, unique and ordered")
    nav = portfolio.equity.to_numpy(float)
    returns = nav / np.r_[1.0, nav[:-1]] - 1
    expected = scaled_returns(portfolio.gross_return, portfolio.gross_exposure, 1, 0)
    if not np.allclose(returns, expected, rtol=0, atol=1e-10) or not np.allclose(returns, portfolio.net_return, rtol=0, atol=1e-10):
        raise ValueError("frozen NAV, source costs and event returns do not reconcile")
    ledger = pd.read_csv(io.BytesIO(evidence.read(root / "trade_ledger.csv", manifest["ledger_sha256"])))
    spec = json.loads(evidence.read(root / "first_minute_alpha_system_spec.json", scope="Current revealed specification, hashed for this audit; not an original frozen output-manifest anchor"))
    selection = pd.read_csv(io.BytesIO(evidence.read(root / "first_minute_alpha_selection_adjusted_tests.csv", scope="Existing revealed full-window selection audit; contextual evidence, not recalculated independent validation")))
    report = {"schema_version": "platform.msci-exposure-audit.v1", "validation_status": "NOT VALIDATED",
              "artifact_id": artifact["artifact_id"], "variant": artifact["variant"], "seed": SEED,
              "scope": "Pre-specified 1x/2x/3x/5x same-event notional sensitivity and +0/+5/+10/+20 bps per-unit-gross execution stress. No strategy retuning or window selection.",
              "clock": spec["bar_semantics"], "production_clock_contract_not_fill_evidence": spec["production_replay_contract"],
              "event_sampling_unit": "Review date is the cluster unit; within-date security events are not independent. Date observations can still be dependent across regimes.",
              "formulas": {"scaled_event_return": "L * (source_gross_return - source_gross_exposure * (10 + extra_bps) / 10000)",
                           "event_nav": "NAV[t] = NAV[t-1] * (1 + scaled_event_return[t]); no return on intervening reserved cash",
                           "additional_cost_breakeven_bps": "10000 * sum(source_net_return_10bps) / sum(source_gross_exposure); arithmetic event mean becomes zero; not a geometric-growth or capacity break-even",
                           "master_independent_10pct_sleeve": "Master NAV = 0.90 + 0.10 * event_sleeve_NAV, with the other 90% held cash. This is an isolated contribution illustration, not the other strategies' combined performance."},
              "capital_clock": {"initial_event_gross_fraction_of_master_if_sleeve_weight_10pct": {str(L): {"two_sided_event": .1 * L, "one_side_event": .05 * L} for L in (1, 2, 3, 5)},
                                "explanation": "5x inside a 10% sleeve initially deploys 50% gross notional of master NAV on two-sided events, or 25% on one-sided events. It does not mean 5x total-portfolio leverage. Independent sleeve weights drift with P&L.",
                                "funding_margin_borrow_capacity": "UNKNOWN. Event trading duration does not establish collateral-reservation duration, short availability, executable size or safely transferable idle cash.",
                                "actual_fill_timestamps": None, "known_bar_coverage_seconds_approximate": 120,
                                "reservation_duration_seconds": None},
              "existing_selection_audit": json.loads(selection.to_json(orient="records")), "windows": {},
              "checks": hand_checks(), "limitations": ["Both windows are retrospective: the baseline clock was selected after outcomes were inspected. The final 18 months match platform chronological allocation evaluation but are not a newly sealed strategy holdout.",
                "IID date bootstrap assumes exchangeability; circular four-date blocks are a limited dependence sensitivity. With only 18 or 6 dates neither interval establishes independent effectiveness or bounds unseen tail risk.",
                "Leverage scales a fixed trade-price proxy with linear costs. Market impact, spread widening, borrow, margin, rejects, partial fills and financing do not necessarily scale linearly and are not supplied by these inputs.",
                "Event-end drawdown omits within-event adverse excursions. Return per minute, annualized active-event Sharpe and return on assumed freed collateral are deliberately not reported.",
                "Alternative window endpoint comparisons below are labeled diagnostics; announcement-to-implementation results lack the complete daily self-financing and funding ledger needed to compare platform NAVs."]}
    for window_id, start, end, months in WINDOWS:
        selected = portfolio.loc[portfolio.effective_close_date.str[:7].between(start, end)].copy()
        dates = selected.effective_close_date.tolist()
        gross, exposure = selected.gross_return.to_numpy(), selected.gross_exposure.to_numpy()
        net = scaled_returns(gross, exposure, 1, 0)
        monthly = {r["month"]: r["return"] for r in artifact["rows"] if start <= r["month"] <= end}
        if len(monthly) != months or not np.isclose(np.prod(1 + net), np.prod([1 + r for r in monthly.values()]), atol=1e-10, rtol=0):
            raise ValueError("event window does not reconcile with complete frozen calendar months")
        local_ledger = ledger.loc[ledger.effective_close_date.str[:7].between(start, end)]
        control = event_aggregate(local_ledger, "first_minute_alpha_return_gross")
        if [x["date"] for x in control] != dates or not np.allclose([x["net_return_10bps"] for x in control], net, atol=1e-10, rtol=0):
            raise ValueError("security ledger does not reconstruct the frozen event portfolio")
        n = len(net)
        indices = resample_indices(n, samples, SEED + months)
        blocks = resample_indices(n, samples, SEED + months + 400, 4)
        summary = {"start": start, "end": end, "calendar_months": months, "review_date_observations": n,
                   "security_events": int(selected.n_names.sum()), "gross_exposure_mean": float(exposure.mean()),
                   "base_10bps": describe(net), "additional_cost_breakeven_bps": float(10000 * net.sum() / exposure.sum()),
                   "event_rows": [{"date": day, "source_gross_return": float(g), "source_gross_exposure": float(e),
                                   "source_10bps_cost": float(e * .001), "source_net_return": float(r)} for day, g, e, r in zip(dates, gross, exposure, net)],
                   "scenarios": [], "alternative_window_diagnostics": []}
        for multiplier in (1, 2, 3, 5):
            for extra in (0, 5, 10, 20):
                r = scaled_returns(gross, exposure, multiplier, extra)
                stats = describe(r)
                summary["scenarios"].append({"multiplier": multiplier, "additional_cost_bps_per_gross": extra,
                    "total_cost_bps_per_gross": 10 + extra, **stats,
                    "calendar_cagr": (1 + stats["total_return"]) ** (12 / months) - 1,
                    "mean_scaled_gross_event_return": float(multiplier * gross.mean()),
                    "mean_scaled_source_cost_event_return": float(multiplier * exposure.mean() * .001),
                    "mean_scaled_additional_cost_event_return": float(multiplier * exposure.mean() * extra / 10000),
                    "master_return_from_initial_10pct_sleeve_other_cash": .1 * stats["total_return"],
                    "leave_one_event_out": leave_one_out(r, dates),
                    "bootstrap_iid_event_dates": bootstrap(r, indices, "IID resampling of whole review dates"),
                    "bootstrap_circular_four_dates": bootstrap(r, blocks, "Circular blocks of four chronological review dates"),
                    "event_returns": r.tolist()})
        for column, label in (("pre_1550_return_gross", "15:49 open → 15:50 open; pre-15:50 endpoint proxy"),
                              ("release_minute_return_gross", "15:50 start-bar open → same-bar close"),
                              ("first_minute_alpha_return_gross", "Frozen 15:49 open → 15:50 start-bar close"),
                              ("flow_return_gross", "15:49 open → implementation daily-close proxy"),
                              ("announcement_drift_return_gross", "First session after announcement-date open → implementation close")):
            event_rows = event_aggregate(local_ledger, column)
            values = np.array([r["net_return_10bps"] for r in event_rows])
            d = describe(values)
            item = {"source_column": column, "clock": label, "review_dates": len(values),
                    "security_events": sum(r["security_events"] for r in event_rows),
                    "mean_endpoint_net_return_10bps": d["mean_event_return"],
                    "endpoint_chain_return_not_complete_strategy_nav": d["total_return"],
                    "source_anchored": True, "status": "DIAGNOSTIC ONLY; no window switch or independent validation"}
            if column == "announcement_drift_return_gross":
                valid = local_ledger.dropna(subset=[column, "drift_entry_date"])
                days = (pd.to_datetime(valid.effective_close_date) - pd.to_datetime(valid.drift_entry_date)).dt.days
                item.update(median_security_holding_calendar_days=float(days.median()), min_security_holding_calendar_days=int(days.min()),
                            max_security_holding_calendar_days=int(days.max()),
                            limitation="Endpoint returns are observed, but daily mark-to-market, cash flows, multi-day financing/borrow and overlapping capital use were not reconstructed. This is a different strategy from the frozen event-minute candidate.")
            summary["alternative_window_diagnostics"].append(item)
        report["windows"][window_id] = summary
        report["checks"].append({"check": f"{window_id}: security-ledger date aggregation, frozen event NAV and {months} complete monthly returns reconcile", "passed": True})
    report["sources"] = evidence.sources
    return report


def markdown(report):
    lines = ["# MSCI 事件敞口审查", "", "状态：**NOT VALIDATED**。所有倍率保持现有 15:49 开盘代理至 15:50 start-bar 收盘的事件窗口；不改变信号和入场时钟。", ""]
    for name, w in report["windows"].items():
        lines += [f"## {w['start']} 至 {w['end']}：{w['review_date_observations']} 个事件日期", "",
                  f"原 10 bps 情景每事件净均值 {w['base_10bps']['mean_event_return']*1e4:.2f} bps；标准差 {w['base_10bps']['event_return_sample_std']*1e4:.2f} bps。额外成本达到 {w['additional_cost_breakeven_bps']:.2f} bps / gross notional 时，样本算术净均值降至零。", "",
                  "| 事件倍率 | 原 10 bps 累计 | 额外 +5 bps | 额外 +10 bps | 额外 +20 bps |", "|---|---:|---:|---:|---:|"]
        for L in (1, 2, 3, 5):
            values = [x["total_return"] for x in w["scenarios"] if x["multiplier"] == L]
            lines.append(f"| {L}× | " + " | ".join(f"{x:.3%}" for x in values) + " |")
        base = w["scenarios"][0]
        ci = base["bootstrap_iid_event_dates"]["mean_event_return_interval_95"]
        loo = base["leave_one_event_out"]
        lines += ["", f"1× 原成本情景，逐次删除一个事件后的累计收益范围 {loo['min_total_return']:.3%} 至 {loo['max_total_return']:.3%}，{loo['positive_total_return_count']}/{loo['replicates']} 为正。按事件日期重采样的描述性 95% 均值区间为 [{ci[0]*1e4:.2f}, {ci[1]*1e4:.2f}] bps；完整 JSON 同时提供 90% 区间、四事件块敏感性和所有成本情景。", ""]
    lines += ["## 资金与时间口径", "", "10% 策略资金 × 5 倍事件敞口，双边齐全时对应初始总组合 50% gross notional；单边事件为 25%。事件约两分钟的价格窗口不能证明资金只需预留两分钟。保证金、借券、容量、实际成交时刻与可跨策略调拨资金仍未知。", "", "18 个月区间与平台滚动训练后的评估期一致，但既有事件窗口由事后研究选择，不能称为新的独立留出验证。放大正收益与损失都依赖线性执行假设。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=config.DATA_DIR / "platform")
    parser.add_argument("--artifact-id", default=DEFAULT_ARTIFACT)
    parser.add_argument("--bootstrap-samples", type=int, default=50000)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/platform_exposure_20260904"))
    args = parser.parse_args()
    if args.bootstrap_samples < 1000:
        raise ValueError("at least 1000 descriptive bootstrap samples are required")
    artifact = PerformanceStore(args.state_dir / "performance.sqlite").get(args.artifact_id)
    evidence = Evidence(args.state_dir / "performance_sources", args.output_dir)
    report = assess(artifact, evidence, args.bootstrap_samples)
    report["calculator_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "msci_event_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    (args.output_dir / "msci_event_audit.md").write_text(markdown(report))
    for key, window in report["windows"].items():
        print(json.dumps({"window": key, "n_dates": window["review_date_observations"], "base": window["base_10bps"],
                          "additional_cost_breakeven_bps": window["additional_cost_breakeven_bps"]}))


if __name__ == "__main__":
    main()
