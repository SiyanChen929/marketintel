from __future__ import annotations

import copy

import pytest

from market_intel.platform.risk_adapter import (
    StockSelectRiskAdapterError,
    StockSelectRiskLoader,
)


def request():
    return {
        "schema_version": "platform.risk.request.v1",
        "binding": {
            "schema_version": "platform.risk.binding.v1",
            "run_id": "run-001",
            "context_hash": "1" * 64,
            "as_of": "2026-09-03T14:00:00+00:00",
            "strategy_id": "stock_select",
            "strategy_version": "0.1.0",
            "portfolio_id": "portfolio-001",
        },
        "run": {
            "run_id": "run-001",
            "portfolio_id": "portfolio-001",
            "portfolio": {"construction": "risk_parity"},
        },
        "required_metrics": {
            "var_99_1d": {
                "window": "3y",
                "window_sessions": 756,
                "horizon_days": 1,
                "confidence_level": 0.99,
            },
            "var_99_10d": {
                "window": "3y",
                "window_sessions": 756,
                "horizon_days": 10,
                "confidence_level": 0.99,
            },
            "es_97_5": {
                "window": "3y",
                "window_sessions": 756,
                "horizon_days": 10,
                "confidence_level": 0.975,
            },
            "tracking_error": {
                "window": "3y",
                "window_sessions": 756,
            },
        },
    }


def full_lineage(_result, source, _layer, _window):
    assert source == "recommended"
    return {
        "data_snapshot_hash": "a" * 64,
        "portfolio_weights_hash": "b" * 64,
        "calculator_hash": "c" * 64,
        "calculation_sample_hash": "d" * 64,
        "asof": "2026-09-02",
        "acquired_at": "2026-09-03T13:59:00+00:00",
        "finality": "confirmed close from acquisition-time rule",
        "holding_first_bars": {"ALAB": "2024-01-19", "SNDK": "2025-02-13"},
    }


def result(*, window_available=True):
    window = {
        "requested_sessions": 756,
        "available": window_available,
        "span_start": "2023-08-29",
        "span_end": "2026-09-02",
        "calculation_sample_hash": "d" * 16 if window_available else None,
    }
    if window_available:
        window.update(
            {
                "start": "2023-08-29",
                "end": "2026-09-02",
                "n_sessions_used": 756,
                "horizons": {
                    "1": {
                        "overlapping": {
                            "var_99": 0.031,
                            "es_975": 0.039,
                            "n_obs": 755,
                        }
                    },
                    "10": {
                        "overlapping": {
                            "var_99": 0.087,
                            "es_975": 0.091,
                            "n_obs": 746,
                        },
                        "estimate_uncertainty": {
                            "tier": "official",
                            "primary_method": "asset_shock_bootstrap",
                            "var_99": {
                                "available": True,
                                "ci": 0.90,
                                "lo": 0.072,
                                "hi": 0.103,
                                "block_length": 9,
                                "reps": 2000,
                                "seed": 20260903,
                                "method": "asset-shock block bootstrap",
                            },
                        },
                    },
                },
                "tracking_error": [
                    {
                        "benchmark": "mandate benchmark",
                        "kind": "formal mandate",
                        "available": False,
                        "reason": "not configured",
                    },
                    {
                        "benchmark": "equal-weight current-universe proxy",
                        "kind": "research proxy",
                        "source": "fixture universe",
                        "available": True,
                        "tracking_error_ann": 0.121,
                        "n_days": 755,
                    },
                    {
                        "benchmark": "SOXX index proxy",
                        "kind": "research proxy",
                        "source": "SOXX ETF close series",
                        "available": True,
                        "tracking_error_ann": 0.142,
                        "n_days": 755,
                        "definition": "annualized sample standard deviation of active return",
                    },
                ],
            }
        )
    else:
        window.update(
            {
                "horizons": {},
                "n_sessions_available": 389,
                "n_trailing_uninterrupted_sessions": 3,
                "limiting_holding": "SNDK",
                "limiting_first_bar": "2025-02-13",
                "holdings_without_full_span": ["ALAB", "SNDK"],
                "reason": (
                    "2 holding(s) do not have structural history from the requested "
                    "span start 2023-08-29; SNDK first prices 2025-02-13. "
                    "The window is not shortened and no sample is substituted."
                ),
            }
        )
    return {
        "available": True,
        "construction": "recommended",
        "layer": "actual_run",
        "window": "3y",
        "precision_tier": "official",
        "config_hash": "e" * 16,
        "run": {
            "run_id": "run-001",
            "portfolio_id": "portfolio-001",
            "construction": "risk_parity",
        },
        "risk": {
            "layers": {
                "actual_run": {
                    "available": True,
                    "layer": {
                        "is_actual_run": True,
                        "basis": "landed run weights read verbatim",
                    },
                    "session_index": {
                        "finality": {
                            "confirmed_through": "2026-09-02",
                            "acquired_at": "2026-09-03T13:59:00+00:00",
                            "acquired_after_close": False,
                            "wall_clock_independent": True,
                            "rule": "acquisition-time rule",
                        }
                    },
                    "provenance": {
                        "data_snapshot_hash": "a" * 16,
                        "portfolio_weights_hash": "b" * 16,
                        "calculator_code_hash": "c" * 16,
                        "asof_last_confirmed_close": "2026-09-02",
                        "acquired_at": "2026-09-03T13:59:00+00:00",
                        "finality_basis": "acquisition-time rule",
                        "reconciliation": None,
                    },
                    "windows": {"3y": window},
                }
            }
        },
    }


