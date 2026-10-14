"""Research challengers preserve production state and require bound evidence."""
import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ai_semi_intel.analysis import scoring, strategic
from ai_semi_intel.config.settings import ScreenConfig
from ai_semi_intel.research import challengers as C
from ai_semi_intel.research.forward_registry import digest

CLOCK = "2026-09-22T15:00:00Z"


def inputs(*, age=45, confidence=.8, quant_risk=.2, strategic_risk=1., stale=False):
    known = pd.Timestamp(CLOCK) - pd.Timedelta(days=age)
    evidence = [{"date": str(known.date()), "claim": "fixture fact", "source": "fixture"}]
    dims = {k: {"score": .75} for k in set(strategic.SCARCITY_WEIGHTS) | set(strategic.RISK_WEIGHTS)}
    a = {"ticker": "A", "asof": str(known.date()), "assessor": "assessor-x", "dimensions": dims,
         "confidence": confidence, "evidence": evidence,
         "provenance": {"model": "fixture-model", "known_at": known.isoformat(), "prompts": {"system": "fixture"},
                        "prompt_hash": digest({"system": "fixture"}), "evidence_hash": digest(evidence)}}
    text = "Independently inspected source fixture, not market evidence."
    r = {"assessment_hash": digest(a), "method": "independent_source_review", "independent_of_assessor": True,
         "reviewer": "reviewer-y", "reviewed_at": known.isoformat(), "items": [{"evidence_index": 0,
         "evidence_hash": digest(evidence[0]), "source_text": text, "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
         "source_known_at": known.isoformat(), "source_published_at": known.isoformat(),
         "supported_dimensions": sorted(dims), "status": "supported"}]}
    # q-risk components have total weight .75; set them equally to reconstruct quant_risk.
    row = {"score_fund_momentum": 1., "score_price_momentum": .5, "score_value": -.2,
           "score_scarcity": 0. if stale else confidence, "score_risk": np.clip(quant_risk + .25 * strategic_risk, -2, 2),
           "score_market_resilience": quant_risk / .75, "score_financial_resilience": quant_risk / .75,
           "score_valuation_resilience": quant_risk / .75, "score_strategic_resilience": strategic_risk,
           "scarcity_raw": .75, "llm_resilience": .75, "strategic_confidence": confidence,
           "strategic_stale": stale, "score_event": 0., "eligible": True, "risk_eligible": True,
           "risk_capped": False, "segment": "AI Compute Silicon"}
    cfg = ScreenConfig(top_n=2, max_per_segment=2)
    row["composite"] = cfg.w_growth * row["score_fund_momentum"] + cfg.w_momentum * row["score_price_momentum"] + cfg.w_value * row["score_value"] + cfg.w_scarcity * row["score_scarcity"] + cfg.w_risk * row["score_risk"]
    return pd.DataFrame([row], index=["A"]), {"A": a}, {"A": r}, cfg


def build(universe, assessments, reviews, cfg, **kw):
    return C.build_daily_candidates(run_id="fixture", universe=universe, assessments=assessments,
                                    evidence_verifications=reviews, screen_config=cfg,
                                    knowledge_at=CLOCK, previous_holdings=[], **kw)


def test_e2_changes_only_two_contributions_with_exact_paired_formula():
    u, a, review, cfg = inputs()
    artifact = build(u, a, review, cfg)
    row = artifact["rows"][0]
    assert row["paired_eligible"] and row["status"] == "paired_score_available"
    assert row["candidate_score"] == pytest.approx(row["baseline_score"] - .15 * .4 - .1 * .25 * .6)
    e = row["explanation"]
    assert e["c_scarcity"] == pytest.approx(.4) and e["c_risk"] == pytest.approx(.4)
    assert e["quant_risk_unchanged"] == pytest.approx(.2)
    assert artifact["oos_eligible"] is False and artifact["execution_status"] == "NO_ORDERS"


