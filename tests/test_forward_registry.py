"""Causal-clock, immutable evidence, missing-label and matched-cohort regressions."""
import copy
import json

import numpy as np
import pandas as pd
import pytest

from ai_semi_intel.analysis import strategic
from ai_semi_intel.research import forward_registry as F, live_ic
from ai_semi_intel.trading_policy import calendar


def _assessment(score=.6, *, known_at="2026-09-22T11:00:00+00:00", provenance=True):
    evidence = [{"date": "2026-09-21", "source": "fixture", "claim": "test evidence"}]
    result = {"ticker": "A", "asof": "2026-09-22", "version": 1, "strategic_score": score,
              "dimensions": {k: {"score": score, "rationale": "fixture"} for k in strategic.SCARCITY_WEIGHTS}, "evidence": evidence}
    if provenance:
        result["provenance"] = {"model": "frozen-test-model", "prompt_hash": F.digest({"system": "fixture prompt"}), "prompts": {"system": "fixture prompt"},
                                "evidence_hash": F.digest(evidence), "known_at": known_at}
    return result


def _register(root, run_id="run-a", *, registered="2026-09-22T12:00:00+00:00", decision="2026-09-22T11:50:00+00:00", legacy=False):
    universe = pd.DataFrame({"alpha_score": [1., 2., 3.], "eligible": [True, False, True]}, index=["A", "B", "C"])
    assessments = {t: _assessment(i / 4, provenance=not legacy) for i, t in enumerate(universe.index, 1)}
    contexts = {"A": {"business_summary": "commodity"}, "B": {"business_summary": "normal components"},
                "C": {"business_summary": "proprietary patent"}}
    return F.register_snapshot(root, run_id=run_id, decision_at=decision, universe=universe,
                               assessments=assessments, contexts=contexts, recorded_at=registered)


def _prices():
    idx = calendar().sessions_in_range("2026-09-22", "2026-10-23")
    return pd.DataFrame({"A": np.linspace(100, 110, len(idx)), "B": np.linspace(100, 120, len(idx)),
                         "C": np.linspace(100, 130, len(idx))}, index=idx)


def test_old_score_cannot_backdate_registration_and_legacy_provenance_remains_missing(tmp_path):
    record = _register(tmp_path, decision="2026-09-02T12:00:00+00:00", legacy=True)
    assert record["effective_at"].startswith("2026-09-22")
    assert record["backdated"] is False
    assert record["rows"][0]["groups"]["llm"]["status"] == "provenance_incomplete"
    assert "known_at" in record["rows"][0]["groups"]["llm"]["missing_fields"]
    assert F.label_window(record["effective_at"], 5)[0] == pd.Timestamp("2026-09-22")
    assert "provenance" not in record["sources"]["assessments"]["A"]


def test_repeated_identical_registration_is_idempotent_but_changed_score_is_rejected(tmp_path):
    first = _register(tmp_path)
    raw = (tmp_path / "registrations/run-a.json").read_bytes()
    again = _register(tmp_path, registered="2026-09-23T12:00:00+00:00")
    assert first == again
    assert (tmp_path / "registrations/run-a.json").read_bytes() == raw
    universe = pd.DataFrame({"alpha_score": [99]}, index=["A"])
    with pytest.raises(ValueError, match="immutable"):
        F.register_snapshot(tmp_path, run_id="run-a", decision_at="2026-09-22T11:50:00Z", universe=universe,
                            assessments={}, recorded_at="2026-09-23T12:00:00Z")
    assert (tmp_path / "registrations/run-a.json").read_bytes() == raw


def test_future_assessment_is_excluded_even_if_available_before_late_registration(tmp_path):
    record = _register(tmp_path, decision="2026-09-22T10:00:00Z")
    group = record["rows"][0]["groups"]["llm"]
    assert group["status"] == "provenance_incomplete" and "known_after_decision" in group["missing_fields"]


@pytest.mark.parametrize("patch,reason", [({"asof": "2026-09-23"}, "future_asof"),
    ({"available_at": "2026-09-22T12:01:00Z"}, "future_available_at"),
    ({"asof": "NaT"}, "invalid_asof")])
def test_registry_and_production_share_source_availability_boundary(patch, reason):
    assessment = _assessment()
    assessment.update(patch)
    group = F._llm(assessment, pd.Timestamp("2026-09-22T12:00:00Z"))
    assert group["status"] == "provenance_incomplete"
    assert reason in group["missing_fields"]


@pytest.mark.parametrize("field,value,missing", [("prompt_hash", "short", "invalid_prompt_hash"),
                                                   ("known_at", "2026-09-22", "invalid_known_at"),
                                                   ("evidence_hash", "a" * 64, "evidence_hash_mismatch")])
