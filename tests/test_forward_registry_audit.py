"""Independent counterexamples for the prospective experiment; NOT VALIDATED.

Counterexamples remain strict regression tests after remediation.
Fixtures are synthetic and never write the real research archive.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

import numpy as np
import pandas as pd
import pytest

from ai_semi_intel.analysis import scoring, strategic
from ai_semi_intel.research import forward_registry as F, live_ic


def assessment(ticker, value, known="2026-09-04T19:00:00Z", evidence_date="2026-09-03"):
    evidence = [{"date": evidence_date, "source": "annual report", "claim": "Synthetic business fact"}]
    prompts = {"system": "Frozen scarcity rubric", "user": "Frozen public evidence"}
    return {"ticker": ticker, "asof": "2026-09-04", "version": 1,
            "dimensions": {name: {"score": value} for name in strategic.SCARCITY_WEIGHTS},
            "evidence": evidence, "provenance": {
                "known_at": known, "model": "audit-model", "prompts": prompts,
                "prompt_hash": F.digest(prompts), "prompt_template_hash": F.digest(prompts["system"]),
                "evidence_hash": F.digest(evidence), "source_availability_status": "unverified"}}


def register(root, run_id, when="2026-09-04T20:30:00Z", texts=None, n=3):
    tickers = list("ABCD")[:n]
    table = pd.DataFrame({"alpha_score": np.arange(n, dtype=float)}, index=tickers)
    assessments = {t: assessment(t, (i + 1) / (n + 1)) for i, t in enumerate(tickers)}
    words = texts or ["commodity", "normal business", "patent", "proprietary patent"][:n]
    return F.register_snapshot(root, run_id=run_id, decision_at=when, recorded_at=when,
                               universe=table, assessments=assessments,
                               contexts={t: {"business_summary": word} for t, word in zip(tickers, words)})


def test_hand_reconstruction_five_sessions_after_labor_day_and_after_close():
    # Friday Sep 4 after close -> Labor Day Monday is shut -> anchor Tue Sep 8.
    # Five later closes: Sep 9, 10, 11, 14, 15. 110 / 100 - 1 = 10%.
    days = pd.to_datetime(["2026-09-04", "2026-09-08", "2026-09-09", "2026-09-10",
                           "2026-09-11", "2026-09-14", "2026-09-15"])
    prices = pd.DataFrame({"A": [1, 100, 101, 104, 105, 108, 110]}, index=days)
    got = F.labels(prices, effective_at="2026-09-04T20:30:00Z", sessions=5,
                   acquired_at="2026-09-15T20:20:00Z", evaluated_at="2026-09-15T20:21:00Z")
    assert got["anchor_session"] == "2026-09-08" and got["end_session"] == "2026-09-15"
    assert got["returns"]["A"] == pytest.approx(.10)
    assert got["matures_at"] == "2026-09-15T20:20:00+00:00"


def test_raw_numeric_score_invariant_under_extreme_llm_perturbations(monkeypatch):
    n = 8
    index = [f"T{i}" for i in range(n)]
    data = pd.DataFrame({name: np.arange(1, n + 1, dtype=float) for name in scoring.ALL_FEATURES}, index=index)
    data = data.assign(segment="memory", market_cap=1e11, price=100., ev_sales_ntm=5., upside=.2,
                       gross_margin=.5, risk_flags="", strategic_confidence=1., strategic_stale=False,
                       scarcity_raw=0., llm_risk=0., risk_eligible=True)
    # Risk overlay is irrelevant to alpha_score; deterministic fixture prevents
    # unrelated optional risk columns from affecting the counterexample.
    monkeypatch.setattr(scoring, "score_risk", lambda d, pct: pd.DataFrame({
        "score_risk_resilience": np.zeros(len(d)), "risk_coverage": np.ones(len(d))}, index=d.index))
    low = scoring.score_universe(data)
    changed = data.assign(scarcity_raw=1., llm_risk=1., risk_eligible=False)
    high = scoring.score_universe(changed)
    pd.testing.assert_series_equal(low["alpha_score"], high["alpha_score"])
    assert not low["composite"].equals(high["composite"])
    assert low["eligible"].all() and not high["eligible"].any()


def test_explicit_future_evidence_cannot_be_ready():
    a = assessment("A", .8, evidence_date="2027-01-01")
    got = F._llm(a, pd.Timestamp("2026-09-04T20:30:00Z"))
    assert got["status"] != "ready"


def test_explicit_future_text_context_cannot_be_ready(tmp_path):
    rec = F.register_snapshot(tmp_path, run_id="future-text", decision_at="2026-09-04T20:30:00Z",
        recorded_at="2026-09-04T20:31:00Z", universe=pd.DataFrame({"alpha_score": [1.]}, index=["A"]),
        assessments={"A": assessment("A", .8)}, contexts={"A": {
            "business_summary": "sole supplier patent", "known_at": "2027-01-01T00:00:00Z"}})
    assert rec["rows"][0]["groups"]["fixed_text_rule"]["status"] != "ready"


def test_changed_stored_prompt_cannot_keep_ready_provenance():
    a = assessment("A", .8)
    a["provenance"]["prompts"]["user"] = "Different information obtained after the outcome"
    assert F._llm(a, pd.Timestamp("2026-09-04T20:30:00Z"))["status"] != "ready"


def test_legacy_diagnostic_cannot_use_future_acquisition_today(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 9, 4, 21, tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)
    monkeypatch.setattr(live_ic, "datetime", FrozenDatetime)
    prices = pd.DataFrame({"A": [100., 110.]}, index=pd.to_datetime(["2026-09-08", "2026-09-15"]))
    try:
        result = live_ic.forward_returns(prices, "2026-09-04", 5,
            generated_at="2026-09-04T20:30:00Z", acquired_at="2026-09-15T20:30:00Z")
    except ValueError:
        return
    assert result is None


def test_paired_summary_cannot_compare_different_dates(tmp_path):
    register(tmp_path, "first", texts=["ordinary business"] * 3)
    register(tmp_path, "second", when="2026-09-08T20:30:00Z")
    # Date 1: A<B<C returns. Date 2: A>B>C returns.
    prices = pd.DataFrame({"A": [100., 100., 100., 130.],
                           "B": [100., 100., 110., 120.],
                           "C": [100., 100., 120., 110.]},
                          index=pd.to_datetime(["2026-09-08", "2026-09-09", "2026-09-15", "2026-09-16"]))
    result = F.evaluate(tmp_path, prices, include_synthetic=True, acquired_at="2026-09-16T20:30:00Z",
                        evaluated_at="2026-09-16T20:31:00Z", min_names=3)
    week = next(g for g in result["summary"].values() if g["horizon"] == "1w")
    counts = {g["n_dates"] for g in week["ic"].values()}
    assert len(counts) == 1


def test_missing_potential_delisting_requires_partial_cohort_summary(tmp_path):
    register(tmp_path, "four", n=4)
    prices = pd.DataFrame({"A": [100., 90.], "B": [100., 100.], "C": [100., 110.], "D": [100., np.nan]},
                          index=pd.to_datetime(["2026-09-08", "2026-09-15"]))
    result = F.evaluate(tmp_path, prices, include_synthetic=True, acquired_at="2026-09-15T20:30:00Z",
                        evaluated_at="2026-09-15T20:31:00Z", min_names=3)
    week = next(g for g in result["summary"].values() if g["horizon"] == "1w")
    assert week["ic"]["numerical"]["mean_ic"] is None or week.get("cohort_status") == "partial_labels"


def test_internal_provenance_hash_disagreement_is_rejected(tmp_path):
    rec = register(tmp_path, "hashes")
    path = tmp_path / "registrations" / "hashes.json"
    changed = deepcopy(rec)
    changed["source_hash"] = "0" * 64
    changed["protocol_hash"] = "0" * 64
    changed["record_hash"] = F.digest({k: v for k, v in changed.items() if k != "record_hash"})
    path.write_bytes(F.canonical_bytes(changed))
    with pytest.raises(ValueError):
        F.load_record(path)


def test_readiness_summary_cannot_include_future_registered_records(tmp_path):
    register(tmp_path, "later")
    got = F.summary(tmp_path, include_synthetic=True, now="2026-09-03T20:30:00Z")
    assert got["n_registered_versions"] == 0