def test_clipping_is_reapplied_not_subtracted_from_clipped_total():
    u, a, review, cfg = inputs(quant_risk=1.9, strategic_risk=2.)
    row = build(u, a, review, cfg)["rows"][0]
    assert row["explanation"]["old_risk"] == 2.
    assert row["explanation"]["new_risk"] == 2.
    assert row["explanation"]["risk_decision_delta"] == 0.


@pytest.mark.parametrize("age,confidence,expected", [(0, 1., .9), (45, .8, .4), (90, .8, .2), (91, .8, 0.)])
def test_fixed_cap_half_life_and_cutoff(age, confidence, expected):
    u, a, reviews, cfg = inputs(age=age, confidence=confidence, stale=age > 90)
    row = build(u, a, reviews, cfg)["rows"][0]
    assert row["explanation"]["c_scarcity"] == pytest.approx(expected)
    assert row["paired_eligible"]  # An observed stale assessment tests explicit removal.


def test_absent_review_is_neutralization_only_and_keeps_risk_gate():
    u, a, _, cfg = inputs()
    u.loc["A", "eligible"] = False
    u.loc["A", "risk_eligible"] = False
    row = build(u, a, {}, cfg)["rows"][0]
    assert row["status"] == "neutralization_diagnostic_only" and not row["paired_eligible"]
    assert row["candidate_score"] is not None
    assert row["risk_eligible_unchanged"] is False
    assert row["explanation"]["new_risk"] == pytest.approx(.2)


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r.update(assessment_hash="a" * 64), "review_assessment_hash_mismatch"),
    (lambda r: r["items"][0].update(source_sha256="b" * 64), "source_hash_mismatch"),
    (lambda r: r["items"][0].pop("source_text"), "source_bytes_missing"),
    (lambda r: r.update(reviewed_at="2026-09-22T15:01:00Z"), "review_known_after_decision"),
    (lambda r: r.update(independent_of_assessor=False), "independent_review_declaration_missing"),
    (lambda r: r.update(reviewer="assessor-x"), "independent_reviewer_missing"),
])
def test_false_or_unbound_review_never_makes_paired_sample(mutate, reason):
    u, a, reviews, cfg = inputs()
    mutate(reviews["A"])
    row = build(u, a, reviews, cfg)["rows"][0]
    assert not row["paired_eligible"] and row["explanation"]["c_risk"] == 0.
    assert any(reason in value for value in row["missing_fields"])


def test_each_rubric_has_its_own_weighted_coverage_without_missing_dimension_renormalization():
    u, a, reviews, cfg = inputs()
    reviews["A"]["items"][0]["supported_dimensions"] = ["scarcity", "competitive_threat"]
    row = build(u, a, reviews, cfg)["rows"][0]
    assert row["explanation"]["q_scarcity"] == pytest.approx(.4)
    assert row["explanation"]["q_risk"] == pytest.approx(.35)
    assert row["explanation"]["c_scarcity"] == pytest.approx(.16)
    assert row["explanation"]["c_risk"] == pytest.approx(.14)


@pytest.mark.parametrize("change,reason", [("missing", "score_risk"), ("baseline", "production_baseline_reconstruction_mismatch"),
                                           ("source", "assessment_frozen_feature_mismatch")])
def test_missing_or_inconsistent_baseline_unavailable_not_approximate(change, reason):
    u, a, reviews, cfg = inputs()
    if change == "missing":
        u = u.drop(columns="score_risk")
    elif change == "baseline":
        u.loc["A", "composite"] += 1
    else:
        a["A"]["dimensions"]["scarcity"]["score"] = .1
    row = build(u, a, reviews, cfg)["rows"][0]
    assert row["status"] == "unavailable" and row["candidate_score"] is None
    assert reason in row["missing_fields"]