def test_invalid_source_provenance_is_not_silently_ready(field, value, missing):
    a = _assessment(); a["provenance"][field] = value
    got = F._llm(a, pd.Timestamp("2026-09-22T12:00:00Z"))
    assert got["status"] == "provenance_incomplete" and missing in got["missing_fields"]


def test_missing_llm_and_text_are_explicit_not_neutral_scores(tmp_path):
    rec = F.register_snapshot(tmp_path, run_id="missing", decision_at="2026-09-22T10:00:00Z",
                              universe=pd.DataFrame({"alpha_score": [np.nan]}, index=["A"]), assessments={},
                              recorded_at="2026-09-22T12:00:00Z")
    for group in F.GROUPS:
        assert rec["rows"][0]["groups"][group]["status"] == "missing"
        assert rec["rows"][0]["groups"][group]["score"] is None


def test_integrity_mismatch_is_reported_and_never_used(tmp_path):
    _register(tmp_path)
    path = tmp_path / "registrations/run-a.json"
    d = json.loads(path.read_text()); d["rows"][0]["groups"]["numerical"]["score"] = 999
    path.write_text(json.dumps(d))
    result = F.evaluate(tmp_path, include_synthetic=True, px=_prices(), acquired_at="2026-10-23T21:00:00Z", evaluated_at="2026-10-23T21:01:00Z")
    assert result["n_registered_versions"] == 0 and len(result["integrity_errors"]) == 1
    assert F.summary(tmp_path, include_synthetic=True)["status"] == "integrity_error"


def test_close_after_decision_and_exchange_calendar_control_horizon():
    assert F.label_window("2026-09-22T19:59:59Z", 5) == (pd.Timestamp("2026-09-22"), pd.Timestamp("2026-09-29"))
    assert F.label_window("2026-09-22T20:00:00Z", 5) == (pd.Timestamp("2026-09-23"), pd.Timestamp("2026-09-30"))
    # Thanksgiving closed; Black Friday is a 13:00 ET early close.
    assert F.label_window("2026-11-26T12:00:00Z", 1) == (pd.Timestamp("2026-11-27"), pd.Timestamp("2026-11-30"))
    assert F.label_window("2026-11-27T18:00:00Z", 1)[0] == pd.Timestamp("2026-11-30")


def test_labels_require_original_acquisition_after_finality_and_never_skip_missing_day():
    args = dict(effective_at="2026-09-22T12:00:00Z", sessions=5, evaluated_at="2026-10-24T00:00:00Z")
    assert F.labels(_prices(), acquired_at="2026-09-29T20:19:59Z", **args)["status"] == "immature"
    good = F.labels(_prices(), acquired_at="2026-09-29T20:20:00Z", **args)
    assert good["status"] == "mature" and good["end_session"] == "2026-09-29"
    # Having the next day's value cannot rescue a missing exact endpoint.
    px = _prices().drop(pd.Timestamp("2026-09-29"))
    assert F.labels(px, acquired_at="2026-10-23T21:00:00Z", **args)["status"] == "missing_price_session"


def test_missing_individual_prices_preserve_denominator_and_common_cohort(tmp_path):
    _register(tmp_path)
    px = _prices(); px.loc["2026-09-29", "C"] = np.nan
    result = F.evaluate(tmp_path, include_synthetic=True, px=px, acquired_at="2026-10-23T21:00:00Z", evaluated_at="2026-10-23T21:01:00Z", min_names=3)
    first = next(r for r in result["records"] if r["horizon"] == "1w")
    assert first["n_registered"] == first["n_paired_scores"] == 3
    assert first["n_paired_labels"] == 2 and first["n_missing_paired_labels"] == 1
    assert first["ic"] == {}


def test_three_arm_comparison_shares_names_including_source_gated_name_and_never_claims_significance(tmp_path):
    _register(tmp_path)
    result = F.evaluate(tmp_path, include_synthetic=True, px=_prices(), acquired_at="2026-10-23T21:00:00Z", evaluated_at="2026-10-23T21:01:00Z", min_names=3)
    first = next(r for r in result["records"] if r["horizon"] == "1w")
    assert first["n_paired_labels"] == 3  # source eligibility false for B cannot select the test cohort
    assert first["ic"] == {"numerical": 1., "fixed_text_rule": 1., "llm": 1.}
    for group in result["summary"].values():
        for arm in group["ic"].values():
            assert arm["t_stat"] is None
    assert result["validation_status"] == "NOT VALIDATED"


