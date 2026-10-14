from __future__ import annotations

import numpy as np
import pandas as pd

from ai_semi_intel.research import optuna_tpe as OT
from ai_semi_intel.research import portfolio_sweep as PS


def _two_config_table() -> pd.DataFrame:
    return pd.DataFrame([
        {"method": "erc", "max_weight": .15, "lookback": 756, "n_hold": 15, "hysteresis": 1.0, "vol_target": .25},
        {"method": "equal", "max_weight": .15, "lookback": 756, "n_hold": 15, "hysteresis": 1.0, "vol_target": .25},
    ])


def test_full_union_turnover_counts_exits_and_entries():
    old = pd.Series({"A": .5, "B": .5})
    new = pd.Series({"C": .5, "D": .5})
    assert PS.full_union_turnover(old, new) == 1.0
    assert PS.full_union_turnover(pd.Series(dtype=float), pd.Series({"A": .8})) == .4


def test_execution_dates_are_strictly_after_close_signal():
    idx = pd.bdate_range("2024-01-29", "2024-03-05")
    entry, exit_ = PS.execution_dates(idx, pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29"))
    assert entry == pd.Timestamp("2024-02-01")
    assert exit_ == pd.Timestamp("2024-03-01")
    assert entry > pd.Timestamp("2024-01-31") and exit_ > pd.Timestamp("2024-02-29")


def test_parameter_mapping_is_one_to_one():
    cfg = _two_config_table()
    for i in cfg.index:
        assert OT.params_to_config(cfg, OT.config_params(cfg, i)) == i
    assert PS.default_erc_config(cfg) == 0


def test_weight_solver_enforces_segment_and_name_caps():
    S = np.eye(6)
    z = np.arange(6, dtype=float)
    segments = np.array(["crowded"] * 3 + ["x", "y", "z"])
    for method in ("equal", "inv_vol", "min_var", "erc", "ms_ic03"):
        w = PS._weights(method, S, z, .02, .40, segments, .35)
        assert np.isclose(w.sum(), 1)
        assert w.max() <= .40 + 1e-6
        assert w[:3].sum() <= .35 + 1e-6


def test_weight_solver_keeps_cash_when_name_cap_prevents_full_investment():
    segments = np.array(["a", "a", "b", "b", "c", "c"])
    w = PS._weights("erc", np.eye(6), np.arange(6, dtype=float), .02, .10, segments, .35)
    assert np.isclose(w.sum(), .60)
    assert w.max() <= .10 + 1e-8


def test_tpe_fold_cannot_see_outer_year_and_is_reproducible():
    idx = pd.date_range("2014-01-31", "2020-12-31", freq="ME")
    R = pd.DataFrame({0: .01, 1: 0.0}, index=idx)
    R.loc[R.index.year == 2020, 1] = .50  # would dominate only if the outer fold leaked
    cfg = _two_config_table()
    a = OT.tune_fold(R, cfg, "2020-01-01", n_trials=20, seed=7, inner_first_year=2016,
                     min_inner_folds=3, baseline_config=0)
    b = OT.tune_fold(R, cfg, "2020-01-01", n_trials=20, seed=7, inner_first_year=2016,
                     min_inner_folds=3, baseline_config=0)
    assert a["best_config"] == b["best_config"] == 0
    assert a["best_score"] == b["best_score"]
    assert a["training_max_date"] == "2019-12-31"
    assert all(t.user_attrs["max_training_date"] == "2019-12-31" for t in a["study"].trials)


def test_run_grid_uses_next_available_close_and_full_union_costs():
    dates = pd.bdate_range("2019-12-02", "2020-04-02")
    names = list("ABCDEF")
    adj = pd.DataFrame(100.0, index=dates, columns=names)
    adj.loc[adj.index >= "2020-03-02"] = 110.0
    signal_dates = pd.to_datetime(["2020-01-31", "2020-02-28", "2020-03-31"])
    scored = pd.DataFrame([{"date": d, "ticker": t, "eligible": True, "composite": i,
                            "segment": f"s{i // 2}"}
                           for d in signal_dates for i, t in enumerate(names)])
    grid = {"max_weight": [1 / 6], "lookback": [5], "n_hold": [6], "hysteresis": [1.0],
            "vol_target": [None]}
    R, cfg, G, T = PS.run_grid(scored, adj, grid=grid, methods=["equal"], min_days=2, verbose=False)
    assert list(G.index) == list(signal_dates[:-1])
    assert np.isclose(G.iloc[0, 0], .10)  # 2020-02-01/03 entry to 2020-03-02 exit
    assert np.isclose(T.iloc[0, 0], .50)  # fully invested from cash under the one-way convention
    assert np.isclose(R.iloc[0, 0], G.iloc[0, 0] - .002)  # 20 bp on 100% purchased notional


def test_newey_west_and_block_bootstrap_are_paired():
    idx = pd.date_range("2020-01-31", periods=36, freq="ME")
    a = pd.Series(np.linspace(.005, .02, len(idx)), index=idx)
    b = a - .001
    assert OT.newey_west_mean_t(a - b) > 0
    out = OT.block_bootstrap_sharpe_diff(a, b, n_boot=100, block=4, seed=1)
    assert out["n_months"] == 36 and out["sharpe_diff"] > 0
