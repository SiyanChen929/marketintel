import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from scripts.platform_event_activity import build_event_profile


def source(path):
    return {"name": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def commodity(tmp_path):
    daily = tmp_path / "stitched_daily.csv"
    # The second active session earns exactly zero. The final session is flat
    # after a sale but has carried risk and a realized loss.
    pd.DataFrame({"date": ["2020-01-01", "2020-01-02", "2020-01-03"],
                  "initial_capital_cny": [100, 100, 100], "equity_cny": [110, 110, 99],
                  "net_pnl_cny": [10, 0, -11], "net_return": [.1, 0, -.11],
                  "gross_notional_cny": [55, 55, 0], "margin_cny": [11, 11, 0],
                  "transaction_cost_cny": [1, 0, 1]}).to_csv(daily, index=False)
    trades = tmp_path / "stitched_trades.csv"
    pd.DataFrame([{"entry_date": "2020-01-01", "exit_date": "2020-01-03", "holding_days": 2,
                   "fold_id": "outer_2020", "portfolio_slot": 1, "trade_number": 1,
                   "organic_completed": True, "basket_id": "A__B",
                   "portfolio_net_pnl_contribution_cny": -1}]).to_csv(trades, index=False)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": [{"path": p.name, "sha256": source(p)["sha256"]} for p in [daily, trades]]}))
    return {"artifact_id": "perf_fixture", "strategy_id": "commodity_arbitrage",
            "sources": [source(manifest), source(daily)], "rows": [{"month": "2020-01", "return": -.01}]}


def msci(tmp_path):
    daily = tmp_path / "first_minute_alpha_portfolio.csv"
    pd.DataFrame({"effective_close_date": ["2020-01-31", "2020-04-30"], "cost_bps": [10, 10],
                  "equity": [1.01, 1.01 * .98], "net_return": [.01, -.02],
                  "gross_return": [.0105, -.019], "gross_exposure": [.5, 1], "n_names": [2, 5]}).to_csv(daily, index=False)
    manifest = tmp_path / "first_minute_alpha_run_manifest.json"
    manifest.write_text('{}')
    return {"artifact_id": "perf_fixture_msci", "strategy_id": "msci_rebalance", "variant": "15:49 → 15:50 proxy · 10 bps",
            "sources": [source(manifest), source(daily)],
            "rows": [{"month": "2020-01", "return": .01}, {"month": "2020-02", "return": 0},
                     {"month": "2020-03", "return": 0}, {"month": "2020-04", "return": -.02}]}


def test_commodity_true_nav_and_active_zero_pnl_exit_are_preserved(tmp_path):
    p = build_event_profile("commodity", commodity(tmp_path), tmp_path / "archive")
    rows = p["rows"]
    assert [r["return"] for r in rows] == pytest.approx([.1, 0, -.1])
    assert np.prod([1 + r["return"] for r in rows]) == pytest.approx(.99)
    assert all(r["active"] for r in rows)
    assert rows[1]["net_pnl"] == 0
    assert rows[2]["gross_close"] == 0 and rows[2]["gross_open"] == pytest.approx(.5)
    assert rows[0]["gross_close"] == pytest.approx(55 / 110)
    assert rows[1]["margin_open"] == pytest.approx(11 / 110)
    assert "post-trade" in p["exposure_basis"] and "intraday peak" in p["exposure_basis"]
    assert p["trade_observations"][0]["holding_sessions"] == 2
    assert len(p["sources"]) == 3


def test_msci_sparse_events_not_calendar_or_one_minute_returns(tmp_path):
    p = build_event_profile("msci", msci(tmp_path), tmp_path / "archive")
    assert p["frequency"] == "event" and len(p["rows"]) == 2
    assert [r["event_id"] for r in p["rows"]] == ["2020-01-31", "2020-04-30"]
    assert [r["net_pnl"] for r in p["rows"]] == pytest.approx([.01, -.0202])
    assert p["rows"][0]["gross_open"] == .5
    assert all(r["margin_open"] is None and r["margin_close"] is None for r in p["rows"])
    assert "15:50:59" in p["holding_period"] and "two minutes" in p["holding_period"]


def test_primary_hash_and_extra_manifest_are_enforced(tmp_path):
    a = commodity(tmp_path)
    (tmp_path / "stitched_daily.csv").write_text("changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        build_event_profile("commodity", a, tmp_path / "archive")
    a = commodity(tmp_path)
    (tmp_path / "stitched_trades.csv").write_text("changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        build_event_profile("commodity", a, tmp_path / "archive")


def test_frozen_archive_is_reproducible_and_corruption_fails_closed(tmp_path):
    a = commodity(tmp_path)
    archive = tmp_path / "archive"
    p = build_event_profile("commodity", a, archive)
    (tmp_path / "stitched_daily.csv").write_text("mutable current output changed")
    assert build_event_profile("commodity", a, archive) == p
    (archive / a["sources"][1]["sha256"]).write_text("archive corrupted")
    with pytest.raises(ValueError, match="checksum mismatch"):
        build_event_profile("commodity", a, archive)


def test_profile_must_reconcile_to_exact_return_artifact(tmp_path):
    a = commodity(tmp_path)
    a["rows"][0]["return"] = .05
    with pytest.raises(ValueError, match="monthly NAV"):
        build_event_profile("commodity", a, tmp_path / "archive")
    a = msci(tmp_path)
    a["rows"][1]["return"] = .05
    with pytest.raises(ValueError, match="monthly NAV"):
        build_event_profile("msci", a, tmp_path / "archive2")


def test_unsupported_strategy_is_not_silently_attached(tmp_path):
    a = commodity(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        build_event_profile("msci", a, tmp_path / "archive")