def test_same_day_rerun_remains_inspectable_but_cannot_replace_first_registered_cohort(tmp_path):
    first = _register(tmp_path, run_id="z-first")
    _register(tmp_path, run_id="a-rerun", registered="2026-09-22T13:00:00Z")
    result = F.evaluate(tmp_path, include_synthetic=True, px=_prices(), acquired_at="2026-10-23T21:00:00Z", evaluated_at="2026-10-23T21:01:00Z", min_names=3)
    assert result["n_registered_versions"] == 2
    assert all(r["canonical_for_summary"] == (r["run_id"] == first["run_id"]) for r in result["records"])
    assert all(g["n_dates"] == 1 for g in result["summary"].values())


def test_summary_calendar_maturity_does_not_claim_price_labels_available(tmp_path):
    _register(tmp_path, legacy=True)
    result = F.summary(tmp_path, include_synthetic=True, now="2026-10-23T21:01:00Z")
    assert result["groups"]["llm"] == {"provenance_incomplete": 3}
    assert result["maturity"] == {"calendar_mature_labels_unverified": 2, "immature": 1}
    assert result["label_availability"].startswith("unverified")


def test_configuration_drift_starts_separate_experiment_not_more_observations_in_same_study(tmp_path, monkeypatch):
    from ai_semi_intel.config import settings
    first = _register(tmp_path, run_id="original")
    cfg = copy.deepcopy(settings.CONFIG)
    cfg.screen.alpha_w_growth = .55
    monkeypatch.setattr(settings, "CONFIG", cfg)
    second = _register(tmp_path, run_id="changed", registered="2026-09-22T13:00:00Z")
    assert first["experiment_hash"] != second["experiment_hash"]
    result = F.evaluate(tmp_path, include_synthetic=True, px=_prices(), acquired_at="2026-10-23T21:00:00Z", evaluated_at="2026-10-23T21:01:00Z", min_names=3)
    assert len(result["summary"]) == 6
    assert all(g["n_dates"] == 1 for g in result["summary"].values())


def test_clock_override_is_synthetic_and_never_enters_observed_readiness_or_evaluation(tmp_path):
    record = _register(tmp_path)
    assert record["sample_type"] == "synthetic_fixture"
    assert F.summary(tmp_path)["n_registered_versions"] == 0
    result = F.evaluate(tmp_path, _prices(), acquired_at="2026-10-23T21:00:00Z", evaluated_at="2026-10-23T21:01:00Z")
    assert result["n_registered_versions"] == 0 and result["records"] == []


def test_v2_scores_use_identical_numeric_base_and_predeclared_overlay_intensity(tmp_path):
    record = _register(tmp_path)
    assert record["protocol"]["version"] == "prospective_incremental_three_arm_v2"
    scores = {r["ticker"]: {g: r["groups"][g]["score"] for g in F.GROUPS} for r in record["rows"]}
    assert scores == {"A": {"numerical": 1., "fixed_text_rule": .75, "llm": .75},
                      "B": {"numerical": 2., "fixed_text_rule": 2., "llm": 2.},
                      "C": {"numerical": 3., "fixed_text_rule": 3.5, "llm": 3.25}}
    assert all(r["incremental_identification"] == "confounded_information_sets" for r in record["rows"])


def test_fixed_rule_uses_same_frozen_dossier_as_llm_when_available(tmp_path):
    a = _assessment()
    prompts = {"parse_user": "Ticker: A. Today: 2026-09-22.\n\nDOSSIER:\nsole supplier patent"}
    a["provenance"].update(prompts=prompts, prompt_hash=F.digest(prompts))
    rec = F.register_snapshot(tmp_path, run_id="shared", decision_at="2026-09-22T11:50:00Z", recorded_at="2026-09-22T12:00:00Z",
                              universe=pd.DataFrame({"alpha_score": [1.]}, index=["A"]), assessments={"A": a},
                              contexts={"A": {"business_summary": "commodity"}})
    row = rec["rows"][0]
    assert row["groups"]["fixed_text_rule"]["raw_score"] == 2
    assert row["groups"]["fixed_text_rule"]["score"] == 1.5
    assert row["incremental_identification"] == "shared_frozen_dossier"


def test_constant_overlay_combination_excludes_date_from_every_summary_arm(tmp_path):
    tickers = ["A", "B", "C"]
    rec = F.register_snapshot(tmp_path, run_id="cancellation", decision_at="2026-09-22T11:50:00Z", recorded_at="2026-09-22T12:00:00Z",
                              universe=pd.DataFrame({"alpha_score": [0., .25, .5]}, index=tickers),
                              assessments={t: _assessment(.5) for t in tickers},
                              contexts={t: {"business_summary": text} for t, text in zip(tickers, ["patent proprietary", "patent", "ordinary"])} )
    assert {r["groups"]["fixed_text_rule"]["score"] for r in rec["rows"]} == {.5}
    out = F.evaluate(tmp_path, _prices(), include_synthetic=True, min_names=3,
                     acquired_at="2026-10-23T21:00:00Z", evaluated_at="2026-10-23T21:01:00Z")
    week = next(g for g in out["summary"].values() if g["horizon"] == "1w")
    assert {arm["n_dates"] for arm in week["ic"].values()} == {0}
    assert all(arm["mean_ic"] is None for arm in week["ic"].values())


