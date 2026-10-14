import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from scripts import platform_continuous_activity as activity


def _source(path):
    return {"name": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _artifact(sid, variant, sources, returns):
    return {"artifact_id": "perf_fixture", "strategy_id": sid, "variant": variant,
            "sources": sources, "limitations": ["fixture"],
            "rows": [{"month": "2024-01", "return": float(np.prod(1 + np.array(returns)) - 1)}]}


def _ps_fixture(tmp_path, monkeypatch):
    root = tmp_path / "ps"
    root.mkdir()
    clock = root / "walkforward_top10.py"
    clock.write_text("# reviewed fixture close-to-close clock\n")
    clock_hash = _source(clock)["sha256"]
    monkeypatch.setattr(activity, "_PS_CLOCK_SHA", clock_hash)
    # Entry pays a cost while opening exposure is zero. Next session has
    # a real position and exactly zero return. Final exit pays its own cost.
    nav = np.array([1.0, .9998, .9998, 1.0098, 1.009598])
    returns = nav / np.r_[1.0, nav[:-1]] - 1
    d = pd.DataFrame({"date": pd.date_range("2024-01-02", periods=5),
                      "portfolio_id": "joint_max_sharpe_top10", "equity": nav,
                      "portfolio_return": returns, "gross_exposure": [0, 1 / .9998, 1, 1, 0],
                      "gross_pnl": [0, 0, 0, .01, 0],
                      "transaction_cost": [0, .0002, 0, 0, .000202], "borrow_cost": 0.,
                      "leg_turnover": [0, 1., 0, 0, 1.01]})
    ledger = root / "v10_portfolio_daily.csv.gz"
    d.to_csv(ledger, index=False, compression="gzip")
    manifest = root / "reveal_manifest.json"
    manifest.write_text(json.dumps({"output_files": {"portfolio_daily": {"sha256": _source(ledger)["sha256"]}},
                                    "source_hashes": {"v9_walkforward_module": {"path": str(clock), "sha256": clock_hash}}}))
    return _artifact("pairs_trading", "joint_max_sharpe_top10", [_source(manifest), _source(ledger)], returns), ledger


def test_ps_activity_retains_entry_exit_and_zero_pnl_positions(tmp_path, monkeypatch):
    artifact, _ = _ps_fixture(tmp_path, monkeypatch)
    result = activity.build_continuous_profile("ps", artifact, tmp_path / "archive")
    rows = result["rows"]
    assert [row["active"] for row in rows] == [False, True, True, True, True]
    assert rows[1]["gross_open"] == 0 and rows[1]["return"] < 0
    assert rows[2]["return"] == 0 and rows[2]["gross_open"] > 1
    assert rows[-1]["gross_open"] == 1 and rows[-1]["gross_close"] == 0
    assert all(row["margin_open"] is None and row["margin_close"] is None for row in rows)
    assert all(abs(row["nav_close"] / row["nav_open"] - 1 - row["return"]) < 1e-12 for row in rows)


def test_activity_rejects_mutated_source_and_monthly_mismatch(tmp_path, monkeypatch):
    artifact, ledger = _ps_fixture(tmp_path, monkeypatch)
    raw = ledger.read_bytes()
    ledger.write_bytes(raw + b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        activity.build_continuous_profile("ps", artifact, tmp_path / "first_archive")
    ledger.write_bytes(raw)
    artifact["rows"][0]["return"] += .01
    with pytest.raises(ValueError, match="frozen monthly artifact"):
        activity.build_continuous_profile("ps", artifact, tmp_path / "second_archive")


def test_ps_changed_clock_requires_review(tmp_path, monkeypatch):
    artifact, _ = _ps_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(activity, "_PS_CLOCK_SHA", "0" * 64)
    with pytest.raises(ValueError, match="accounting clock changed"):
        activity.build_continuous_profile("ps", artifact, tmp_path / "archive")


def test_vol_prior_close_exposure_preserves_overnight_exit_pnl(tmp_path):
    root = tmp_path / "vol"
    root.mkdir()
    dates = pd.date_range("2024-01-02", periods=4)
    nav = np.array([100., 100., 120., 130.])
    returns = nav / np.r_[100., nav[:-1]] - 1
    ledger = root / "representative_ledger.parquet"
    pd.DataFrame({"date": dates, "strategy_id": "VB2", "seed_index": 0,
                  "equity": nav, "nav": nav / 100, "daily_return": returns,
                  "gross_exposure": [0., 1., .5, 0.]}).to_parquet(ledger, index=False)
    positions = root / "representative_positions.parquet"
    pd.DataFrame({"date": dates, "strategy_id": "VB2", "seed_index": 0, "security_id": "A",
                  "open": [10., 10., 12., 14.], "close": [10., 10., 12., 10.],
                  "shares_before": [0., 0., 10., 5.], "shares": [0., 10., 5., 0.],
                  "trade_shares": [0., 10., -5., -5.], "trading_cost": 0.}).to_parquet(positions, index=False)
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"artifacts": [{"name": p.name, "sha256": _source(p)["sha256"]} for p in (ledger, positions)]}))
    artifact = _artifact("vol_trading", "VB2 · representative seed 0", [_source(manifest), _source(ledger)], returns)
    result = activity.build_continuous_profile("vol", artifact, tmp_path / "archive")
    rows = result["rows"]
    assert [row["active"] for row in rows] == [False, True, True, True]
    assert [row["gross_open"] for row in rows] == [0., 0., 1., .5]
    assert rows[1]["return"] == 0 and rows[1]["active"]
    assert rows[-1]["gross_close"] == 0 and rows[-1]["net_pnl"] == 10.
    assert "prior-close" in result["capital_basis"]
    assert len(result["sources"]) == 3
    assert (tmp_path / "archive" / _source(positions)["sha256"]).exists()
