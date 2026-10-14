"""S1/S2: canonical run manifest, cash-aware turnover, append-only run history."""
import json

import numpy as np
import pandas as pd
import pytest

from ai_semi_intel import runs
from ai_semi_intel.dashboard import monitoring


def _opt(tickers, seed=0, scale=0.5):
    rng = np.random.default_rng(seed)
    rp = pd.Series(rng.dirichlet(np.ones(len(tickers))), index=tickers)
    eq = pd.Series(1.0 / len(tickers), index=tickers)
    return {"recommended": {"weights": rp * scale, "cash": 1 - scale, "base": "risk_parity", "scale": scale, "exp_return": 0.1, "vol": 0.25, "sharpe": 0.2},
            "risk_parity": {"weights": rp}, "max_sharpe": {"weights": eq}, "min_vol": {"weights": rp}, "equal_weight": {"weights": eq}}


def _snapshot(tickers, opt, run_id, asof, gen):
    return {"date": asof, "asof_session": asof, "run_id": run_id, "portfolio_id": runs.PORTFOLIO_ID, "generated_at": gen, "supersedes": None,
            "cash": opt["recommended"]["cash"], "weights": opt["recommended"]["weights"].to_dict(), "weights_model": opt["risk_parity"]["weights"].to_dict(),
            "weights_max_sharpe": opt["max_sharpe"]["weights"].to_dict(), "top": list(tickers), "composite": {t: 1.0 for t in tickers},
            "upside": {t: 0.1 for t in tickers}, "rank": {t: i + 1 for i, t in enumerate(tickers)}, "price": {t: 100.0 for t in tickers}}


def test_run_manifest_reconciles_and_flags_price_gaps(tmp_path):
    tickers = ["A", "B", "C"]; opt = _opt(tickers)
    dates = pd.bdate_range("2026-06-01", periods=80)
    px = pd.DataFrame({t: 100.0 + np.arange(80) for t in tickers}, index=dates)
    manifest = runs.data_manifest(px, tickers, panel={}, data_cache=tmp_path)
    snap = _snapshot(tickers, opt, "r1", "2026-09-02", "2026-09-02T18:00:00")
    run = runs.build_run(snap, opt, manifest, runs.validation_summary(tmp_path), {"prev_run_id": None, "basis": "first_run"}, config_h="c", code_h="k")
    pf = run["portfolio"]
    assert pf["reconciles"] and abs(sum(p["target_executable_weight"] for p in pf["positions"]) + pf["cash"] - 1) < 1e-9
    assert all(p["current_live_weight"] is None for p in pf["positions"]) and run["validation_status"].startswith("NOT VALIDATED")
    assert pf["positions"][0]["target_model_weight"] > pf["positions"][0]["target_executable_weight"] and run["run_id"] == "r1"
    px2 = px.copy(); px2.iloc[-3, 1] = np.nan
    m2 = runs.data_manifest(px2, tickers, panel={}, data_cache=tmp_path)
    run2 = runs.build_run(snap, opt, m2, {}, {}, config_h="c", code_h="k")
    assert m2["price_gaps"] == {"B": [str(dates[-3].date())]} and run2["actionable"] is False
    assert any("price gap" in r for r in run2["not_actionable_reasons"]) and m2["entries"]["prices"]["status"] == "stale"


def test_compare_runs_turnover_includes_cash():
    cur = {"date": "2026-09-03", "run_id": "b", "top": ["A", "B"], "weights": {"A": 0.3, "B": 0.2}, "cash": 0.5, "rank": {"A": 1, "B": 2}}
    prev = {"date": "2026-09-02", "run_id": "a", "top": ["A", "C"], "weights": {"A": 0.4, "C": 0.2}, "cash": 0.4, "rank": {"A": 1, "C": 2}}
    ch = monitoring.compare_runs(cur, prev)
    assert ch["entries"] == ["B"] and ch["exits"] == ["C"] and ch["prev_run_id"] == "a" and ch["prev_date"] == "2026-09-02"
    assert np.isclose(ch["turnover"], 0.5 * (0.1 + 0.2 + 0.2 + 0.1)) and np.isclose(ch["cash_change"], 0.1)
    assert monitoring.compare_runs(cur, None)["turnover"] == 0.0