def loader(fake_result, calls):
    def build(name, **kwargs):
        calls.append((name, kwargs))
        return fake_result

    return StockSelectRiskLoader(
        build_construction=build,
        enumerate_constructions=lambda: {
            "recommended": {"is_canonical_executable": True},
            "risk_parity": {"is_run_primary": True},
        },
        lineage_loader=full_lineage,
        official_bootstrap_reps=2000,
    )


def test_mutable_adapter_exposes_scope_but_withholds_available_metrics():
    calls = []
    payload = loader(result(), calls)(request())

    assert calls == [
        (
            "recommended",
            {
                "layer": "actual_run",
                "window": "3y",
                "bootstrap_reps": 2000,
                "force": False,
            },
        )
    ]
    assert payload["available"] is False
    assert payload["metrics"] == {}
    assert payload["unavailable"]["code"] == "FROZEN_RISK_INPUTS_UNVERIFIED"
    assert payload["lineage"]["complete"] is False
    assert payload["lineage"]["frozen_inputs_consumed"] is False
    assert payload["construction"]["name"] == "risk_parity"
    assert payload["construction"]["source_construction"] == "recommended"
    assert payload["construction"]["layer"] == "actual_run"
    assert payload["construction"]["weight_identity"] == "b" * 64
    assert payload["construction"]["basis"]["algorithm_identity"] == "run.portfolio.construction"


def test_adapter_preserves_three_year_unavailability_without_fallback():
    calls = []
    payload = loader(result(window_available=False), calls)(request())

    assert calls[0][0] == "recommended"
    assert calls[0][1]["window"] == "3y"
    assert calls[0][1]["layer"] == "actual_run"
    assert payload["available"] is False
    assert payload["metrics"] == {}
    assert payload["unavailable"]["code"] == "INSUFFICIENT_STRUCTURAL_HISTORY"
    assert payload["unavailable"]["no_shorter_window_or_substitute"] is True
    assert {row["ticker"] for row in payload["unavailable"]["limiting_holdings"]} == {
        "ALAB",
        "SNDK",
    }
    first_bars = {
        row["ticker"]: row["first_bar"]
        for row in payload["unavailable"]["limiting_holdings"]
    }
    assert first_bars == {"ALAB": "2024-01-19", "SNDK": "2025-02-13"}
    assert payload["lineage"]["run_id"] == "run-001"
    assert payload["lineage"]["context_hash"] == "1" * 64
    assert payload["lineage"]["portfolio_weights_hash"] == "b" * 64


def test_adapter_fails_if_workbench_silently_returns_another_window():
    bad = result()
    bad["window"] = "1y"
    with pytest.raises(StockSelectRiskAdapterError, match="different window"):
        loader(bad, [])(request())


def test_adapter_never_uses_run_primary_model_column_as_actual_run():
    used = []

    def build(name, **kwargs):
        used.append((name, kwargs))
        if name == "risk_parity":
            raise AssertionError("model risk_parity column must not be used as actual landed weights")
        return result(window_available=False)

    adapter = StockSelectRiskLoader(
        build_construction=build,
        enumerate_constructions=lambda: {
            "recommended": {"is_canonical_executable": True},
            "risk_parity": {"is_run_primary": True},
        },
        lineage_loader=full_lineage,
        official_bootstrap_reps=2000,
    )
    adapter(request())
    assert used[0][0] == "recommended"
    assert used[0][1]["layer"] == "actual_run"


def test_adapter_withholds_metrics_when_official_interval_excludes_point():
    bad = result()
    ci = bad["risk"]["layers"]["actual_run"]["windows"]["3y"]["horizons"]["10"][
        "estimate_uncertainty"
    ]["var_99"]
    ci.update({"lo": 0.09, "hi": 0.10})
    payload = loader(bad, [])(request())
    assert payload["available"] is False
    assert payload["metrics"] == {}
    assert payload["unavailable"]["code"] == "CONFIDENCE_INTERVAL_EXCLUDES_ESTIMATE"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update({"construction": "risk_parity"}),
        lambda r: r.update({"layer": "model"}),
        lambda r: r.update({"precision_tier": "preview"}),
        lambda r: r["run"].update({"run_id": "other-run"}),
        lambda r: r["risk"]["layers"]["actual_run"]["layer"].update(
            {"is_actual_run": False}
        ),
    ],
)
def test_adapter_rejects_scope_or_identity_drift(mutate):
    bad = copy.deepcopy(result())
    mutate(bad)
    with pytest.raises(StockSelectRiskAdapterError):
        loader(bad, [])(request())
