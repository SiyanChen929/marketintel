"""Prospective score registration and matched-cohort diagnostics, never a trade ledger.

Registration starts the research clock. An old as-of date cannot move that clock
backwards. Source assessment provenance is preserved, including missing fields.
The frozen protocol uses the first XNYS close after registration, then exact
exchange-session horizons; missing bars cannot shorten or lengthen the horizon.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from ..trading_policy import ET, FINALITY_BUFFER, aware, calendar

SCHEMA = "stock_select.forward_scores.v1"
GROUPS = ("numerical", "fixed_text_rule", "llm")
HORIZONS = {"1w": 5, "1m": 21, "3m": 63}
OVERLAY_WEIGHT = 0.25
# Deliberately modest, frozen comparator. No fitting or search over these terms.
TEXT_RULE = {
    "positive": ["sole supplier", "sole source", "patent", "proprietary", "switching costs", "long-term agreement"],
    "negative": ["commodity", "intense competition", "price competition", "customer concentration", "export restrictions"],
}
PROTOCOL = {
    "version": "prospective_incremental_three_arm_v2", "groups": list(GROUPS),
    "numerical": "archived alpha_score; no LLM scarcity or LLM risk term",
    "fixed_text_rule": TEXT_RULE, "text_rule_formula": "A + 0.25 * clip(positive phrase hits minus negative phrase hits, -2, 2); each phrase at most once",
    "llm": "A + 0.25 * clip((scarcity_strength - 0.5) / 0.25, -2, 2)",
    "overlay_weight": OVERLAY_WEIGHT, "information_set": "same frozen LLM dossier when available; otherwise confounded_information_sets",
    "label": "adjusted-close diagnostic, first XNYS close strictly after effective_at, then h exchange sessions",
    "horizons": HORIZONS, "cohort_rule": "first registration per anchor session and protocol; all registered names",
    "paired_rule": "all three groups ready on identical names; no future-return completeness filter at registration",
    "inference": "descriptive date-level IC only; no IID t statistic for overlapping labels",
    "validation_status": "NOT VALIDATED",
}


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_clean(v) for v in value]
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def canonical_bytes(value) -> bytes:
    return json.dumps(_clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _valid_hash(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _number(value):
    try:
        return float(value) if value is not None and math.isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def write_once(path: Path, body: dict) -> dict:
    """Atomically create an immutable JSON file; exact retries are idempotent."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_bytes(body)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pending-", delete=False) as handle:
        temp = Path(handle.name)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temp, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError(f"immutable record conflict: {path.name}") from None
    finally:
        temp.unlink(missing_ok=True)
    return body


def load_record(path: Path) -> dict:
    record = json.loads(Path(path).read_text())
    supplied = record.get("record_hash")
    body = {k: v for k, v in record.items() if k != "record_hash"}
    if record.get("schema") != SCHEMA or supplied != digest(body):
        raise ValueError(f"forward record integrity mismatch: {Path(path).name}")
    for key, payload in (("source_hash", record.get("sources")), ("protocol_hash", record.get("protocol"))):
        if record.get(key) != digest(payload):
            raise ValueError(f"forward internal {key} mismatch: {Path(path).name}")
    if "arm_identities" in record:
        expected_experiment = digest({"protocol": record["protocol"], "identities": record["arm_identities"]})
        if record.get("experiment_hash") != expected_experiment:
            raise ValueError(f"forward experiment identity mismatch: {Path(path).name}")
        request = {"run_id": record["run_id"], "decision_at": record["decision_at"], "source": record["sources"],
                   "protocol": record["protocol"], "identities": record["arm_identities"]}
        if record.get("request_hash") != digest(request):
            raise ValueError(f"forward request identity mismatch: {Path(path).name}")
    return record


def load_contexts(directory: Path) -> dict:
    """Read existing context files; no vendor requests or inferred timestamps."""
    return {p.stem: json.loads(p.read_text()) for p in sorted(Path(directory).glob("*.json"))}