def test_future_or_provenance_missing_assessment_is_never_a_paired_sample():
    u, a, reviews, cfg = inputs()
    a["A"]["asof"] = "2026-09-23"
    reviews["A"]["assessment_hash"] = digest(a["A"])
    row = build(u, a, reviews, cfg)["rows"][0]
    assert not row["paired_eligible"] and row["explanation"]["c_scarcity"] == 0.
    assert "future_asof" in row["missing_fields"]
    a["A"]["asof"] = "2026-09-22"
    a["A"]["provenance"].pop("model")
    reviews["A"]["assessment_hash"] = digest(a["A"])
    row = build(u, a, reviews, cfg)["rows"][0]
    assert not row["paired_eligible"] and "assessment_provenance:model" in row["missing_fields"]


def test_pure_frozen_function_does_not_read_config_cache_or_files_and_does_not_mutate(monkeypatch):
    u, a, reviews, cfg = inputs()
    before = digest({"u": u.to_dict(), "a": a, "r": reviews})
    monkeypatch.setattr(strategic, "load_all", lambda: pytest.fail("cache read"))
    monkeypatch.setattr(strategic, "load_assessment", lambda *_: pytest.fail("cache read"))
    monkeypatch.setattr(scoring, "CONFIG", None)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("filesystem read"))
    monkeypatch.setattr(Path, "read_text", lambda *_: pytest.fail("filesystem read"))
    first, second = build(u, a, reviews, cfg), build(u, a, reviews, cfg)
    assert first == second
    assert before == digest({"u": u.to_dict(), "a": a, "r": reviews})


def test_matched_selection_excludes_missing_evidence_from_both_branches():
    u, a, reviews, cfg = inputs()
    u = pd.concat([u, u.rename(index={"A": "B"})])
    a["B"] = copy.deepcopy(a["A"])
    a["B"]["ticker"] = "B"
    artifact = build(u, a, reviews, cfg)
    selection = artifact["selection"]
    assert set(selection["production_selection"]) == {"A", "B"}
    assert selection["paired_baseline_selection"] == selection["paired_candidate_selection"] == ["A"]
    assert not selection["full_universe_comparison_available"]
    assert artifact["n_paired_scores"] == 1


def test_assessment_security_identity_cannot_be_relabelled_by_dictionary_key():
    u, a, reviews, cfg = inputs()
    a["A"]["ticker"] = "OTHER"
    reviews["A"]["assessment_hash"] = digest(a["A"])
    row = build(u, a, reviews, cfg)["rows"][0]
    assert row["status"] == "unavailable" and row["candidate_score"] is None
    assert "assessment_security_identity_mismatch" in row["missing_fields"]


def test_hysteresis_uses_explicit_previous_holdings(monkeypatch):
    u, a, reviews, cfg = inputs()
    calls = []
    original = scoring.select_top
    def check(df, passed_cfg, prev):
        calls.append(prev)
        assert passed_cfg.hysteresis == 1.5
        return original(df, passed_cfg, prev)
    monkeypatch.setattr(scoring, "select_top", check)
    cfg.hysteresis = 1.5
    C.build_daily_candidates(run_id="fixture", universe=u, assessments=a, knowledge_at=CLOCK,
                            screen_config=cfg, previous_holdings=["A"], evidence_verifications=reviews)
    assert calls == [["A"], ["A"], ["A"]]
    with pytest.raises(ValueError, match="previous_holdings"):
        C.build_daily_candidates(run_id="fixture", universe=u, assessments=a, knowledge_at=CLOCK,
                                screen_config=cfg, previous_holdings=None)


@pytest.mark.parametrize("top_n,previous", [(2, ["A", "B"]), (1, ["A", "B", "C"])])
def test_production_hysteresis_never_exceeds_explicit_selection_size(top_n, previous):
    universe = pd.DataFrame({"composite": [3., 2., 1.], "eligible": [True, True, True],
                             "segment": ["AI Compute Silicon"] * 3}, index=["A", "B", "C"])
    cfg = ScreenConfig(top_n=top_n, max_per_segment=3, hysteresis=4.)
    selected = scoring.select_top(universe, cfg, prev=previous)
    assert list(selected.index) == previous[:top_n]