def test_history_is_append_only_and_prior_is_the_approved_run(tmp_path):
    latest, history = tmp_path / "latest", tmp_path / "history"
    latest.mkdir(); history.mkdir()
    tickers = ["A", "B", "C"]; opt = _opt(tickers)
    manifest = {"status": "fresh", "entries": {}, "price_gaps": {}, "stale": [], "hash": "h"}

    def make(run_id, asof, gen):
        snap = _snapshot(tickers, opt, run_id, asof, gen)
        (latest / "snapshot.json").write_text(json.dumps(snap)); (latest / "summary.json").write_text("{}")
        run = runs.build_run(snap, opt, manifest, {}, {}, config_h="c", code_h="k")
        (latest / "run.json").write_text(json.dumps(run))
        runs.register(run, latest, history)
        return run

    make("d1a", "2026-09-01", "2026-09-01T13:00:00")
    make("d1b", "2026-09-01", "2026-09-01T18:00:00")                      # same-session rerun supersedes d1a
    idx = runs.load_index(history); st = {r["run_id"]: r for r in idx}
    assert st["d1a"]["status"] == "superseded" and st["d1a"]["superseded_by"] == "d1b" and st["d1b"]["status"] == "pending"
    assert runs.latest_same_session(idx, "2026-09-01")["run_id"] == "d1b"
    assert runs.approved_prior(idx, "2026-09-02")["run_id"] == "d1b"     # last run of the earlier session
    make("d2a", "2026-09-02", "2026-09-02T18:00:00")
    idx = runs.load_index(history); st = {r["run_id"]: r for r in idx}
    assert st["d1b"]["status"] == "approved" and st["d2a"]["status"] == "pending" and len(idx) == 3
    assert all((history / "runs" / r / "snapshot.json").exists() for r in ("d1a", "d1b", "d2a"))
    assert runs.load_run_snapshot("d1a", history)["run_id"] == "d1a"     # the superseded run is still readable
    runs.approve("d1a", history)
    st = {r["run_id"]: r for r in runs.load_index(history)}
    assert st["d1a"]["status"] == "approved" and st["d1b"]["status"] == "superseded"
    p = history / "runs.csv"
    runs.append_rows(p, pd.DataFrame({"date": ["2026-09-01"], "ticker": ["A"], "weight": [0.1]}), "d1b")
    runs.append_rows(p, pd.DataFrame({"date": ["2026-09-01"], "ticker": ["A"], "weight": [0.2]}), "d1c")
    df = pd.read_csv(p)
    assert len(df) == 2 and list(df["run_id"]) == ["d1b", "d1c"]
    assert list(runs.latest_per_date(df, [{"run_id": "d1b", "status": "approved"}])["run_id"]) == ["d1b"]
    assert list(runs.latest_per_date(df, [])["run_id"]) == ["d1c"]


def test_runs_cli_list_and_approve(tmp_path, monkeypatch, capsys):
    latest, history = tmp_path / "latest", tmp_path / "history"
    latest.mkdir(); history.mkdir()
    monkeypatch.setattr(runs, "HISTORY", history); monkeypatch.setattr(runs, "LATEST", latest)
    tickers = ["A", "B"]; opt = _opt(tickers)
    for rid, gen in (("x1", "2026-09-01T13:00:00"), ("x2", "2026-09-01T18:00:00")):
        snap = _snapshot(tickers, opt, rid, "2026-09-01", gen)
        (latest / "snapshot.json").write_text(json.dumps(snap))
        run = runs.build_run(snap, opt, {"status": "fresh", "entries": {}, "price_gaps": {}, "stale": [], "hash": "h"}, {}, {}, config_h="c", code_h="k")
        (latest / "run.json").write_text(json.dumps(run))
        runs.register(run, latest, history)
    assert runs._cli(["list"]) == 0
    out = capsys.readouterr().out
    assert "x1" in out and "superseded" in out and "x2" in out
    assert runs._cli(["approve", "x1", "--note", "analyst prefers the 13:00 inputs"]) == 0
    st = {r["run_id"]: r for r in runs.load_index(history)}
    assert st["x1"]["status"] == "approved" and st["x2"]["status"] == "superseded" and st["x1"]["note"].startswith("analyst")


