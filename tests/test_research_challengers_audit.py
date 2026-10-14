"""Independent adversarial checks; synthetic scores have no return evidence."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

import pandas as pd
import pytest

from ai_semi_intel.analysis import strategic
from ai_semi_intel.config.settings import ScreenConfig
from ai_semi_intel.research import challengers as C
from ai_semi_intel.research.forward_registry import digest

CLOCK = "2026-09-22T15:00:00+00:00"
KNOWN = "2026-09-01T15:00:00+00:00"


def candidates(*, reviews=True, mode="prospective_candidate", future_field=None):
    cfg = ScreenConfig(top_n=1, max_per_segment=1)
    dims = {key: {"score": .75} for key in set(strategic.SCARCITY_WEIGHTS) | set(strategic.RISK_WEIGHTS)}
    evidence = [{"date": "2026-09-01", "claim": "synthetic independent audit", "source": "fixture"}]
    assessment = {"ticker": "AAA", "asof": "2026-09-01", "assessor": "fixture-assessor", "dimensions": dims,
                  "confidence": .8, "evidence": evidence,
                  "provenance": {"model": "fixture-model", "known_at": KNOWN, "prompts": {"system": "fixture"},
                                 "prompt_hash": digest({"system": "fixture"}), "evidence_hash": digest(evidence)}}
    text = "Synthetic source content with no market claim."
    review = {"assessment_hash": digest(assessment), "method": "independent_source_review", "independent_of_assessor": True,
              "reviewer": "fixture-reviewer", "reviewed_at": KNOWN, "items": [{"evidence_index": 0,
              "evidence_hash": digest(evidence[0]), "source_text": text, "source_sha256": sha256(text.encode()).hexdigest(),
              "source_known_at": KNOWN, "source_published_at": KNOWN, "supported_dimensions": sorted(dims), "status": "supported"}]}
    row = {"score_fund_momentum": 1., "score_price_momentum": 0., "score_value": 0., "score_scarcity": .8,
           "score_risk": .25, "score_market_resilience": 0., "score_financial_resilience": 0., "score_valuation_resilience": 0.,
           "score_strategic_resilience": 1., "scarcity_raw": .75, "llm_resilience": .75, "strategic_confidence": .8,
           "strategic_stale": False, "score_event": 0., "eligible": True, "risk_eligible": True, "risk_capped": False,
           "segment": "AI Compute Silicon", "composite": cfg.w_growth + cfg.w_scarcity * .8 + cfg.w_risk * .25}
    if future_field:
        row[future_field] = "2026-09-23T15:00:00+00:00"
    return C.build_daily_candidates(run_id="independent-audit", universe=pd.DataFrame([row], index=["AAA"]),
        assessments={"AAA": assessment}, knowledge_at=CLOCK, screen_config=cfg, previous_holdings=[],
        evidence_verifications={"AAA": review} if reviews else {}, mode=mode)


def sign(artifact):
    artifact["artifact_hash"] = digest({k: v for k, v in artifact.items() if k != "artifact_hash"})
    return artifact


@pytest.fixture
def real_registration_clock(monkeypatch):
    class Clock:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 23, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(C, "datetime", Clock)


@pytest.mark.parametrize("mutation", [
    lambda a: a["rows"][0].update(candidate_score=999.),
    lambda a: a["selection"].update(paired_candidate_selection=["NOT_IN_UNIVERSE"]),
    lambda a: a.update(n_paired_scores=999),
    lambda a: a.update(validation_status="VALIDATED", execution_status="LIVE"),
])
def test_registration_recomputes_frozen_sources_instead_of_accepting_resigned_derived_values(tmp_path, real_registration_clock, mutation):
    artifact = candidates()
    mutation(artifact)
    with pytest.raises(ValueError):
        C.register_candidates(tmp_path, sign(artifact))
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("field", ["known_at", "available_at", "published_at"])
def test_explicit_future_numeric_input_cannot_create_a_paired_comparison(field):
    artifact = candidates(future_field=field)
    assert artifact["n_paired_scores"] == 0
    assert artifact["selection"]["paired_candidate_selection"] == []
    assert artifact["rows"][0]["paired_eligible"] is False


def test_no_evidence_neutralization_is_not_registered_as_an_oos_observation(tmp_path, real_registration_clock):
    artifact = candidates(reviews=False)
    assert artifact["rows"][0]["status"] == "neutralization_diagnostic_only"
    record = C.register_candidates(tmp_path, artifact)
    assert record["oos_eligible"] is False


def test_stored_derived_row_cannot_change_even_if_local_record_hash_is_recomputed(tmp_path, real_registration_clock):
    artifact = candidates()
    record = C.register_candidates(tmp_path, artifact)
    path = next(tmp_path.rglob("*.json"))
    record["rows"][0]["candidate_score"] = 999.
    record["record_hash"] = digest({k: v for k, v in record.items() if k != "record_hash"})
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        C.register_candidates(tmp_path, artifact)


def test_real_registration_time_remains_later_than_knowledge_and_diagnostic_stays_out(tmp_path, real_registration_clock):
    artifact = candidates(mode="diagnostic_replay")
    record = C.register_candidates(tmp_path, artifact)
    assert record["effective_at"] == record["registered_at"] == "2026-09-23T15:00:00+00:00"
    assert record["oos_eligible"] is False and record["sample_type"] == "diagnostic_replay"
    assert C.register_candidates(tmp_path, deepcopy(artifact)) == record