def test_registration_uses_real_clock_not_past_knowledge_and_retries_are_immutable(tmp_path, monkeypatch):
    u, a, reviews, cfg = inputs()
    artifact = build(u, a, reviews, cfg, mode="prospective_candidate")
    class FixedClock:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 23, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(C, "datetime", FixedClock)
    registered = C.register_candidates(tmp_path, artifact)
    assert registered["effective_at"] == "2026-09-23T14:00:00+00:00"
    assert registered["oos_eligible"] and registered["sample_type"] == "observed_forward_candidate"
    assert C.register_candidates(tmp_path, artifact) == registered
    artifact["rows"][0]["candidate_score"] += 1
    with pytest.raises(ValueError, match="artifact hash"):
        C.register_candidates(tmp_path, artifact)


def test_diagnostic_replay_stays_excluded_when_written_today(tmp_path, monkeypatch):
    u, a, reviews, cfg = inputs()
    artifact = build(u, a, reviews, cfg)
    class FixedClock:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 23, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(C, "datetime", FixedClock)
    record = C.register_candidates(tmp_path, artifact)
    assert record["sample_type"] == "diagnostic_replay" and not record["oos_eligible"]


def test_changed_frozen_config_changes_experiment_identity():
    u, a, reviews, cfg = inputs()
    first = build(u, a, reviews, cfg)
    cfg.hysteresis = 1.5
    second = build(u, a, reviews, cfg)
    assert first["experiment_hash"] != second["experiment_hash"]


def test_read_only_inspection_exposes_evidence_gap_and_clock_run_identity(tmp_path, monkeypatch):
    u, a, _, cfg = inputs()
    artifact = build(u, a, {}, cfg, mode="prospective_candidate")
    class FixedClock:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 23, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(C, "datetime", FixedClock)
    record = C.register_candidates(tmp_path, artifact)
    path = tmp_path / record["protocol"]["id"] / "fixture.json"
    original = path.read_bytes()
    result = C.inspect_registration(path, now="2026-09-23T15:00:00Z", current_run_id="fixture")
    assert result["status"] == "evidence_incomplete" and result["n_rows"] == 1
    assert result["n_paired_scores"] == 0 and not result["oos_scope_eligible"]
    assert result["diagnostic_counts"] == {"neutralization_diagnostic_only": 1}
    assert result["validation_status"] == "NOT VALIDATED" and result["selection"]["paired_candidate_selection"] == []
    assert C.inspect_registration(path, now="2026-09-23T15:00:00Z", current_run_id="other")["status"] == "stale_run"
    assert C.inspect_registration(path, now="2026-09-22T15:00:00Z", current_run_id="fixture")["status"] == "integrity_error"
    assert path.read_bytes() == original


def test_inspection_handles_missing_record_without_creating_paths(tmp_path):
    missing = tmp_path / "missing" / "no-file.json"
    result = C.inspect_registration(missing, now=CLOCK, current_run_id="fixture")
    assert result["status"] == "not_started" and result["n_paired_scores"] == 0
    assert not missing.parent.exists()


def test_reconstruction_preserves_original_universe_order_for_tie_and_row_identity(tmp_path, monkeypatch):
    u, a, reviews, cfg = inputs()
    u = pd.concat([u.rename(index={"A": "Z"}), u])
    a["Z"] = copy.deepcopy(a["A"])
    a["Z"]["ticker"] = "Z"
    artifact = build(u, a, reviews, cfg)
    class FixedClock:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 23, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(C, "datetime", FixedClock)
    record = C.register_candidates(tmp_path, artifact)
    assert record["sources"]["universe_order"] == ["Z", "A"]
    assert [row["ticker"] for row in record["rows"]] == ["Z", "A"]