def _llm(assessment: dict | None, effective_at: pd.Timestamp) -> dict:
    if not assessment:
        return {"score": None, "status": "missing", "missing_fields": ["assessment"]}
    from ..analysis.strategic import _availability_issue, scarcity_strength
    missing = []
    availability_issue = _availability_issue(assessment, effective_at)
    if availability_issue:
        missing.append(availability_issue)
    metadata = assessment.get("provenance") or {}
    for key in ("model", "prompt_hash", "evidence_hash", "known_at"):
        if not metadata.get(key):
            missing.append(key)
    if not isinstance(metadata.get("prompts"), dict) or not metadata["prompts"]:
        missing.append("prompt_payload")
    if not isinstance(assessment.get("evidence"), list) or not assessment["evidence"]:
        missing.append("evidence_payload")
    for key in ("prompt_hash", "evidence_hash"):
        if metadata.get(key) and not _valid_hash(metadata[key]):
            missing.append(f"invalid_{key}")
    if metadata.get("evidence_hash") and metadata["evidence_hash"] != digest(assessment.get("evidence", [])):
        missing.append("evidence_hash_mismatch")
    if metadata.get("prompts") is not None and metadata.get("prompt_hash") != digest(metadata["prompts"]):
        missing.append("prompt_hash_mismatch")
    for evidence in assessment.get("evidence", []) or []:
        if not isinstance(evidence, dict):
            missing.append("invalid_evidence_payload")
            continue
        for key in ("date", "known_at", "published_at", "available_at"):
            if evidence.get(key) is None:
                continue
            try:
                stamp = pd.Timestamp(evidence[key])
                if pd.isna(stamp):
                    raise ValueError("missing timestamp")
                if stamp.tzinfo is None:
                    # Date-only publication claims cannot establish an exact time,
                    # but an explicitly future date is already disqualifying.
                    if stamp.date() > effective_at.tz_convert(ET).date():
                        missing.append("future_evidence")
                    if key != "date":
                        missing.append("invalid_evidence_" + key)
                elif stamp.tz_convert("UTC") > effective_at:
                    missing.append("future_evidence")
            except (ValueError, TypeError):
                missing.append("invalid_evidence_" + key)
    known_at = None
    if metadata.get("known_at"):
        try:
            known_at = aware(metadata["known_at"])
        except (ValueError, TypeError, RuntimeError):
            missing.append("invalid_known_at")
    if known_at is not None and known_at > effective_at:
        missing.append("known_after_decision")
    dims = assessment.get("dimensions") or {}
    from ..analysis.strategic import SCARCITY_WEIGHTS
    if any(_number((dims.get(k) or {}).get("score")) is None or not 0 <= float(dims[k]["score"]) <= 1 for k in SCARCITY_WEIGHTS):
        score = None
        missing.append("scarcity_dimensions")
    else:
        score = _number(scarcity_strength(dims))
    return {"score": score, "status": "ready" if not missing else "provenance_incomplete",
            "missing_fields": sorted(set(missing)), "model": metadata.get("model"),
            "prompt_hash": metadata.get("prompt_hash"), "evidence_hash": metadata.get("evidence_hash"),
            "known_at": known_at.isoformat() if known_at is not None else None,
            "assessment_hash": digest(assessment), "assessment_asof": assessment.get("asof"),
            "assessment_version": assessment.get("version"), "assessor": assessment.get("assessor"),
            "source_availability_status": metadata.get("source_availability_status", "unverified")}


def _input_time_issue(source: dict, decision: pd.Timestamp) -> str | None:
    if not source.get("known_at"):
        return None
    try:
        return "known_after_decision" if aware(source["known_at"]) > decision else None
    except (ValueError, TypeError, RuntimeError):
        return "invalid_known_at"


def _corpus(assessment: dict, context: dict) -> tuple[str | None, str, dict]:
    provenance = assessment.get("provenance") or {}
    parse_user = (provenance.get("prompts") or {}).get("parse_user")
    if isinstance(parse_user, str) and "DOSSIER:\n" in parse_user:
        return parse_user.split("DOSSIER:\n", 1)[1], "shared_frozen_dossier", provenance
    return context.get("business_summary"), "confounded_information_sets", context


