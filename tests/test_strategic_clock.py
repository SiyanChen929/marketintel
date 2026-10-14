"""Future source content cannot enter the production scoring path."""
import pandas as pd
import pytest

from ai_semi_intel.analysis import strategic as S


def assessment(**extra):
    return {"ticker": "A", "asof": "2026-09-22", "news_asof": "2026-09-22", "news_direction": .7,
            "strategic_score": .8, "confidence": .9,
            "dimensions": {k: {"score": .8} for k in S.DIMENSION_WEIGHTS}, "evidence": [], **extra}


@pytest.mark.parametrize("patch,reason", [({"asof": "2026-09-23"}, "future_asof"),
    ({"provenance": {"known_at": "2026-09-22T15:01:00Z"}}, "future_known_at"),
    ({"provenance": {"known_at": "2026-09-22"}}, "invalid_known_at"),
    ({"evidence": [{"date": "2027-01-01"}]}, "future_evidence_date")])
def test_future_assessments_do_not_reach_scarcity_risk_or_gate(monkeypatch, patch, reason):
    monkeypatch.setattr(S, "load_assessment", lambda ticker: assessment(**patch))
    monkeypatch.setattr(S, "load_reference_priors", lambda: pd.DataFrame())
    row = S.strategic_features(["A"], pd.Timestamp("2026-09-22"), knowledge_at="2026-09-22T15:00:00Z").loc["A"]
    assert pd.isna(row["scarcity_raw"]) and pd.isna(row["risk_score"])
    assert row["strategic_availability"] == "rejected" and row["strategic_rejection_reason"] == reason


def test_just_completed_same_day_assessment_uses_actual_knowledge_clock(monkeypatch):
    monkeypatch.setattr(S, "load_assessment", lambda ticker: assessment(provenance={"known_at": "2026-09-22T15:00:00Z"}))
    monkeypatch.setattr(S, "load_reference_priors", lambda: pd.DataFrame())
    row = S.strategic_features(["A"], pd.Timestamp("2026-09-22T14:59:00Z"), knowledge_at="2026-09-22T15:01:00Z").loc["A"]
    assert row["strategic_source"] == "evidence_assessment" and row["strategic_rejection_reason"] is None


def test_legacy_missing_known_at_stays_unverified_without_fabrication(monkeypatch):
    a = assessment()
    monkeypatch.setattr(S, "load_assessment", lambda ticker: a)
    monkeypatch.setattr(S, "load_reference_priors", lambda: pd.DataFrame())
    row = S.strategic_features(["A"], pd.Timestamp("2026-09-22"), knowledge_at="2026-09-22T15:00:00Z").loc["A"]
    assert row["strategic_availability"] == "legacy_unverified"
    assert "provenance" not in a


def test_future_reference_prior_is_excluded(monkeypatch):
    monkeypatch.setattr(S, "load_assessment", lambda ticker: None)
    monkeypatch.setattr(S, "load_reference_priors", lambda: pd.DataFrame({"asof": ["2027-01-01"], "strategic_score": [.9], "confidence": [.8], "source_ref": ["fixture"]}, index=["A"]))
    row = S.strategic_features(["A"], pd.Timestamp("2026-09-22"), knowledge_at="2026-09-22T15:00:00Z").loc["A"]
    assert pd.isna(row["scarcity_raw"]) and row["strategic_rejection_reason"] == "reference_prior:future_asof"


def test_future_news_never_amplifies_direction_and_nat_is_stale():
    assert S.decayed_news(assessment(news_asof="2026-10-01"), pd.Timestamp("2026-09-22")) == 0.
    assert S.decayed_news(assessment(news_direction=5.), pd.Timestamp("2026-09-22")) == 1.
    assert S.is_stale(assessment(asof="2026-09-23"), pd.Timestamp("2026-09-22"))
    assert S.is_stale(assessment(asof="NaT"), pd.Timestamp("2026-09-22"))


def test_frozen_assessment_inputs_are_reused_without_rereading_mutable_store(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(S, "load_assessment", lambda ticker: pytest.fail("mutable assessment reload"))
    monkeypatch.setattr(S, "load_all", lambda: pytest.fail("mutable snapshot reload"))
    monkeypatch.setattr(S, "load_reference_priors", lambda: pd.DataFrame())
    monkeypatch.setattr(S, "HISTORY", tmp_path)
    frozen = {"A": assessment()}
    row = S.strategic_features(["A"], pd.Timestamp("2026-09-22"), knowledge_at="2026-09-22T15:00:00Z", assessments=frozen).loc["A"]
    assert row["scarcity_raw"] == pytest.approx(.8)
    target = S.snapshot(pd.Timestamp("2026-09-22"), assessments=frozen)
    assert json.loads(target.read_text()) == frozen
    empty = S.strategic_features(["A"], pd.Timestamp("2026-09-22"), knowledge_at="2026-09-22T15:00:00Z", assessments={}).loc["A"]
    assert empty["strategic_source"] == "missing"