@pytest.mark.parametrize("kind", ["duplicate_ticker", "duplicate_day", "unsorted_day", "weekend"])
def test_price_identity_failures_raise(kind):
    px = _prices()
    if kind == "duplicate_ticker": px.columns = ["A", "A", "C"]
    if kind == "duplicate_day": px = pd.concat([px.iloc[:1], px])
    if kind == "unsorted_day": px = px.iloc[::-1]
    if kind == "weekend": px.index = pd.bdate_range("2026-09-22", periods=len(px)) + pd.Timedelta(days=1)
    with pytest.raises(ValueError):
        F.labels(px, effective_at="2026-09-22T12:00:00Z", sessions=5, acquired_at="2026-10-23T21:00:00Z", evaluated_at="2026-10-23T21:01:00Z")


@pytest.mark.parametrize("max_per_run,allow", [(12, False), (0, True)])
def test_refresh_disabled_policy_does_not_even_construct_client(monkeypatch, max_per_run, allow):
    monkeypatch.setattr(strategic, "detect_triggers", lambda *args: {"A": ["fixture"]})
    monkeypatch.setattr(strategic, "_client", lambda: pytest.fail("client must not be constructed"))
    result = strategic.refresh(pd.DataFrame(), pd.DataFrame(), pd.Timestamp("2026-09-22"),
                               max_per_run=max_per_run, allow_llm_refresh=allow)
    assert result["status"] == "disabled_by_policy" and result["queued"] == 1
    assert result["client_available"] is None and result["assessed"] == []


def test_assessment_versions_never_overwrite_original_and_collision_leaves_latest_intact(tmp_path, monkeypatch):
    monkeypatch.setattr(strategic, "ASSESS_DIR", tmp_path)
    a = _assessment(); strategic.save_assessment(a)
    raw = (tmp_path / "history/A_v1.json").read_bytes()
    b = _assessment(.8); strategic.save_assessment(b)
    assert b["version"] == 2 and (tmp_path / "history/A_v1.json").read_bytes() == raw
    latest = (tmp_path / "A.json").read_bytes()
    (tmp_path / "history/A_v3.json").write_text('{"incompatible":true}')
    with pytest.raises(ValueError, match="immutable"):
        strategic.save_assessment(_assessment(.9))
    assert (tmp_path / "A.json").read_bytes() == latest


def test_legacy_live_ic_unknown_clock_cannot_use_same_day_close():
    px = _prices()
    assert live_ic.forward_returns(px, "2026-09-22", 5) is None
    r = live_ic.forward_returns(px, "2026-09-22", 5, generated_at="2026-09-22T21:00:00Z", acquired_at="2026-10-23T21:00:00Z", evaluated_at="2026-10-23T21:01:00Z")
    expected = px.loc["2026-09-30"] / px.loc["2026-09-23"] - 1
    pd.testing.assert_series_equal(r, expected)


def test_legacy_live_ic_replaces_two_period_iid_t_stat_with_explicit_unavailable(tmp_path, monkeypatch):
    history = tmp_path / "history"; history.mkdir(); (history / "runs").mkdir()
    latest = tmp_path / "latest"; latest.mkdir()
    rows = []
    for date, rid in [("2026-09-22", "r1"), ("2026-09-23", "r2")]:
        rows += [{"ticker": f"T{i}", "date": date, "run_id": rid, "eligible": True, "composite": float(i)} for i in range(25)]
    pd.DataFrame(rows).to_csv(history / "universe_panel.csv", index=False)
    (history / "runs/index.json").write_text(json.dumps([{"run_id": r, "generated_at": d + "T12:00:00Z", "status": "approved"}
                                                        for d, r in [("2026-09-22", "r1"), ("2026-09-23", "r2")]]))
    idx = _prices().index
    px = pd.DataFrame({f"T{i}": np.linspace(100, 101 + i, len(idx)) for i in range(25)}, index=idx)
    monkeypatch.setattr(live_ic, "HISTORY", history); monkeypatch.setattr(live_ic, "LATEST", latest)
    result = live_ic.compute(px, acquired_at="2026-10-23T21:00:00Z", evaluated_at="2026-10-23T21:01:00Z")
    assert result["ic"]["1w"]["composite"]["n_periods"] == 2
    assert result["ic"]["1w"]["composite"]["t_stat"] is None
    assert result["inference_status"] == "DESCRIPTIVE_ONLY"