def register_snapshot(root: Path, *, run_id: str, decision_at, universe: pd.DataFrame,
                      assessments: dict, contexts: dict | None = None,
                      input_manifest: dict | None = None, recorded_at=None) -> dict:
    """Freeze three arms now. A clock override forces synthetic fixture status.

    A repeated run ID accepts identical inputs only, never changed scores. Old
    assessments can be carried forward, but absent provenance remains absent.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,140}", run_id) or run_id in (".", ".."):
        raise ValueError("unsafe run_id")
    if universe.index.has_duplicates or universe.empty:
        raise ValueError("registration requires a nonempty unique ticker index")
    if any(not isinstance(t, str) or not t.strip() for t in universe.index):
        raise ValueError("registration requires nonempty string tickers")
    decision = aware(decision_at)
    clock = aware(recorded_at or datetime.now(timezone.utc))
    sample_type = "synthetic_fixture" if recorded_at is not None else "observed_forward"
    if decision > clock:
        raise ValueError("decision_at cannot be later than registration")
    contexts = contexts or {}
    from ..analysis import scoring, strategic
    from ..config.settings import CONFIG
    identities = {
        "numerical": {"field": "alpha_score", "config_hash": digest(asdict(CONFIG.screen)),
                      "implementation_hash": hashlib.sha256(Path(scoring.__file__).read_bytes()).hexdigest()},
        "fixed_text_rule": {"rule_hash": digest(TEXT_RULE), "input_field": "business_summary"},
        "llm": {"models": sorted({str((a.get("provenance") or {}).get("model") or "UNKNOWN") for a in assessments.values()}),
                "rubric_hash": digest(strategic.SCARCITY_WEIGHTS),
                "prompt_template_hashes": sorted({str((a.get("provenance") or {}).get("prompt_template_hash") or "UNKNOWN") for a in assessments.values()})},
    }
    experiment_hash = digest({"protocol": PROTOCOL, "identities": identities})
    source = {"universe": _clean(universe.sort_index().to_dict(orient="index")), "assessments": assessments,
              "contexts": contexts, "input_manifest": input_manifest or {}}
    request_hash = digest({"run_id": run_id, "decision_at": decision.isoformat(), "source": source,
                           "protocol": PROTOCOL, "identities": identities})
    path = Path(root) / "registrations" / f"{run_id}.json"
    if path.exists():
        existing = load_record(path)
        if existing["request_hash"] != request_hash:
            raise ValueError(f"immutable record conflict: {run_id}")
        return existing
    rows = []
    for ticker, row in universe.sort_index().iterrows():
        numerical = _number(row.get("alpha_score"))
        context = contexts.get(ticker) or {}
        assessment = assessments.get(ticker) or {}
        raw_text, information_set, text_source = _corpus(assessment, context)
        text_time_issue = _input_time_issue(text_source, decision)
        numeric_time_issue = _input_time_issue(row.to_dict(), decision)
        has_text = isinstance(raw_text, str) and bool(raw_text.strip())
        lower = raw_text.lower() if has_text else ""
        hits = {side: [p for p in phrases if p in lower] for side, phrases in TEXT_RULE.items()}
        text_score = len(hits["positive"]) - len(hits["negative"]) if has_text else None
        llm = _llm(assessment, decision)
        llm["raw_score"] = llm["score"]
        llm["score"] = numerical + OVERLAY_WEIGHT * float(np.clip((llm["raw_score"] - .5) / .25, -2, 2)) if numerical is not None and llm["raw_score"] is not None else None
        if numerical is None or numeric_time_issue:
            if llm["status"] == "ready":
                llm["status"] = "missing_numerical_baseline"
            llm["missing_fields"].append("numerical_baseline")
        text_status = "ready" if has_text and numerical is not None else "missing"
        if text_time_issue or numeric_time_issue:
            text_status = "provenance_incomplete"
        rows.append({"ticker": ticker, "security_identity_status": "current_ticker_only",
                     "incremental_identification": information_set,
                     "groups": {
                         "numerical": {"score": numerical, "status": "provenance_incomplete" if numeric_time_issue else "ready" if numerical is not None else "missing",
                                       "missing_fields": [numeric_time_issue] if numeric_time_issue else [],
                                       "known_at": clock.isoformat(), "feature_hash": digest(source["universe"][ticker])},
                         "fixed_text_rule": {"score": numerical + OVERLAY_WEIGHT * float(np.clip(text_score, -2, 2)) if text_score is not None and numerical is not None else None,
                                             "raw_score": text_score, "status": text_status, "missing_fields": [x for x in (text_time_issue, numeric_time_issue) if x],
                                             "known_at": text_source.get("known_at") or clock.isoformat(), "text_hash": digest(raw_text), "hits": hits},
                         "llm": llm},
                     "source_eligible": _clean(row.get("eligible")), "segment": _clean(row.get("segment"))})
    record = {"schema": SCHEMA, "run_id": run_id, "request_hash": request_hash,
              "decision_at": decision.isoformat(), "registered_at": clock.isoformat(),
              "sample_type": sample_type,
              "effective_at": clock.isoformat(), "backdated": False,
              "registration_lag_seconds": (clock - decision).total_seconds(),
              "protocol": PROTOCOL, "protocol_hash": digest(PROTOCOL),
              "arm_identities": identities, "experiment_hash": experiment_hash,
              "implementation_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_hash": digest(source), "sources": source, "rows": rows,
              "validation_status": "NOT VALIDATED", "execution_status": "NO_ORDERS",
              "limitations": ["diagnostic labels are not fills or portfolio returns", "source publication timestamps require separate verification",
                              "ticker identity and adjusted-price history are not survivorship-free PIT data"]}
    record["record_hash"] = digest(record)
    return write_once(path, record)


def label_window(effective_at, sessions: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    if isinstance(sessions, bool) or not isinstance(sessions, int) or sessions < 1:
        raise ValueError("horizon must be a positive number of exchange sessions")
    instant = aware(effective_at)
    cal = calendar()
    anchor = cal.date_to_session(instant.tz_convert(ET).date(), direction="next")
    if cal.session_close(anchor) <= instant:
        anchor = cal.next_session(anchor)
    end = cal.session_offset(anchor, sessions)
    return anchor, end


def labels(px: pd.DataFrame, *, effective_at, sessions: int, acquired_at, evaluated_at=None) -> dict:
    """Never ffill, search ahead for a missing price, or treat future bars as mature."""
    acquired = aware(acquired_at)
    evaluation = aware(evaluated_at or datetime.now(timezone.utc))
    if acquired > evaluation:
        raise ValueError("price acquisition is later than the evaluation clock")
    if px.index.has_duplicates or px.columns.has_duplicates or not px.index.is_monotonic_increasing:
        raise ValueError("label prices require sorted unique sessions and unique tickers")
    idx = pd.DatetimeIndex(px.index)
    if idx.tz is not None or not idx.equals(idx.normalize()):
        raise ValueError("label prices require timezone-naive daily session labels")
    if any(not calendar().is_session(day) for day in idx):
        raise ValueError("label price index contains a non-XNYS session")
    anchor, end = label_window(effective_at, sessions)
    matured_at = calendar().session_close(end) + FINALITY_BUFFER
    result = {"anchor_session": str(anchor.date()), "end_session": str(end.date()),
              "matures_at": matured_at.isoformat(), "status": "immature", "returns": {}}
    if acquired < matured_at or evaluation < matured_at:
        return result
    if anchor not in px.index or end not in px.index:
        result["status"] = "missing_price_session"
        return result
    result["status"] = "mature"
    for ticker in px.columns:
        start_price, end_price = _number(px.at[anchor, ticker]), _number(px.at[end, ticker])
        result["returns"][ticker] = end_price / start_price - 1 if start_price and start_price > 0 and end_price and end_price > 0 else None
    return result


def evaluate(root: Path, px: pd.DataFrame, *, acquired_at, evaluated_at=None, min_names: int = 20,
             include_synthetic: bool = False) -> dict:
    """Read only; report each version but summarize first registration per anchor.

    Group comparisons share names and a date, never substitute missing LLM scores
    with zero. Correlations are descriptive; evaluation does not certify alpha.
    """
    if isinstance(min_names, bool) or not isinstance(min_names, int) or min_names < 3:
        raise ValueError("min_names must be an integer >= 3")
    evaluation = aware(evaluated_at or datetime.now(timezone.utc))
    acquired = aware(acquired_at)
    if acquired > evaluation:
        raise ValueError("price acquisition is later than the evaluation clock")
    paths = sorted((Path(root) / "registrations").glob("*.json"))
    records, errors, excluded = [], [], Counter()
    for path in paths:
        try:
            record = load_record(path)
            if (include_synthetic or record.get("sample_type") == "observed_forward") and aware(record["registered_at"]) <= evaluation:
                records.append(record)
            else:
                excluded["registered_after_evaluation" if aware(record["registered_at"]) > evaluation else record.get("sample_type", "unverified_legacy_scope")] += 1
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            errors.append({"file": path.name, "reason": str(exc)})
    records.sort(key=lambda r: (r["registered_at"], r["run_id"]))
    detail, selected = [], set()
    for record in records:
        if aware(record["registered_at"]) > evaluation:
            continue
        for horizon, sessions in record["protocol"]["horizons"].items():
            lab = labels(px, effective_at=record["effective_at"], sessions=sessions, acquired_at=acquired, evaluated_at=evaluation)
            experiment = record.get("experiment_hash", record["protocol_hash"])
            key = (experiment, horizon, lab["anchor_session"])
            canonical = key not in selected
            selected.add(key)
            coverage = {group: dict(Counter(r["groups"][group]["status"] for r in record["rows"])) for group in GROUPS}
            paired = [r for r in record["rows"] if all(r["groups"][g]["status"] == "ready" for g in GROUPS)]
            observed = [r for r in paired if lab["returns"].get(r["ticker"]) is not None]
            correlations = {}
            cohort_status = "partial_labels" if lab["status"] == "mature" and len(observed) < len(paired) else "complete_labels" if lab["status"] == "mature" else "labels_unavailable"
            if lab["status"] == "mature" and len(observed) >= min_names and cohort_status == "complete_labels":
                y = [lab["returns"][r["ticker"]] for r in observed]
                for group in GROUPS:
                    x = [r["groups"][group]["score"] for r in observed]
                    correlations[group] = float(spearmanr(x, y).statistic) if len(set(x)) > 1 and len(set(y)) > 1 else None
            detail.append({"run_id": record["run_id"], "record_hash": record["record_hash"], "protocol_hash": record["protocol_hash"], "experiment_hash": experiment,
                           "horizon": horizon, "canonical_for_summary": canonical, **{k: v for k, v in lab.items() if k != "returns"},
                           "n_registered": len(record["rows"]), "coverage": coverage, "n_paired_scores": len(paired),
                           "cohort_status": cohort_status,
                           "incremental_identification": "shared_frozen_dossier" if paired and all(r.get("incremental_identification") == "shared_frozen_dossier" for r in paired) else "confounded_information_sets",
                           "n_paired_labels": len(observed), "n_missing_paired_labels": len(paired) - len(observed) if lab["status"] == "mature" else None,
                           "ic": correlations, "inference_status": "DESCRIPTIVE_ONLY"})
    summary = {}
    for item in detail:
        if not item["canonical_for_summary"]:
            continue
        key = item["experiment_hash"] + ":" + item["horizon"]
        group = summary.setdefault(key, {"horizon": item["horizon"], "protocol_hash": item["protocol_hash"],
                                         "experiment_hash": item["experiment_hash"], "n_dates": 0, "maturity": {}, "cohort_status": "complete_labels",
                                         "incremental_identification": "shared_frozen_dossier", "ic": {g: [] for g in GROUPS},
                                         "incremental_ic": {g: [] for g in GROUPS if g != "numerical"}, "llm_vs_rule_ic": []})
        group["n_dates"] += 1
        group["maturity"][item["status"]] = group["maturity"].get(item["status"], 0) + 1
        if item["cohort_status"] == "partial_labels":
            group["cohort_status"] = "partial_labels"
        if item["incremental_identification"] == "confounded_information_sets":
            group["incremental_identification"] = "confounded_information_sets"
        # An undefined correlation in any arm excludes this date from every arm,
        # preserving identical security AND date samples for the comparison.
        if len(item["ic"]) == len(GROUPS) and all(value is not None for value in item["ic"].values()):
            for arm, value in item["ic"].items():
                group["ic"][arm].append(value)
                if arm != "numerical":
                    group["incremental_ic"][arm].append(value - item["ic"]["numerical"])
            group["llm_vs_rule_ic"].append(item["ic"]["llm"] - item["ic"]["fixed_text_rule"])
    for group in summary.values():
        group["ic"] = {arm: {"mean_ic": float(np.mean(values)) if values else None, "n_dates": len(values), "t_stat": None}
                       for arm, values in group["ic"].items()}
        group["incremental_ic"] = {arm: {"mean_difference_vs_numerical": float(np.mean(values)) if values else None,
                                         "n_dates": len(values), "t_stat": None} for arm, values in group["incremental_ic"].items()}
        differences = group["llm_vs_rule_ic"]
        group["llm_vs_rule_ic"] = {"mean_difference": float(np.mean(differences)) if differences else None,
                                    "n_dates": len(differences), "t_stat": None,
                                    "identification": group["incremental_identification"]}
    return {"schema": "stock_select.forward_evaluation.v1", "status": "integrity_error" if errors else "diagnostic_only" if records else "no_registrations",
            "validation_status": "NOT VALIDATED", "evaluated_at": evaluation.isoformat(), "price_acquired_at": acquired.isoformat(),
            "price_hash": digest({"index": [str(x) for x in px.index], "columns": list(px.columns), "values": px.to_numpy()}),
            "n_registered_versions": len(records), "records": detail, "summary": summary, "integrity_errors": errors,
            "excluded_versions": dict(excluded),
            "inference_status": "DESCRIPTIVE_ONLY", "t_stat_reason": "overlapping horizons and insufficient independent dates; no IID significance",
            "performance_basis": "fixed-horizon score comparison, no trades, costs, borrow or portfolio P&L"}


def summary(root: Path, *, now=None, include_synthetic: bool = False) -> dict:
    """Read-only readiness view. Calendar maturity does not prove label availability."""
    clock = aware(now or datetime.now(timezone.utc))
    records, errors, excluded = [], [], Counter()
    for path in sorted((Path(root) / "registrations").glob("*.json")):
        try:
            record = load_record(path)
            if (include_synthetic or record.get("sample_type") == "observed_forward") and aware(record["registered_at"]) <= clock:
                records.append(record)
            else:
                excluded["registered_after_query_clock" if aware(record["registered_at"]) > clock else record.get("sample_type", "unverified_legacy_scope")] += 1
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            errors.append({"file": path.name, "reason": str(exc)})
    records.sort(key=lambda r: (r["registered_at"], r["run_id"]))
    group_coverage = {g: Counter() for g in GROUPS}
    maturity = Counter()
    for record in records:
        for row in record["rows"]:
            for group in GROUPS:
                group_coverage[group][row["groups"][group]["status"]] += 1
        for sessions in record["protocol"]["horizons"].values():
            _, end = label_window(record["effective_at"], sessions)
            status = "calendar_mature_labels_unverified" if calendar().session_close(end) + FINALITY_BUFFER <= clock else "immature"
            maturity[status] += 1
    latest = records[-1] if records else None
    return {"schema": "stock_select.forward_readiness.v1", "status": "integrity_error" if errors else "collecting" if records else "not_started",
            "validation_status": "NOT VALIDATED", "n_registered_versions": len(records),
            "n_registration_dates": len({aware(r["registered_at"]).tz_convert(ET).date() for r in records}),
            "decision_dates": sorted({aware(r["decision_at"]).tz_convert(ET).date().isoformat() for r in records}),
            "first_registered_at": records[0]["registered_at"] if records else None,
            "last_registered_at": latest["registered_at"] if latest else None,
            "latest_run_id": latest["run_id"] if latest else None,
            "latest_record_hash": latest["record_hash"] if latest else None,
            "groups": {g: dict(v) for g, v in group_coverage.items()}, "maturity": dict(maturity),
            "coverage_basis": "registered rows across all versions",
            "latest_groups": {g: dict(Counter(r["groups"][g]["status"] for r in latest["rows"])) for g in GROUPS} if latest else {},
            "label_availability": "unverified; run evaluate with confirmed prices and acquisition time",
            "excluded_versions": dict(excluded),
            "integrity_errors": errors, "checked_at": clock.isoformat()}