def test_run_archive_keeps_attempt_identity_and_its_own_refresh_evidence(tmp_path):
    latest, history = tmp_path / "latest", tmp_path / "history"
    latest.mkdir()
    opt = _opt(["A", "B"])
    snapshot = _snapshot(["A", "B"], opt, "evidence-run", "2026-09-22", "2026-09-22T07:20:00-04:00")
    snapshot["daily_attempt_id"] = "daily-attempt-1"
    run = runs.build_run(snapshot, opt, {"status": "fresh", "entries": {}}, {}, {}, config_h="c", code_h="k")
    assert run["daily_attempt_id"] == "daily-attempt-1"
    for name, data in {"run.json": run, "snapshot.json": snapshot,
                       "strategic_refresh.json": {"assessed": [{"ticker": "A"}], "dry_run": False},
                       "strategic_queue.json": {"B": ["new filing"]}}.items():
        (latest / name).write_text(json.dumps(data))
    frozen = runs.register(run, latest, history)
    old = (frozen / "strategic_refresh.json").read_bytes()
    (latest / "strategic_refresh.json").write_text(json.dumps({"assessed": [], "dry_run": True}))
    assert (frozen / "strategic_refresh.json").read_bytes() == old
    assert json.loads((frozen / "strategic_queue.json").read_text()) == {"B": ["new filing"]}


def test_index_interrupted_write_keeps_previous_index(tmp_path, monkeypatch):
    before = [{"run_id": "old", "asof_session": "2026-09-14", "generated_at": "2026-09-14T07:20:00-04:00"}]
    runs.save_index(before, tmp_path)
    raw = (tmp_path / "runs" / "index.json").read_bytes()
    def partial(value, path):
        path.write_text('{"partial":')
        raise OSError("interrupted write")
    monkeypatch.setattr(runs, "write_json", partial)
    with pytest.raises(OSError):
        runs.save_index(before + [{"run_id": "new", "asof_session": "2026-09-15", "generated_at": "2026-09-15T07:20:00-04:00"}], tmp_path)
    assert (tmp_path / "runs" / "index.json").read_bytes() == raw


def test_panel_interrupted_write_keeps_previous_rows(tmp_path, monkeypatch):
    path = tmp_path / "panel.csv"
    path.write_text("run_id,date,ticker\nold,2026-09-14,A\n")
    raw = path.read_bytes()
    def partial(self, destination, **kwargs):
        destination.write_text("partial")
        raise OSError("interrupted rows")
    monkeypatch.setattr(pd.DataFrame, "to_csv", partial)
    with pytest.raises(OSError):
        runs.append_rows(path, pd.DataFrame({"date": ["2026-09-15"], "ticker": ["B"]}), "new")
    assert path.read_bytes() == raw


def test_current_live_diagnostics_can_be_injected_without_stale_latest(tmp_path):
    path = tmp_path / "latest" / "live_validation.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"n_snapshots": 99, "status": "old"}))
    got = runs.validation_summary(tmp_path, live={"n_snapshots": 1, "status": "new"})
    assert got["live_snapshots"] == 1 and got["live_status"] == "new"
    assert runs.validation_summary(tmp_path, live={})["live_snapshots"] == 0


def test_row_batch_retry_rejects_changed_content_without_touching_history(tmp_path):
    path = tmp_path / "panel.csv"
    rows = pd.DataFrame({"date": ["2026-09-22"], "ticker": ["A"], "score": [0.3]})
    runs.append_rows(path, rows, "frozen-run")
    before = path.read_bytes()
    with pytest.raises(runs.DataUnavailable, match="immutable row batch conflict"):
        runs.append_rows(path, rows.assign(score=.4), "frozen-run")
    assert path.read_bytes() == before
    with pytest.raises(runs.DataUnavailable, match="different run identity"):
        runs.append_rows(path, rows.assign(run_id="someone-else"), "frozen-run")
    assert path.read_bytes() == before


def test_row_batch_retry_survives_other_run_adding_nullable_columns(tmp_path):
    path = tmp_path / "panel.csv"
    original = pd.DataFrame({"date": ["2026-09-14"], "ticker": ["A"], "rank": [1]})
    runs.append_rows(path, original, "first")
    runs.append_rows(path, pd.DataFrame({"date": ["2026-09-15"], "ticker": ["B"], "rank": [2], "new_field": [.5]}), "second")
    before = path.read_bytes()
    runs.append_rows(path, original, "first")
    assert path.read_bytes() == before
