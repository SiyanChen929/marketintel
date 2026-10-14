"""Counterexample tests for the construction/backtest audit findings (P0 items 9-15)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ai_semi_intel.portfolio import construction_eval as CE
from market_intel import pit_contract


# --------------------------------------------------------------- 9. one continuous shares + cash ledger
def test_ledger_drifts_with_prices_instead_of_holding_fixed_weights():
    """Between rebalances the book holds shares, so weights move. Fixed target weights are a different book."""
    L = CE.Ledger(cost_bps=0.0)
    p0 = pd.Series({"A": 100.0, "B": 100.0})
    L.rebalance("d0", p0, pd.Series({"A": 0.5, "B": 0.5}))
    p1 = pd.Series({"A": 200.0, "B": 100.0})          # A doubles
    w = L.weights(p1)
    assert w["A"] == pytest.approx(2 / 3, rel=1e-9), "shares are unchanged, so the weight must drift to 2/3"
    assert L.value(p1) == pytest.approx(1.5, rel=1e-9)
    fixed_weight_pnl = 0.5 * 1.0 + 0.5 * 0.0          # the discarded proxy: rebalanced back to 50/50 daily
    assert L.value(p1) - 1.0 == pytest.approx(0.5) == pytest.approx(fixed_weight_pnl)
    p2 = pd.Series({"A": 200.0, "B": 200.0})          # now B doubles
    drifted = L.value(p2) / L.value(p1) - 1.0
    assert drifted == pytest.approx(1 / 3, rel=1e-9), "the drifted book holds only 1/3 in B"
    assert drifted != pytest.approx(0.5), "a fixed-weight proxy would book 0.5 here; that book was never held"


def test_ledger_cannot_value_a_book_whose_holding_has_no_bar():
    L = CE.Ledger(cost_bps=0.0)
    L.rebalance("d0", pd.Series({"A": 100.0, "B": 100.0}), pd.Series({"A": 0.5, "B": 0.5}))
    assert L.value(pd.Series({"A": 110.0, "B": np.nan})) is None, "a missing bar must not be carried forward"


def test_no_session_is_omitted_between_the_signal_and_the_new_book(wf):
    """The close(t) -> close(t+1) fill move belongs to the old book and must be in the record."""
    dates = pd.to_datetime(wf["cumulative"]["dates"])
    assert len(dates) == len(set(dates))
    gaps = pd.Series(dates).diff().dt.days.dropna()
    assert gaps.max() <= 5, "the ledger runs on every session; a fill date is never dropped from the curve"
    fills = {pd.Timestamp(r["fill_date"]) for r in wf["rebalances"]}
    covered = set(dates)
    assert fills <= covered | {min(covered)}, "every fill date must appear in the ledger's own session record"


# --------------------------------------------------------------- 10. no look-ahead at the fill
def test_eligibility_uses_fill_time_information_only():
    """A name that prices at the fill must be bought even if it stops pricing weeks later."""
    idx = pd.bdate_range("2020-01-01", periods=900)
    rng = np.random.default_rng(11)
    px = pd.DataFrame({t: 100 * np.cumprod(1 + rng.normal(0.0005, 0.02, len(idx)))
                       for t in ("AAA", "BBB", "CCC", "DDD")}, index=idx)
    px["SOXX"] = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, len(idx)))
    px.loc[idx[700]:, "DDD"] = np.nan                  # DDD stops pricing partway through
    uni = pd.DataFrame({"market_cap": [1e10] * 4, "segment": ["s1", "s2", "s3", "s4"]}, index=["AAA", "BBB", "CCC", "DDD"])
    out = CE.walk_forward(px, uni, methods=["equal_weight"], start="2021-01-01", top_n=4, min_names=2,
                          cov_years=1, bench="SOXX", max_w=1.0, max_seg=1.0)
    assert out["available"]
    src = __import__("inspect").getsource(CE.walk_forward)
    assert "fill_time_information" in src.replace(" ", "_").lower() or "FILL-TIME information only" in src
    assert "seg_px[t].notna().all()" not in src, "the look-ahead priceability test must be gone"
    ev = out["data_events"]
    for e in ev:
        assert "not_priceable_at_fill" in e, "the only recorded exclusion reason may be a fill-time one"


def test_walk_forward_source_never_inspects_the_future_holding_segment():
    src = __import__("inspect").getsource(CE.walk_forward)
    assert "hold_days" not in src, "selecting on prices across the whole future holding period is look-ahead"
    assert "priceable = [t for t in sel if seg_px" not in src


# --------------------------------------------------------------- 11. the PIT gate must actually gate
def test_junk_pit_file_does_not_open_the_gate(tmp_path, monkeypatch):
    """A present-but-meaningless file must fail validation rather than license a claim."""
    p = tmp_path / "universe_membership.csv"
    p.write_text("ticker,effective_from,effective_to,source\n")           # header only: no rows
    spec = {**pit_contract.REQUIREMENTS["pit_universe"], "path": p}
    res = pit_contract.validate("pit_universe", spec)
    assert res["present"] is True and res["valid"] is False
    assert res["failed_check"] == "non_trivial"
    p.write_text("foo,bar\n1,2\n3,4\n")                                    # wrong schema
    assert pit_contract.validate("pit_universe", {**spec, "path": p})["failed_check"] == "schema"
    p.write_text("ticker,effective_from,effective_to,source\nAAA,notadate,,\nBBB,alsonot,,\n")
    assert pit_contract.validate("pit_universe", {**spec, "path": p})["failed_check"] == "dates_parse"
    p.write_text("ticker,effective_from,effective_to,source\nAAA,2020-01-01,,\nBBB,2020-01-02,,\n")
    assert pit_contract.validate("pit_universe", {**spec, "path": p})["failed_check"] == "vintage", \
        "an unattributed file is not point-in-time evidence"


def test_pit_coverage_and_identifiers_are_checked(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("ticker,effective_from,effective_to,source\nAAA,2020-01-01,,crsp\nBBB,2020-06-01,,crsp\n")
    spec = {**pit_contract.REQUIREMENTS["pit_universe"], "path": p}
    assert pit_contract.validate("pit_universe", spec, window=("2013-01-01", "2026-01-01"))["failed_check"] == "coverage"
    assert pit_contract.validate("pit_universe", spec, tickers=["ZZZ"])["failed_check"] == "identifiers"
    assert pit_contract.validate("pit_universe", spec, window=("2020-02-01", "2020-05-01"), tickers=["AAA"])["valid"]


def test_require_pit_takes_a_real_branch_and_withholds_level_claims(wf):
    enf = wf["pit_enforcement"]
    assert enf["require_pit"] is True and enf["gate_open"] is False
    assert enf["branch_taken"] == "MECHANISM_DIAGNOSTIC_ONLY"
    assert {"cagr", "sharpe", "total_return", "alpha_pa"} <= set(enf["withheld"])
    for m, st in wf["stats"].items():
        assert "cagr" not in st and "sharpe" not in st, f"{m} still publishes a level claim"
        assert "cagr" in st.get("withheld", {}), f"{m} must retain the number under 'withheld'"
    assert "MECHANISM DIAGNOSTIC ONLY" in wf["claim_status"]


# --------------------------------------------------------------- 12. no impostor equal-weight book
def test_a_failed_solver_never_publishes_equal_weight_under_its_name(monkeypatch):
    from ai_semi_intel.portfolio import constructions as C
    real = C.build

    def boom(name, ctx):
        if name == "hrp":
            raise RuntimeError("solver exploded")
        return real(name, ctx)

    monkeypatch.setattr(C, "build", boom)
    idx = pd.bdate_range("2020-01-01", periods=900)
    rng = np.random.default_rng(5)
    px = pd.DataFrame({t: 100 * np.cumprod(1 + rng.normal(0.0005, 0.02, len(idx)))
                       for t in ("AAA", "BBB", "CCC")}, index=idx)
    px["SOXX"] = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, len(idx)))
    uni = pd.DataFrame({"market_cap": [1e10] * 3, "segment": ["s1", "s2", "s3"]}, index=["AAA", "BBB", "CCC"])
    out = CE.walk_forward(px, uni, methods=["equal_weight", "hrp"], start="2021-01-01", top_n=3,
                          min_names=2, cov_years=1, bench="SOXX", max_w=1.0, max_seg=1.0)
    assert "hrp" not in out["methods"], "a method that never solved must not appear as a result"
    assert "hrp" in out["failed_methods"]
    assert out["failed_methods"]["hrp"]["n_failures"] > 0
    assert "NEVER published" in out["failed_methods"]["hrp"]["treatment"]
    assert "hrp" not in out["stats"]
    src = __import__("inspect").getsource(CE.walk_forward)
    assert "w_full = pd.Series(1.0 / len(sel), index=sel)" not in src


# --------------------------------------------------------------- 13. the cost ledger is closed
def test_initial_allocation_is_charged_one_side_only():
    L = CE.Ledger(cost_bps=10.0)
    rec = L.rebalance("d0", pd.Series({"A": 100.0, "B": 100.0}), pd.Series({"A": 0.5, "B": 0.5}))
    assert rec["traded_notional"] == pytest.approx(1.0), "buying into an empty book trades NAV once"
    assert rec["cost"] == pytest.approx(1.0 * 10 / 1e4)
    assert rec["cost"] != pytest.approx(2.0 * 10 / 1e4), "the initial allocation is not two sides"


def test_rebalance_cost_uses_actual_traded_notional():
    L = CE.Ledger(cost_bps=0.0)
    L.rebalance("d0", pd.Series({"A": 100.0, "B": 100.0}), pd.Series({"A": 0.5, "B": 0.5}))
    L.cost_bps = 10.0
    rec = L.rebalance("d1", pd.Series({"A": 100.0, "B": 100.0}), pd.Series({"A": 0.6, "B": 0.4}))
    assert rec["traded_notional"] == pytest.approx(0.2, rel=1e-6), "0.1 sold and 0.1 bought"
    assert rec["cost"] == pytest.approx(0.2 * 10 / 1e4)


def test_terminal_liquidation_is_inside_the_reported_net_return(wf):
    for m, st in wf["stats"].items():
        if st.get("family") == "benchmark":
            continue
        assert st["liquidation_cost"] is not None and st["liquidation_cost"] > 0
        assert st["terminal_nav_net_of_liquidation"] < st["terminal_nav_pre_liquidation"]
        assert st["terminal_return_net_of_liquidation"] == pytest.approx(
            st["terminal_nav_net_of_liquidation"] - 1.0, rel=1e-9)
    assert "inside the published level series" in wf["ledger"]["terminal_liquidation"]


# --------------------------------------------------------------- 14. production semantics
def test_production_semantics_are_stated_and_no_production_rank_is_published(wf):
    ps = wf["production_semantics"]
    assert ps["production_rank"] is None
    assert "not yet validated" in ps["production_rank_reason"]
    assert "Black-Litterman posterior covariance is fed to every method" in ps["research_arm_differs_by"]
    assert "every research arm is fully invested with no cash" in ps["research_arm_differs_by"]
    assert ps["production_covariance"].startswith("raw")


def test_production_replica_uses_raw_covariance_and_retains_cash(wf):
    if wf["production_semantics"]["replica_arm"] is None:
        pytest.skip("replica arm not run")
    assert CE.PRODUCTION_ARM in wf["methods"]
    scales = [r["production_scale"] for r in wf["rebalances"] if "production_scale" in r]
    assert scales and all(0 < s <= 1.0 for s in scales)
    assert any(s < 0.999 for s in scales), "the production book holds cash; a fully-invested replica is not it"
    src = __import__("inspect").getsource(CE.walk_forward)
    assert "ctx_prod = C.Ctx(cov=cov_raw" in src, "the replica must not receive the BL posterior covariance"


# --------------------------------------------------------------- 15. artifact freshness
def test_stale_artifact_is_detected_and_refused():
    import pandas as _pd
    idx = _pd.bdate_range("2024-01-01", periods=60)
    px = _pd.DataFrame({"AAA": np.linspace(100, 120, 60)}, index=idx)
    uni = _pd.DataFrame({"market_cap": [1e10], "segment": ["s"]}, index=["AAA"])
    fresh = {"schema_version": CE.SCHEMA_VERSION, "inputs": CE.input_fingerprint(px, uni)}
    assert CE.verify_artifact(fresh, px, uni)["fresh"] is True
    stale_code = {**fresh, "inputs": {**fresh["inputs"], "code_hash": "0" * 16}}
    v = CE.verify_artifact(stale_code, px, uni)
    assert v["fresh"] is False and "code_hash" in v["stale_reasons"] and "STALE" in v["verdict"]
    old_schema = {**fresh, "schema_version": "construction_eval.v1"}
    assert CE.verify_artifact(old_schema, px, uni)["fresh"] is False
    px2 = px.copy(); px2.iloc[0, 0] = 99.0                      # a corrected early bar, same dates and shape
    assert CE.verify_artifact(fresh, px2, uni)["fresh"] is False, \
        "the fingerprint must cover the whole history, not only the tail"


def test_fingerprint_covers_full_history_not_just_the_tail():
    idx = pd.bdate_range("2020-01-01", periods=600)
    px = pd.DataFrame({"AAA": np.linspace(100, 200, 600)}, index=idx)
    uni = pd.DataFrame({"market_cap": [1e10], "segment": ["s"]}, index=["AAA"])
    a = CE.input_fingerprint(px, uni)
    px2 = px.copy(); px2.iloc[10, 0] = 1.0                      # far outside tail(250)
    b = CE.input_fingerprint(px2, uni)
    assert a["price_hash_tail250"] == b["price_hash_tail250"], "the old fingerprint could not see this"
    assert a["price_hash_full_history"] != b["price_hash_full_history"]


# --------------------------------------------------------------- ledger accounting contract (audit round 2)
def test_cash_actually_accrues_the_risk_free_rate():
    """A book that reports a cash remainder earning rf must actually credit it."""
    L = CE.Ledger(cost_bps=0.0, rf=0.05)
    L.cash = 1.0
    L.accrue_cash(365.25)
    assert L.cash == pytest.approx(1.05, rel=1e-9), "one year of cash at 5% must earn 5%"
    assert L.accrued_days == pytest.approx(365.25)
    L2 = CE.Ledger(cost_bps=0.0, rf=0.05)
    L2.cash = 1.0
    L2.accrue_cash(0)
    assert L2.cash == 1.0, "a zero interval must not accrue"


def test_walk_forward_calls_accrue_cash_every_session(wf):
    """The dead-code defect: accrue_cash existed but was never called from the loop."""
    src = __import__("inspect").getsource(CE.walk_forward)
    assert "L.accrue_cash(elapsed)" in src, "cash must accrue once per session"
    assert "elapsed = (t - prev_t).days" in src, "accrual must use the real elapsed interval"
    prod = wf["stats"].get(CE.PRODUCTION_ARM)
    if prod is None:
        pytest.skip("production replica not run")
    assert prod["cash_accrual_days"] > 300, "the replica ran for years; accrual days must reflect that"
    assert prod["cash_interest_earned"] > 0, "the replica holds cash, so it must have earned interest"


def test_fully_invested_arms_earn_no_cash_interest(wf):
    """Only a book that holds cash may report cash interest."""
    ew = wf["stats"].get("equal_weight")
    if ew is None:
        pytest.skip("equal_weight not run")
    assert ew["cash_interest_earned"] == pytest.approx(0.0, abs=1e-9)


def test_reported_total_return_is_terminal_nav_net_of_liquidation_minus_funding(wf):
    """The accounting contract: what is published as the return must be what the closed book actually made."""
    for m, st in wf["stats"].items():
        if st.get("family") == "benchmark":
            continue
        vals = {**{k: v for k, v in st.items() if k != "withheld"}, **(st.get("withheld") or {})}
        tot = vals.get("total_return")
        assert tot is not None, f"{m} publishes no total return"
        net_terminal = st["terminal_nav_net_of_liquidation"]
        assert net_terminal is not None, f"{m} did not liquidate"
        # funded at exactly 1.0 on the session before the first trade
        assert tot == pytest.approx(net_terminal - 1.0, rel=1e-9, abs=1e-9), (
            f"{m}: reported total return {tot} != closed-book terminal NAV {net_terminal} - 1.0")
        assert st["terminal_return_net_of_liquidation"] == pytest.approx(tot, rel=1e-9, abs=1e-9)


def test_published_statistics_are_net_of_the_exit_cost(wf):
    """Previously the curve stopped before liquidation while total_cost already included it."""
    for m, st in wf["stats"].items():
        if st.get("family") == "benchmark":
            continue
        assert st["terminal_nav_net_of_liquidation"] < st["terminal_nav_pre_liquidation"], \
            "closing the book costs money, so the closed NAV must be lower"
        assert "CLOSED BOOK" in st["accounting_basis"]
    assert "inside the published level series" in wf["ledger"]["terminal_liquidation"]


def test_equity_curve_starts_at_funded_par(wf):
    """The initial allocation cost must be inside the return, so the curve starts at 1.0 before the first trade."""
    for m in wf["methods"]:
        first = wf["cumulative"][m][0]
        assert first == pytest.approx(1.0, abs=1e-9), f"{m} curve starts at {first}, not funded par"
        last = wf["cumulative"][m][-1]
        st = wf["stats"][m]
        assert last == pytest.approx(st["terminal_nav_net_of_liquidation"], rel=1e-3), \
            "the curve must end on the closed-book NAV"
