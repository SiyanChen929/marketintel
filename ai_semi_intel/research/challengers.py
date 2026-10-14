"""Frozen-input research challengers. These functions never read the data cache.

E2 replaces only the scarcity and strategic-risk contributions to an archived
production score. It does not modify eligibility, risk gates, capital weights,
or live targets. Reviewer attestations bind source bytes; hashes do not establish
that the reviewer or the source is correct.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from ..analysis import scoring, strategic
from ..trading_policy import aware
from .forward_registry import _llm, canonical_bytes, digest, write_once

PROTOCOL = {
    "id": "E2_EVIDENCE_AND_AGE_SHRINKAGE_V2",
    "half_life_days": 45, "cutoff_days": 90, "confidence_cap": .9,
    "age": "elapsed days since assessment provenance.known_at, evaluated at frozen knowledge_at",
    "scarcity_weights": dict(strategic.SCARCITY_WEIGHTS),
    "strategic_risk_weights": dict(strategic.RISK_WEIGHTS),
    "risk_pillar_weights": {"market_resilience": .26, "financial_resilience": .26,
                            "valuation_resilience": .23, "strategic_resilience": .25},
    "scarcity": "S_new = q_scarcity * min(confidence,.9) * 2**(-age/45) * clip((scarcity_raw-.5)/.25,-2,2)",
    "risk": "R_new = clip(R_quant + .25*q_risk*min(confidence,.9)*2**(-age/45)*score_strategic_resilience,-2,2)",
    "decision": "D_new = D_production + w_scarcity*(S_new-S_production) + w_risk*(R_new-R_production)",
    "quality": "q is rubric-weight coverage of dimensions with independent-review attestations bound to source bytes; separate scarcity and risk coverage",
    "zero_rule": "age>90 or future/unverified source gives zero confidence; absent verification is neutralization-only, excluded from paired eligibility",
    "gate_policy": "frozen production eligible and risk gates unchanged in both branches",
    "selection": "same production select_top, explicit previous_holdings and frozen screen_config; matched available-name comparison labelled separately",
    "primary_horizon_sessions": 21, "secondary_horizons_sessions": [5, 63],
    "validation_status": "NOT VALIDATED", "inference_status": "NO_RETURN_EVALUATION",
    "version_note": "V2 clarifies separate rubric coverage and actual-known-at age before activation; V1 was an unactivated design",
}
_REQUIRED_CONFIG = ("w_growth", "w_momentum", "w_value", "w_scarcity", "w_risk",
                    "w_event", "top_n", "max_per_segment", "hysteresis")
_SCORE_FIELDS = ("composite", "score_fund_momentum", "score_price_momentum", "score_value", "score_scarcity",
                 "score_risk", "score_market_resilience", "score_financial_resilience", "score_valuation_resilience",
                 "score_strategic_resilience", "scarcity_raw", "llm_resilience", "strategic_confidence", "score_event")
# Freeze implementation identities at module import. Candidate construction is
# pure: it performs no filesystem, cache, provider or current-settings reads.
_CODE_IDENTITIES = {
    "challenger_implementation_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "selection_implementation_hash": hashlib.sha256(Path(scoring.__file__).read_bytes()).hexdigest(),
    "strategic_implementation_hash": hashlib.sha256(Path(strategic.__file__).read_bytes()).hexdigest(),
}


def _finite(value):
    try:
        return float(value) if math.isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def _same(a, b):
    return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-8)


def _frozen_config(config):
    value = asdict(config) if is_dataclass(config) else copy.deepcopy(dict(config))
    missing = [key for key in _REQUIRED_CONFIG if key not in value]
    if missing:
        raise ValueError("frozen screen_config missing: " + ", ".join(missing))
    for key in _REQUIRED_CONFIG:
        if _finite(value[key]) is None:
            raise ValueError("nonfinite screen_config: " + key)
    if any(value[key] < 0 for key in ("w_growth", "w_momentum", "w_value", "w_scarcity", "w_risk", "w_event")):
        raise ValueError("negative decision weight")
    if not math.isclose(sum(value[key] for key in ("w_growth", "w_momentum", "w_value", "w_scarcity", "w_risk")), 1.):
        raise ValueError("decision weights must sum to one")
    for key in ("top_n", "max_per_segment"):
        if isinstance(value[key], bool) or not isinstance(value[key], int) or value[key] <= 0:
            raise ValueError(key + " must be a positive integer")
    if value["hysteresis"] < 1:
        raise ValueError("hysteresis must be at least one")
    return value


def _review_coverage(assessment, review, clock):
    """Validate the scope of a caller-supplied independent-review attestation.

    Each accepted item needs an evidence index/hash, exact nonempty source text
    and its UTF-8 SHA256, aware source/publication clocks and supported dimensions.
    This is integrity and chronology validation, not automatic factual verification.
    """
    issues, accepted, covered = [], [], set()
    if not review:
        return 0., 0., [], ["independent_evidence_review_missing"]
    if review.get("assessment_hash") != digest(assessment):
        issues.append("review_assessment_hash_mismatch")
    if review.get("method") != "independent_source_review" or review.get("independent_of_assessor") is not True:
        issues.append("independent_review_declaration_missing")
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip() or reviewer == assessment.get("assessor"):
        issues.append("independent_reviewer_missing")
    try:
        reviewed_at = aware(review.get("reviewed_at"))
        if reviewed_at > clock:
            issues.append("review_known_after_decision")
    except (ValueError, TypeError, RuntimeError):
        reviewed_at = None
        issues.append("review_timestamp_invalid")
    if issues:
        return 0., 0., [], issues
    evidence = assessment.get("evidence") or []
    valid_dimensions = set(PROTOCOL["scarcity_weights"]) | set(PROTOCOL["strategic_risk_weights"])
    for index, item in enumerate(review.get("items") or []):
        reason = None
        if not isinstance(item, dict):
            issues.append(f"review_item_{index}:invalid_item")
            continue
        ev_index = item.get("evidence_index")
        if isinstance(ev_index, bool) or not isinstance(ev_index, int) or not 0 <= ev_index < len(evidence):
            reason = "invalid_evidence_index"
        elif item.get("evidence_hash") != digest(evidence[ev_index]):
            reason = "evidence_hash_mismatch"
        payload = item.get("source_text")
        if not isinstance(payload, str) or not payload.strip():
            reason = "source_bytes_missing"
        elif item.get("source_sha256") != hashlib.sha256(payload.encode()).hexdigest():
            reason = "source_hash_mismatch"
        try:
            known, published = aware(item.get("source_known_at")), aware(item.get("source_published_at"))
            if not published <= known <= reviewed_at <= clock:
                reason = "source_review_clock_inconsistent"
        except (ValueError, TypeError, RuntimeError):
            reason = "source_clock_invalid"
        dims = item.get("supported_dimensions")
        if not isinstance(dims, list) or not dims or any(not isinstance(dim, str) for dim in dims) or not set(dims) <= valid_dimensions or item.get("status") != "supported":
            reason = "supported_dimensions_missing"
        elif not set(dims) <= set(assessment.get("dimensions") or {}):
            reason = "review_dimension_missing_assessment_score"
        if reason:
            issues.append(f"review_item_{index}:{reason}")
            continue
        covered.update(dims)
        accepted.append({"evidence_index": ev_index, "source_sha256": item["source_sha256"], "supported_dimensions": sorted(set(dims))})
    # The production risk rubric renormalizes available dimensions. Coverage is
    # instead measured against the complete rubric, so absent dimensions cannot
    # improve evidence coverage.
    qs = sum(w for dim, w in PROTOCOL["scarcity_weights"].items() if dim in covered)
    qr = sum(w for dim, w in PROTOCOL["strategic_risk_weights"].items() if dim in covered)
    if not accepted:
        issues.append("no_supported_evidence_dimensions")
    return qs, qr, accepted, issues


def _one(ticker, row, assessment, review, clock, cfg):
    baseline = _finite(row.get("composite"))
    result = {"baseline_score": baseline, "candidate_score": None, "paired_eligible": False,
              "status": "unavailable", "missing_fields": [], "explanation": {},
              "assessment_hash": digest(assessment) if assessment else None,
              "review_hash": digest(review) if review else None}
    missing = [field for field in _SCORE_FIELDS if _finite(row.get(field)) is None]
    for field in ("known_at", "available_at", "published_at"):
        if row.get(field) is not None:
            try:
                if aware(row[field]) > clock:
                    missing.append("future_numerical_" + field)
            except (ValueError, TypeError, RuntimeError):
                missing.append("invalid_numerical_" + field)
    if not isinstance(row.get("eligible"), (bool, np.bool_)):
        missing.append("eligible")
    if not isinstance(row.get("strategic_stale"), (bool, np.bool_)):
        missing.append("strategic_stale")
    if not isinstance(row.get("segment"), str) or row["segment"] not in scoring.SEGMENT_ORDER:
        missing.append("segment")
    if not isinstance(assessment, dict) or not assessment:
        missing.append("assessment")
    elif assessment.get("ticker") != ticker:
        missing.append("assessment_security_identity_mismatch")
    if missing:
        result["missing_fields"] = missing
        return result
    values = {field: float(row[field]) for field in _SCORE_FIELDS}
    dims = assessment.get("dimensions") or {}
    try:
        raw_scarcity = strategic.scarcity_strength(dims)
        resilience = 1 - strategic.llm_risk(dims)
        if any(_finite((dims.get(k) or {}).get("score")) is None for k in PROTOCOL["scarcity_weights"]):
            raise ValueError("missing scarcity dimension")
        if any(not 0 <= float(v["score"]) <= 1 for v in dims.values()):
            raise ValueError("dimension outside rubric")
    except (ValueError, TypeError, KeyError, AttributeError):
        result["missing_fields"] = ["assessment_dimensions_invalid"]
        return result
    source_confidence = _finite(assessment.get("confidence"))
    if source_confidence is None or not 0 <= source_confidence <= 1:
        result["missing_fields"] = ["assessment_confidence_invalid"]
        return result
    if not (_same(raw_scarcity, values["scarcity_raw"]) and _same(resilience, values["llm_resilience"])
            and _same(source_confidence, values["strategic_confidence"])):
        result["missing_fields"] = ["assessment_frozen_feature_mismatch"]
        return result
    base_scarcity = float(np.clip((raw_scarcity - .5) / .25, -2, 2))
    old_scarcity = base_scarcity * (0. if row["strategic_stale"] else source_confidence)
    quant_risk = sum(PROTOCOL["risk_pillar_weights"][name] * values["score_" + name]
                     for name in ("market_resilience", "financial_resilience", "valuation_resilience"))
    old_risk = float(np.clip(quant_risk + .25 * values["score_strategic_resilience"], -2, 2))
    reproduced = (cfg["w_growth"] * values["score_fund_momentum"] + cfg["w_momentum"] * values["score_price_momentum"]
                  + cfg["w_value"] * values["score_value"] + cfg["w_scarcity"] * values["score_scarcity"]
                  + cfg["w_risk"] * values["score_risk"] + cfg["w_event"] * values["score_event"])
    if not (_same(old_scarcity, values["score_scarcity"]) and _same(old_risk, values["score_risk"]) and _same(reproduced, baseline)):
        result["missing_fields"] = ["production_baseline_reconstruction_mismatch"]
        return result
    qs, qr, accepted, issues = _review_coverage(assessment, review, clock)
    availability_issue = strategic._availability_issue(assessment, clock)
    provenance = _llm(assessment, clock)
    if provenance["status"] != "ready":
        issues.extend("assessment_provenance:" + value for value in provenance["missing_fields"])
    age = None
    try:
        known = aware((assessment.get("provenance") or {}).get("known_at"))
        age = (clock - known).total_seconds() / 86400
        if age < 0:
            issues.append("assessment_known_after_decision")
    except (ValueError, TypeError, RuntimeError):
        issues.append("assessment_known_at_unavailable")
    if availability_issue:
        issues.append(availability_issue)
    freshness = 2 ** (-age / 45) if age is not None and 0 <= age <= 90 and not availability_issue and provenance["status"] == "ready" else 0.
    cs, cr = qs * min(source_confidence, .9) * freshness, qr * min(source_confidence, .9) * freshness
    new_scarcity = cs * base_scarcity
    new_risk = float(np.clip(quant_risk + .25 * cr * values["score_strategic_resilience"], -2, 2))
    scarcity_delta = cfg["w_scarcity"] * (new_scarcity - old_scarcity)
    risk_delta = cfg["w_risk"] * (new_risk - old_risk)
    # Absence of source verification is inspectable as neutralization, never an
    # eligible paired treatment observation. The hard gates remain frozen.
    paired = bool(qs > 0 and qr > 0 and age is not None and age >= 0 and not availability_issue and provenance["status"] == "ready")
    result.update(candidate_score=baseline + scarcity_delta + risk_delta, paired_eligible=paired,
                  status="paired_score_available" if paired else "neutralization_diagnostic_only",
                  missing_fields=sorted(set(issues)),
                  explanation={"q_scarcity": qs, "q_risk": qr, "self_confidence": source_confidence,
                               "age_days": age, "freshness_multiplier": freshness,
                               "c_scarcity": cs, "c_risk": cr, "accepted_review_items": accepted,
                               "old_scarcity": old_scarcity, "new_scarcity": new_scarcity,
                               "quant_risk_unchanged": quant_risk, "old_risk": old_risk, "new_risk": new_risk,
                               "scarcity_decision_delta": scarcity_delta, "risk_decision_delta": risk_delta,
                               "review_status": "reviewer_attestation_bound_to_source_bytes" if accepted else "unverified",
                               "risk_gate_policy": "unchanged; neutral score does not assert safety"})
    return result


def build_daily_candidates(*, run_id: str, universe: pd.DataFrame, assessments: dict,
                           knowledge_at, screen_config, previous_holdings: list[str],
                           evidence_verifications: dict | None = None, input_manifest: dict | None = None,
                           mode: str = "diagnostic_replay") -> dict:
    """Build E2 solely from caller-frozen inputs; no I/O or global CONFIG reads.

    `previous_holdings=[]` explicitly requests no hysteresis incumbents. Research
    rankings contain exactly the same ready names on both sides; the full frozen
    production selection is returned separately to expose selection coverage.
    """
    if mode not in ("diagnostic_replay", "prospective_candidate"):
        raise ValueError("unsupported research mode")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,140}", run_id) or run_id in (".", ".."):
        raise ValueError("unsafe run_id")
    if universe.empty or universe.index.has_duplicates or any(not isinstance(t, str) or not t for t in universe.index):
        raise ValueError("unique nonempty ticker universe required")
    if not isinstance(previous_holdings, list) or any(not isinstance(t, str) or not t for t in previous_holdings) or len(set(previous_holdings)) != len(previous_holdings):
        raise ValueError("previous_holdings must be an explicit unique list")
    cfg, clock = _frozen_config(screen_config), aware(knowledge_at)
    reviews = evidence_verifications or {}
    rows = []
    for ticker, row in universe.iterrows():
        result = _one(ticker, row.to_dict(), assessments.get(ticker), reviews.get(ticker), clock, cfg)
        rows.append({"ticker": ticker, "production_eligible": row.get("eligible"), "segment": row.get("segment"),
                     "risk_eligible_unchanged": row.get("risk_eligible"), "risk_capped_unchanged": row.get("risk_capped"), **result})
    # Supplying the frozen cfg prevents select_top from consulting global CONFIG.
    selection_cfg = SimpleNamespace(**cfg)
    required_selection = {"eligible", "segment", "composite"}
    valid_selection = required_selection <= set(universe.columns) and all(
        isinstance(r.get("eligible"), (bool, np.bool_)) and isinstance(r.get("segment"), str)
        and r["segment"] in scoring.SEGMENT_ORDER and _finite(r.get("composite")) is not None
        for _, r in universe.iterrows())
    selection = {"status": "unavailable", "production_selection": [], "paired_baseline_selection": [],
                 "paired_candidate_selection": [], "added_to_paired_selection": [], "removed_from_paired_selection": [],
                 "comparison_scope": "matched_available_names", "full_universe_comparison_available": False}
    if valid_selection:
        production = list(scoring.select_top(universe, selection_cfg, prev=previous_holdings).index)
        paired_names = [r["ticker"] for r in rows if r["paired_eligible"]]
        paired = universe.loc[paired_names].copy()
        candidate = paired.copy()
        candidate["composite"] = pd.Series({r["ticker"]: r["candidate_score"] for r in rows}).reindex(paired.index)
        before = list(scoring.select_top(paired, selection_cfg, prev=previous_holdings).index)
        after = list(scoring.select_top(candidate, selection_cfg, prev=previous_holdings).index)
        selection.update(status="paired_selection_available" if paired_names else "no_paired_names",
                         production_selection=production, paired_baseline_selection=before, paired_candidate_selection=after,
                         added_to_paired_selection=[t for t in after if t not in before], removed_from_paired_selection=[t for t in before if t not in after],
                         full_universe_comparison_available=all(r["paired_eligible"] for r in rows if r["production_eligible"]))
    sources = {"universe": universe.to_dict(orient="index"), "universe_order": list(universe.index), "assessments": copy.deepcopy(assessments),
               "evidence_verifications": copy.deepcopy(reviews), "screen_config": cfg,
               "previous_holdings": list(previous_holdings), "input_manifest": copy.deepcopy(input_manifest or {})}
    identities = {"screen_config_hash": digest(cfg), **_CODE_IDENTITIES}
    artifact = {"schema": "stock_select.research_candidates.v1", "run_id": run_id, "mode": mode,
                "knowledge_at": clock.isoformat(), "protocol": copy.deepcopy(PROTOCOL), "protocol_hash": digest(PROTOCOL),
                "identities": identities, "experiment_hash": digest({"protocol": PROTOCOL, "identities": identities}),
                "sources": sources, "source_hash": digest(sources), "rows": rows, "selection": selection,
                "n_rows": len(rows), "n_paired_scores": sum(r["paired_eligible"] for r in rows),
                "validation_status": "NOT VALIDATED", "inference_status": "NO_RETURN_EVALUATION",
                "execution_status": "NO_ORDERS", "oos_eligible": False,
                "limitations": ["score and selection differences do not establish excess returns", "review declarations require independent audit; hashes only bind bytes",
                                "no confidence calibration, label evaluation or order generation", "build output is not a prospective registration"]}
    # Round-trip makes the returned object detached and JSON-safe, preserving the
    # exact same null representation in the source digest and archived payload.
    artifact["artifact_hash"] = digest(artifact)
    import json
    return json.loads(canonical_bytes(artifact))


def _verify_artifact(body: dict) -> None:
    if body.get("schema") != "stock_select.research_candidates.v1" or body.get("source_hash") != digest(body.get("sources")) or body.get("protocol_hash") != digest(body.get("protocol")):
        raise ValueError("candidate payload identity mismatch")
    if body.get("artifact_hash") != digest({k: v for k, v in body.items() if k != "artifact_hash"}):
        raise ValueError("candidate artifact hash mismatch")
    if body.get("protocol_hash") != digest(PROTOCOL) or body.get("mode") not in ("diagnostic_replay", "prospective_candidate"):
        raise ValueError("unsupported candidate protocol or mode")
    run_id = body.get("run_id", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,140}", run_id) or run_id in (".", ".."):
        raise ValueError("unsafe run_id")
    if body.get("experiment_hash") != digest({"protocol": body["protocol"], "identities": body["identities"]}):
        raise ValueError("candidate experiment identity mismatch")
    if any(body["identities"].get(key) != value for key, value in _CODE_IDENTITIES.items()):
        raise ValueError("candidate implementation unavailable for reconstruction")
    source = body["sources"]
    if body["identities"].get("screen_config_hash") != digest(source.get("screen_config")):
        raise ValueError("candidate config identity mismatch")
    order = source.get("universe_order")
    if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != set(source["universe"]):
        raise ValueError("candidate frozen universe order mismatch")
    universe = pd.DataFrame.from_dict(source["universe"], orient="index").reindex(order)
    rebuilt = build_daily_candidates(run_id=run_id, universe=universe, assessments=source["assessments"],
        knowledge_at=body["knowledge_at"], screen_config=source["screen_config"], previous_holdings=source["previous_holdings"],
        evidence_verifications=source["evidence_verifications"], input_manifest=source["input_manifest"], mode=body["mode"])
    if canonical_bytes(rebuilt) != canonical_bytes(body):
        raise ValueError("candidate derived state does not reconstruct from frozen inputs")


def register_candidates(root: Path, artifact: dict) -> dict:
    """Register a previously built artifact at the real clock; never backdate OOS."""
    body = copy.deepcopy(artifact)
    _verify_artifact(body)
    run_id = body["run_id"]
    path = Path(root) / body["protocol"]["id"] / f"{run_id}.json"
    request_hash = digest(body)
    if path.exists():
        import json
        old = json.loads(path.read_text())
        inspection = inspect_registration(path, now=datetime.now(timezone.utc), current_run_id=run_id)
        if inspection["status"] == "integrity_error":
            raise ValueError("candidate stored record integrity mismatch: " + "; ".join(inspection["integrity_errors"]))
        if old.get("record_hash") != digest({k: v for k, v in old.items() if k != "record_hash"}):
            raise ValueError("candidate stored record integrity mismatch")
        if old.get("request_hash") != request_hash:
            raise ValueError("immutable candidate conflict")
        return old
    clock = aware(datetime.now(timezone.utc))
    if aware(body["knowledge_at"]) > clock:
        raise ValueError("candidate knowledge_at after registration")
    body.update(registered_at=clock.isoformat(), effective_at=clock.isoformat(), request_hash=request_hash,
                registration_lag_seconds=(clock - aware(body["knowledge_at"])).total_seconds(),
                oos_eligible=body["mode"] == "prospective_candidate" and body["n_paired_scores"] > 0,
                sample_type="observed_forward_candidate" if body["mode"] == "prospective_candidate" else "diagnostic_replay")
    body["record_hash"] = digest(body)
    return write_once(path, body)


def inspect_registration(path: Path, *, now, current_run_id: str | None) -> dict:
    """Read-only, fail-closed UI summary; a registered score is never valid alpha."""
    from collections import Counter
    import json
    result = {"schema": "stock_select.research_candidate_readiness.v1", "status": "not_started",
              "validation_status": "NOT VALIDATED", "inference_status": "NO_RETURN_EVALUATION",
              "run_id": None, "matches_current_run": False, "n_rows": 0, "n_paired_scores": 0,
              "diagnostic_counts": {}, "selection": {}, "oos_scope_eligible": False,
              "registered_at": None, "effective_at": None, "integrity_errors": []}
    try:
        clock = aware(now)
        result["checked_at"] = clock.isoformat()
        if not Path(path).exists():
            return result
        record = json.loads(Path(path).read_text())
        if record.get("record_hash") != digest({k: v for k, v in record.items() if k != "record_hash"}):
            raise ValueError("candidate stored record integrity mismatch")
        registration, effective, knowledge = (aware(record[key]) for key in ("registered_at", "effective_at", "knowledge_at"))
        if not knowledge <= registration <= clock or effective != registration:
            raise ValueError("candidate registration clock mismatch or future record")
        if not _same(record.get("registration_lag_seconds"), (registration - knowledge).total_seconds()):
            raise ValueError("candidate registration lag mismatch")
        prospective = record.get("mode") == "prospective_candidate"
        expected_sample = "observed_forward_candidate" if prospective else "diagnostic_replay"
        expected_oos = prospective and record.get("n_paired_scores", 0) > 0
        if record.get("oos_eligible") is not expected_oos or record.get("sample_type") != expected_sample:
            raise ValueError("candidate observation scope mismatch")
        metadata = {"registered_at", "effective_at", "registration_lag_seconds", "request_hash", "sample_type", "record_hash"}
        artifact = {key: value for key, value in record.items() if key not in metadata}
        artifact["oos_eligible"] = False
        if record.get("request_hash") != digest(artifact):
            raise ValueError("candidate registration request mismatch")
        _verify_artifact(artifact)
        if record["run_id"] != Path(path).stem or record["protocol"]["id"] != Path(path).parent.name:
            raise ValueError("candidate path identity mismatch")
        current = current_run_id is not None and record["run_id"] == current_run_id
        counts = dict(Counter(row["status"] for row in record["rows"]))
        result.update(status="stale_run" if not current else "diagnostic_replay" if not prospective else "collecting" if record["n_paired_scores"] else "evidence_incomplete",
                      run_id=record["run_id"], matches_current_run=current, n_rows=record["n_rows"],
                      n_paired_scores=record["n_paired_scores"], diagnostic_counts=counts, selection=record["selection"],
                      oos_scope_eligible=expected_oos, forward_registered=prospective, registered_at=record["registered_at"], effective_at=record["effective_at"],
                      record_hash=record["record_hash"], experiment_hash=record["experiment_hash"],
                      protocol_id=record["protocol"]["id"], source_hash=record["source_hash"],
                      evidence_status="REVIEWER_ATTESTATIONS_REQUIRE_INDEPENDENT_AUDIT",
                      limitation="Score and shortlist differences only; no validated return or trade evidence")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
        result.update(status="integrity_error", integrity_errors=[str(exc)])
    return result
