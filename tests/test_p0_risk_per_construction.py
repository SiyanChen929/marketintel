"""Risk must cover EVERY construction, not just the canonical book.

Covers: full enumeration, per-construction independence, differing target weights producing different
available windows, fail-closed 3Y per construction, benchmark labelling, and independent recomputation.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from market_intel import risk_metrics as RM
from market_intel import risk_workbench as RW


# ----------------------------------------------------------------------------- enumeration
def test_every_construction_in_the_run_is_enumerated(tmp_path):
    cols = ["recommended", "equal_weight", "risk_parity", "min_variance", "max_sharpe"]
    w = pd.DataFrame({c: [0.2, 0.3, 0.5] for c in cols}, index=["AAA", "BBB", "CCC"])
    w["recommended"] = w["recommended"] * 0.5                      # vol-target scaled: holds cash
    path = tmp_path / "portfolio_weights.csv"
    w.to_csv(path)
    cons = RW.enumerate_constructions(path, run={"construction": "risk_parity", "cash": 0.5})
    assert set(cons) == set(cols), "a construction present in the run was not enumerated"
    assert cons["recommended"]["vol_target_scaled"] is True and cons["recommended"]["cash_weight"] == pytest.approx(0.5)
    assert cons["equal_weight"]["vol_target_scaled"] is False and cons["equal_weight"]["cash_weight"] == 0.0
    assert cons["risk_parity"]["is_run_primary"] is True and cons["recommended"]["is_canonical_executable"] is True
    for c in cons.values():
        assert c["source"].startswith("portfolio_weights.csv[")   # provenance of every weight vector


def test_enumeration_is_empty_and_fails_closed_without_a_weights_file(tmp_path):
    assert RW.enumerate_constructions(tmp_path / "missing.csv", run={}) == {}


# ----------------------------------------------------------------------------- independence
@pytest.fixture(scope="module")
def synthetic():
    """Two constructions whose target weights differ in WHICH names they hold, one of them short-listed."""
    rng = np.random.default_rng(4)
    d = pd.bdate_range("2024-01-02", periods=400)
    px = pd.DataFrame({c: 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.018, 400))) for c in ("AAA", "BBB", "CCC")}, index=d)
    px["SOXX"] = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.014, 400)))
    px.loc[px.index[:300], "CCC"] = np.nan                          # CCC lists late: only 100 sessions
    return px


def test_constructions_holding_different_names_get_different_available_windows(synthetic):
    px = synthetic
    long_only = {"AAA": 0.5, "BBB": 0.5}                            # both fully listed
    with_short = {"AAA": 0.4, "BBB": 0.3, "CCC": 0.3}               # includes the late lister
    cons = {"long_history": {"weights": long_only, "cash_weight": 0.0, "n_names": 2, "invested": 1.0,
                             "vol_target_scaled": False, "scale": 1.0, "is_canonical_executable": False,
                             "is_run_primary": False, "source": "test", "cash_basis": "fully invested",
                             "matches_run_cash": None, "weights_hash": RM.weights_hash(long_only),
                             "alias_of": None, "is_alias": False, "alias_basis": None},
            "short_history": {"weights": with_short, "cash_weight": 0.0, "n_names": 3, "invested": 1.0,
                              "vol_target_scaled": False, "scale": 1.0, "is_canonical_executable": False,
                              "is_run_primary": False, "source": "test", "cash_basis": "fully invested",
                              "matches_run_cash": None, "weights_hash": RM.weights_hash(with_short),
                              "alias_of": None, "is_alias": False, "alias_basis": None}}
    now = datetime(2026, 1, 2, 17, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    per = RW.per_construction_risk(px, cons, {"SOXX": 1.0}, {"custom": False, "source": "t"}, rf=0.0,
                                   windows={"1y": 252, "3y": 756}, bootstrap_reps=60, acquired_at=now, vol_target=0.25)
    a = per["constructions"]["long_history"]["layers"]["model"]["windows"]
    b = per["constructions"]["short_history"]["layers"]["model"]["windows"]
    # the construction that holds the late lister can support fewer sessions -> different availability
    assert a["1y"]["available"] is True
    assert b["1y"]["available"] is False, "a construction holding a short-listed name must not borrow another's window"
    assert "CCC" in b["1y"]["reason"] and "not shortened" in b["1y"]["reason"]
    assert b["1y"]["limiting_holding"] == "CCC"
    # neither may claim 3Y
    assert a["3y"]["available"] is False and b["3y"]["available"] is False
    assert a["3y"]["requested_sessions"] == 756
    assert a["3y"]["n_sessions_available"] < a["3y"]["requested_sessions"]


def test_data_hash_identifies_the_market_snapshot_and_weights_hash_the_book(synthetic):
    """Corrected contract: the data hash must depend ONLY on the market snapshot.

    The previous version asserted the opposite - that different weights must yield a different
    data_snapshot_hash - which conflated inputs with the portfolio applied to them and would make one market
    snapshot look like fifteen.
    """
    px = synthetic
    mk = lambda w: {"weights": w, "cash_weight": 0.0, "n_names": len(w), "invested": 1.0, "vol_target_scaled": False,
                    "scale": 1.0, "is_canonical_executable": False, "is_run_primary": False, "source": "test",
                    "cash_basis": "fully invested", "matches_run_cash": None, "weights_hash": RM.weights_hash(w),
                    "alias_of": None, "is_alias": False, "alias_basis": None}
    cons = {"c1": mk({"AAA": 1.0}), "c2": mk({"BBB": 1.0}), "c3": mk({"AAA": 0.5, "BBB": 0.5})}
    now = datetime(2026, 1, 2, 17, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    per = RW.per_construction_risk(px, cons, {"SOXX": 1.0}, {"custom": False, "source": "t"}, rf=0.0,
                                   windows={"1y": 252}, bootstrap_reps=60, acquired_at=now, vol_target=0.25)
    hashes, wh, vars_ = set(), set(), set()
    for name, blk in per["constructions"].items():
        lb = blk["layers"]["model"]
        prov = lb["provenance"]
        hashes.add(prov["data_snapshot_hash"])
        wh.add(prov["portfolio_weights_hash"])
        vars_.add(lb["windows"]["1y"]["horizons"]["1"]["overlapping"]["var_99"])
        assert "SHARED market panel" in prov["data_snapshot_basis"]
    assert len(hashes) == 1, "the same market snapshot produced different data hashes"
    assert len(wh) == 3, "each construction must carry its own portfolio_weights_hash"
    assert len(vars_) == 3, "different books produced identical VaR"
    assert len({blk["config_hash"] for blk in per["constructions"].values()}) == 1


def test_each_construction_carries_its_own_window_and_benchmark(synthetic):
    px = synthetic
    mk = lambda w: {"weights": w, "cash_weight": 0.0, "n_names": len(w), "invested": 1.0, "vol_target_scaled": False,
                    "scale": 1.0, "is_canonical_executable": False, "is_run_primary": False, "source": "test",
                    "cash_basis": "fully invested", "matches_run_cash": None, "weights_hash": RM.weights_hash(w),
                    "alias_of": None, "is_alias": False, "alias_basis": None}
    now = datetime(2026, 1, 2, 17, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    per = RW.per_construction_risk(px, {"c1": mk({"AAA": 1.0}), "c2": mk({"BBB": 1.0})}, {"SOXX": 1.0},
                                   {"custom": False, "source": "t"}, rf=0.0, windows={"1y": 252},
                                   bootstrap_reps=60, acquired_at=now, vol_target=0.25)
    for blk in per["constructions"].values():
        lb = blk["layers"]["model"]
        tes = lb["windows"]["1y"]["tracking_error"]
        assert tes and all(t.get("source") for t in tes if t.get("available"))
        assert all(t.get("kind") in {"formal mandate", "research proxy"} for t in tes)
        assert lb["no_preferred_estimate"] is True


def test_cash_and_vol_target_scale_change_the_loss_distribution(synthetic):
    px = synthetic
    base = {"AAA": 0.5, "BBB": 0.5}
    scaled = {"AAA": 0.25, "BBB": 0.25}                             # same book at half scale, half cash
    mk = lambda w, cash: {"weights": w, "cash_weight": cash, "n_names": len(w), "invested": sum(w.values()),
                          "vol_target_scaled": cash > 0, "scale": sum(w.values()), "is_canonical_executable": False,
                          "is_run_primary": False, "source": "test", "cash_basis": "x", "matches_run_cash": None,
                          "weights_hash": RM.weights_hash(w), "alias_of": None, "is_alias": False, "alias_basis": None}
    now = datetime(2026, 1, 2, 17, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    per = RW.per_construction_risk(px, {"full": mk(base, 0.0), "half": mk(scaled, 0.5)}, {"SOXX": 1.0},
                                   {"custom": False, "source": "t"}, rf=0.0, windows={"1y": 252},
                                   bootstrap_reps=60, acquired_at=now, vol_target=0.25)
    # the MODEL layer normalises both to fully invested, so they must now agree
    vf = per["constructions"]["full"]["layers"]["model"]["windows"]["1y"]["horizons"]["1"]["overlapping"]["var_99"]
    vh = per["constructions"]["half"]["layers"]["model"]["windows"]["1y"]["horizons"]["1"]["overlapping"]["var_99"]
    assert vh == pytest.approx(vf, rel=1e-6), "the model layer must be scale-invariant"
    # and the hypothetical common-vol layer must rescale each book by its OWN ex-ante vol
    ef = per["constructions"]["full"]["layers"]["vol_target_scenario"]
    eh = per["constructions"]["half"]["layers"]["vol_target_scenario"]
    assert ef["layer"]["scale"] == pytest.approx(eh["layer"]["scale"], rel=1e-6)   # same book -> same scale
    assert 0 < ef["layer"]["scale"] <= 1.0 and ef["layer"]["ex_ante_vol_ann"] > 0


# ----------------------------------------------------------------------------- independent recomputation
def test_per_construction_var_is_independently_reproducible(synthetic):
    px = synthetic
    w = {"AAA": 0.6, "BBB": 0.4}
    now = datetime(2026, 1, 2, 17, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    cons = {"c": {"weights": w, "cash_weight": 0.0, "n_names": 2, "invested": 1.0, "vol_target_scaled": False,
                  "scale": 1.0, "is_canonical_executable": False, "is_run_primary": False, "source": "t",
                  "cash_basis": "x", "matches_run_cash": None, "weights_hash": RM.weights_hash(w),
                  "alias_of": None, "is_alias": False, "alias_basis": None}}
    per = RW.per_construction_risk(px, cons, {"SOXX": 1.0}, {"custom": False, "source": "t"}, rf=0.0,
                                   windows={"1y": 252}, bootstrap_reps=60, acquired_at=now, vol_target=0.25)
    blk = per["constructions"]["c"]["layers"]["model"]["windows"]["1y"]
    o1 = blk["horizons"]["1"]["overlapping"]
    o10 = blk["horizons"]["10"]["overlapping"]
    # rebuild by hand, without touching risk_metrics
    sess, _ = RM.session_index(px, "SOXX", now)
    usable = sess[-252:]
    P = px[["AAA", "BBB"]].reindex(usable).to_numpy(float)
    wv = np.array([0.6, 0.4])
    man1 = np.array([((P[i + 1] / P[i] - 1) * wv).sum() for i in range(len(usable) - 1)])
    man10 = np.array([((P[i + 10] / P[i] - 1) * wv).sum() for i in range(len(usable) - 10)])
    assert o1["n_obs"] == len(man1) and o10["n_obs"] == len(man10)
    assert o1["var_99"] == pytest.approx(-np.quantile(man1, 0.01), abs=1e-6)
    assert o10["var_99"] == pytest.approx(-np.quantile(man10, 0.01), abs=1e-6)
    assert o1["es_975"] == pytest.approx(-man1[man1 <= np.quantile(man1, 0.025)].mean(), abs=1e-6)


def test_comparison_table_has_one_row_per_construction_with_its_own_window(synthetic):
    px = synthetic
    mk = lambda w: {"weights": w, "cash_weight": 0.0, "n_names": len(w), "invested": 1.0, "vol_target_scaled": False,
                    "scale": 1.0, "is_canonical_executable": False, "is_run_primary": False, "source": "t",
                    "cash_basis": "x", "matches_run_cash": None, "weights_hash": RM.weights_hash(w),
                    "alias_of": None, "is_alias": False, "alias_basis": None}
    now = datetime(2026, 1, 2, 17, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    cons = {"c1": mk({"AAA": 1.0}), "c2": mk({"BBB": 1.0}), "c3": mk({"AAA": 0.5, "CCC": 0.5})}
    per = RW.per_construction_risk(px, cons, {"SOXX": 1.0}, {"custom": False, "source": "t"}, rf=0.0,
                                   windows={"1y": 252, "3y": 756}, bootstrap_reps=60, acquired_at=now, vol_target=0.25)
    rows = RW.construction_comparison(per, "model")
    assert {r["construction"] for r in rows} == {"c1", "c2", "c3"}
    for r in rows:
        assert "1y_available" in r and "3y_available" in r and r["config_hash"]
        assert r["3y_available"] is False and r.get("3y_reason")
    c3 = next(r for r in rows if r["construction"] == "c3")
    assert c3["1y_available"] is False                              # holds the late lister
    c1 = next(r for r in rows if r["construction"] == "c1")
    assert c1["1y_available"] is True and c1["1y_n_obs_1d"] > 0


# ----------------------------------------------------------------------------- API surface
def test_api_exposes_every_construction_and_labels_the_benchmark(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.entities import Company, EntityIndex
    from market_intel.server import create_app
    from market_intel.store import Store
    monkeypatch.setenv("MARKET_INTEL_NO_LLM", "1")
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "tok")
    idx = EntityIndex([Company(ticker="MU", name="Micron", segment="Memory & Storage", aliases=[], weight=0.1)])
    c = TestClient(create_app(store=Store(tmp_path / "t.sqlite"), index=idx), client=("127.0.0.1", 50000))
    r = c.get("/api/research/risk/constructions?reps=0")
    assert r.status_code == 200
    d = r.json()
    if not d.get("available"):
        pytest.skip(d.get("reason", "no run"))
    assert d["n_constructions"] >= 1 and d["no_preferred_route"] is True
    b = d["benchmark"]
    assert b["label"] in ("mandate benchmark", "equal-weight research proxy") and "is_formal_mandate" in b and b["hash"]
    names = list(d["constructions"])
    assert len(d["comparison"]) == len(names)
    one = c.get(f"/api/research/risk/constructions/{names[0]}?reps=50&layer=model&window=1y")
    assert one.status_code == 200 and one.json()["construction"] == names[0]
    assert one.json()["comparison"]["construction"] == names[0]
    comp = one.json()["comparison"]
    if comp.get("1y_available"):
        assert comp["1y_ci_tier"] == "preview"
    else:
        assert comp.get("1y_reason"), "fail-closed unavailability must be explicit, never an empty row"
    assert set(one.json()["risk"]["layers"]) == {"model"}, "one-row CI endpoint must not compute every layer"
    assert c.get(f"/api/research/risk/constructions/{names[0]}?layer=bad").status_code == 422
    assert c.get("/api/research/risk/constructions/not_a_method").status_code == 404


def test_vol_target_scenario_fails_closed_without_a_construction_specific_scale(synthetic):
    """A fully-invested book must never be relabelled vol-targeted when no scale can be computed."""
    px = synthetic
    mk = lambda w: {"weights": w, "cash_weight": 0.0, "n_names": len(w), "invested": 1.0, "vol_target_scaled": False,
                    "scale": 1.0, "is_canonical_executable": False, "is_run_primary": False, "source": "t",
                    "cash_basis": "x", "matches_run_cash": None, "weights_hash": RM.weights_hash(w),
                    "alias_of": None, "is_alias": False, "alias_basis": None}
    now = datetime(2026, 1, 2, 17, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    per = RW.per_construction_risk(px, {"c": mk({"AAA": 0.5, "BBB": 0.5})}, {"SOXX": 1.0},
                                   {"custom": False, "source": "t"}, rf=0.0, windows={"1y": 252},
                                   bootstrap_reps=40, acquired_at=now, vol_target=None)      # run declares no target
    ex = per["constructions"]["c"]["layers"]["vol_target_scenario"]
    assert ex["available"] is False and "no volatility target" in ex["reason"]
    assert "NOT reported as a vol-target book" in ex["note"]
    assert per["constructions"]["c"]["layers"]["model"]["available"] is True          # model layer still stands
    rows = RW.construction_comparison(per, "vol_target_scenario")
    assert rows[0]["layer_available"] is False and rows[0]["layer_reason"]


def test_min_vol_is_flagged_as_an_alias_of_min_variance():
    cons = RW.enumerate_constructions()
    if "min_vol" not in cons or "min_variance" not in cons:
        pytest.skip("run does not contain both columns")
    assert cons["min_vol"]["is_alias"] is True and cons["min_vol"]["alias_of"] == "min_variance"
    assert cons["min_vol"]["weights_hash"] == cons["min_variance"]["weights_hash"]
    assert cons["min_variance"]["is_alias"] is False


def test_official_and_preview_bootstrap_tiers_are_distinguished(synthetic):
    px = synthetic
    mk = {"weights": {"AAA": 1.0}, "cash_weight": 0.0, "n_names": 1, "invested": 1.0, "vol_target_scaled": False,
          "scale": 1.0, "is_canonical_executable": False, "is_run_primary": False, "source": "t",
          "cash_basis": "x", "matches_run_cash": None, "weights_hash": "h", "alias_of": None,
          "is_alias": False, "alias_basis": None}
    now = datetime(2026, 1, 2, 17, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    prev = RW.per_construction_risk(px, {"c": mk}, {"SOXX": 1.0}, {"custom": False, "source": "t"}, rf=0.0,
                                    windows={"1y": 252}, bootstrap_reps=100, acquired_at=now, vol_target=0.25)
    assert prev["precision_tier"] == "preview" and "PREVIEW ONLY" in prev["precision_note"]
    assert prev["config"]["precision_tier"] == "preview"
    off = RW.per_construction_risk(px, {"c": mk}, {"SOXX": 1.0}, {"custom": False, "source": "t"}, rf=0.0,
                                   windows={"1y": 252}, bootstrap_reps=RW.OFFICIAL_BOOTSTRAP_REPS, acquired_at=now, vol_target=0.25)
    assert off["precision_tier"] == "official" and "PREVIEW ONLY" not in off["precision_note"]
    assert off["config_hash"] != prev["config_hash"]                 # precision is part of the config identity
    prev_row = RW.construction_comparison(prev, "model")[0]
    off_row = RW.construction_comparison(off, "model")[0]
    assert prev_row["1y_ci_tier"] == "preview"
    assert off_row["1y_ci_tier"] == "official"


def test_phase_counts_are_per_phase_not_the_overlapping_total(synthetic):
    px = synthetic
    mk = {"weights": {"AAA": 0.5, "BBB": 0.5}, "cash_weight": 0.0, "n_names": 2, "invested": 1.0,
          "vol_target_scaled": False, "scale": 1.0, "is_canonical_executable": False, "is_run_primary": False,
          "source": "t", "cash_basis": "x", "matches_run_cash": None, "weights_hash": "h", "alias_of": None,
          "is_alias": False, "alias_basis": None}
    now = datetime(2026, 1, 2, 17, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    per = RW.per_construction_risk(px, {"c": mk}, {"SOXX": 1.0}, {"custom": False, "source": "t"}, rf=0.0,
                                   windows={"1y": 252}, bootstrap_reps=40, acquired_at=now, vol_target=0.25)
    h10 = per["constructions"]["c"]["layers"]["model"]["windows"]["1y"]["horizons"]["10"]
    ph, ov = h10["non_overlapping_phases"], h10["overlapping"]
    assert len(ph["phases"]) == 10
    for p_ in ph["phases"]:
        assert 1 <= p_["n_windows"] <= 26, p_          # ~n_sessions/10, never the overlapping count
        assert p_["n_windows"] < ov["n_obs"] / 5
    lo, hi = ph["n_windows_per_phase_range"]
    assert hi - lo <= 1
    assert ph["n_windows_total_across_alternative_phases"] == sum(p_["n_windows"] for p_ in ph["phases"])
    # the heuristic count must not be presented as a statistical effective sample size
    assert "NOT a statistical effective sample size" in ov["non_overlap_equivalent_count_definition"]
    assert "effective_obs" not in ov
    assert "non_overlap_equivalent_count" in ov and ov["non_overlap_equivalent_count"] < ov["n_obs"]


def test_layer_equivalence_is_surfaced_not_hidden():
    """`recommended` is its primary construction rescaled: in the model layer they must coincide and say so."""
    cons = RW.enumerate_constructions()
    if not {"recommended", "risk_parity"} <= set(cons):
        pytest.skip("run lacks both columns")
    # published weights differ (one is vol-target scaled)
    assert cons["recommended"]["weights_hash"] != cons["risk_parity"]["weights_hash"]
    from market_intel import research
    px = research.price_panel()
    bench, bmeta = RW.benchmark_weights(research.universe())
    acquired_at, _ = RW.price_acquisition()
    assert acquired_at is not None
    per = RW.per_construction_risk(px, {k: cons[k] for k in ("recommended", "risk_parity")}, bench, bmeta,
                                   rf=0.0, windows={"1y": 252}, bootstrap_reps=50, vol_target=0.25,
                                   acquired_at=acquired_at)
    rows = RW.construction_comparison(per, "model")
    eq = [r for r in rows if r.get("layer_equivalent_to")]
    assert eq, "the model layer of the executable book must be flagged equivalent to its primary construction"
    assert eq[0]["layer_equivalent_to"] == "recommended" or eq[0]["layer_equivalent_to"] == "risk_parity"
    assert "scale this layer removes" in eq[0]["layer_equivalent_basis"]
