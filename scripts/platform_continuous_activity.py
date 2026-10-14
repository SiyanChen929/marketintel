"""Read-only, hash-pinned activity measurements for imported Vol and PS NAVs.

This adapter adds measurements to already revealed artifacts. It does not run
the strategy, change trading rules, infer idle days from returns, or supply
broker margin. Extra evidence is frozen into the existing source archive.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd


_EPS = 1e-12
# This implementation's close-to-close accounting was independently inspected.
# A changed generator must be reviewed before its EOD exposure can be lagged.
_PS_CLOCK_SHA = "a7a92af72dfb8b1d2619b681327aeba6dff384b0fd548350e115243361f17c94"


class _Evidence:
    def __init__(self, artifact, archive_dir):
        self.archive = Path(archive_dir)
        self.sources = []
        self.raw = {}
        for source in artifact["sources"]:
            self.raw[source["name"]] = self.read(source["name"], source["sha256"])

    def read(self, name, expected):
        if any(part.lower().startswith("sealed") for part in Path(name).parts):
            raise ValueError("sealed sources cannot supply revealed activity profiles")
        if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise ValueError("activity source requires a SHA-256 checksum")
        target = self.archive / expected
        raw = target.read_bytes() if target.exists() else Path(name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"activity source checksum mismatch: {Path(name).name}")
        self.archive.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            try:
                with target.open("xb") as handle:
                    handle.write(raw)
            except FileExistsError:
                pass
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("activity source archive checksum mismatch")
        source = {"name": str(name), "sha256": expected}
        if source not in self.sources:
            self.sources.append(source)
        return raw

    def named(self, basename):
        found = [(name, raw) for name, raw in self.raw.items() if Path(name).name == basename]
        if len(found) != 1:
            raise ValueError(f"exactly one frozen {basename} is required")
        return found[0]


def _daily_frame(frame, *, nav, returns, initial):
    d = frame.copy()
    d["date"] = pd.to_datetime(d["date"], errors="raise")
    if d.empty or d.date.isna().any() or d.date.duplicated().any() or not d.date.is_monotonic_increasing:
        raise ValueError("daily source dates must be nonempty, sorted, and unique")
    if not (d.date == d.date.dt.normalize()).all():
        raise ValueError("daily source dates must identify source sessions")
    levels = d[nav].to_numpy(float)
    previous = np.r_[float(initial), levels[:-1]]
    if not np.isfinite(levels).all() or not np.isfinite(previous).all() or (levels <= 0).any() or (previous <= 0).any():
        raise ValueError("daily activity NAV must be finite and positive")
    r = levels / previous - 1.0
    if not np.isfinite(d[returns]).all() or not np.allclose(r, d[returns], atol=1e-10, rtol=0):
        raise ValueError("daily activity returns do not reconcile with NAV")
    d["nav_open"], d["nav_close"], d["return"] = previous, levels, r
    return d.set_index("date")


def _positive_finite(frame, columns):
    for col in columns:
        a = frame[col].to_numpy(float)
        if not np.isfinite(a).all() or (a < -_EPS).any():
            raise ValueError(f"activity {col} must be finite and nonnegative")


def _finish(artifact, evidence, d, *, capital_basis, limitations):
    months = {row["month"]: float(row["return"]) for row in artifact["rows"]}
    d = d.loc[d.index.strftime("%Y-%m").isin(months)].copy()
    aggregate = d.groupby(d.index.strftime("%Y-%m"))["return"].apply(lambda r: float(np.prod(1 + r) - 1))
    if set(aggregate.index) != set(months) or any(abs(aggregate[m] - r) > 1e-10 for m, r in months.items()):
        raise ValueError("activity daily returns do not reconstruct the frozen monthly artifact")
    _positive_finite(d, ["gross_open", "gross_close"])
    rows = [
        {"date": date.strftime("%Y-%m-%d"), "return": float(row["return"]),
         "active": bool(row["active"]), "gross_open": float(row["gross_open"]),
         "gross_close": float(row["gross_close"]), "margin_open": None,
         "margin_close": None, "net_pnl": float(row["nav_close"] - row["nav_open"]),
         "nav_open": float(row["nav_open"]), "nav_close": float(row["nav_close"]), "event_id": None}
        for date, row in d.iterrows()
    ]
    return {"return_artifact_id": artifact["artifact_id"], "strategy_id": artifact["strategy_id"],
            "frequency": "daily", "sources": evidence.sources, "capital_basis": capital_basis,
            "margin_basis": "unavailable", "limitations": list(artifact.get("limitations", [])) + limitations,
            "rows": rows}


def _vol(artifact, evidence):
    manifest_name, manifest_raw = evidence.named("manifest.json")
    manifest = json.loads(manifest_raw)
    ledger_name, raw = evidence.named("representative_ledger.parquet")
    frozen = {x["name"]: x for x in manifest["artifacts"]}
    if hashlib.sha256(raw).hexdigest() != frozen["representative_ledger.parquet"]["sha256"]:
        raise ValueError("Vol ledger does not match its frozen manifest")
    positions_name = Path(manifest_name).parent / "representative_positions.parquet"
    positions_raw = evidence.read(str(positions_name), frozen["representative_positions.parquet"]["sha256"])
    positions = pd.read_parquet(io.BytesIO(positions_raw))
    ledger = pd.read_parquet(io.BytesIO(raw))
    variants = {f"{name} · representative seed 0": name for name in ("VB2", "VB1", "BUY_HOLD")}
    if artifact["variant"] not in variants or set(ledger.seed_index.unique()) != {0}:
        raise ValueError("unsupported Vol representative variant")
    variant = variants[artifact["variant"]]
    ledger = ledger.loc[(ledger.strategy_id == variant) & (ledger.seed_index == 0)]
    levels = ledger.equity / ledger.nav
    if ledger.empty or not np.isfinite(levels).all() or not np.allclose(levels, levels.iloc[0], atol=1e-6, rtol=1e-12):
        raise ValueError("Vol source initial-capital normalization is inconsistent")
    d = _daily_frame(ledger, nav="equity", returns="daily_return", initial=levels.iloc[0])
    p = positions.loc[(positions.strategy_id == variant) & (positions.seed_index == 0)].copy()
    p["date"] = pd.to_datetime(p.date)
    if p.empty or p.duplicated(["date", "security_id"]).any() or set(p.date) != set(d.index):
        raise ValueError("Vol position ledger must cover each source session uniquely")
    columns = ["shares_before", "shares", "trade_shares", "open", "close", "trading_cost"]
    if not np.isfinite(p[columns].to_numpy(float)).all() or (p[["open", "close"]] <= 0).any().any():
        raise ValueError("Vol position marks are incomplete")
    _positive_finite(p, ["shares_before", "shares", "trading_cost"])
    if not np.allclose(p.shares_before + p.trade_shares, p.shares, atol=1e-8, rtol=1e-12):
        raise ValueError("Vol execution shares do not reconstruct closing holdings")
    p = p.sort_values(["security_id", "date"])
    previous_shares = p.groupby("security_id").shares.shift(1).fillna(0)
    if not np.allclose(p.shares_before, previous_shares, atol=1e-8, rtol=1e-12):
        raise ValueError("Vol opening shares do not reconcile with previous close holdings")
    prior_close = p.groupby("security_id").close.shift(1)
    if (p.shares_before.gt(_EPS) & prior_close.isna()).any():
        raise ValueError("Vol opening positions lack a prior close mark")
    p["opening_notional"] = (p.shares_before * prior_close).abs().fillna(0)
    p["closing_notional"] = (p.shares * p.close).abs()
    p["active"] = p.shares_before.abs().gt(_EPS) | p.shares.abs().gt(_EPS) | p.trade_shares.abs().gt(_EPS)
    p["net_pnl"] = p.shares_before * (p.open - prior_close).fillna(0) + p.shares * (p.close - p.open) - p.trading_cost
    g = p.groupby("date").agg(opening_notional=("opening_notional", "sum"), closing_notional=("closing_notional", "sum"), active=("active", "max"), reconstructed_pnl=("net_pnl", "sum"))
    d = d.join(g)
    if not np.allclose(d.nav_close - d.nav_open, d.reconstructed_pnl, atol=1e-6, rtol=1e-10):
        raise ValueError("Vol position PnL does not reconcile with daily NAV")
    d["gross_open"] = d.opening_notional / d.nav_open
    d["gross_close"] = d.closing_notional / d.nav_close
    if not np.allclose(d.gross_close, d.gross_exposure, atol=1e-10, rtol=0):
        raise ValueError("Vol closing position exposure does not reconcile")
    return _finish(artifact, evidence, d,
                   capital_basis="Long/cash funded equity; NAV in source USD. gross_open is prior-close shares valued at prior close / prior-close NAV. gross_close is after-open-fill holdings valued at current close / current NAV.",
                   limitations=["Vol activity uses actual pre-fill and post-fill shares and executions. The return contains overnight old-position PnL plus intraday post-opening-fill PnL. gross_open is not the current opening post-fill exposure and must not divide the entire daily return.",
                                "Margin, broker buying power and transferable idle cash are unavailable; gross notional is not a margin requirement.",
                                "Only source-declared daily sessions inside the frozen monthly artifact are included; no missing sessions are filled or inferred."])


def _ps(artifact, evidence):
    _, manifest_raw = evidence.named("reveal_manifest.json")
    manifest = json.loads(manifest_raw)
    _, raw = evidence.named("v10_portfolio_daily.csv.gz")
    if hashlib.sha256(raw).hexdigest() != manifest["output_files"]["portfolio_daily"]["sha256"]:
        raise ValueError("PS daily ledger does not match its frozen manifest")
    clock_source = manifest["source_hashes"]["v9_walkforward_module"]
    if clock_source["sha256"] != _PS_CLOCK_SHA:
        raise ValueError("PS accounting clock changed; review opening-exposure semantics before adapting")
    evidence.read(clock_source["path"], clock_source["sha256"])
    supported = ("joint_max_sharpe_top10", "matched_max_expected_return_top10", "strict_all_eligible_equal_weight", "v9_score_top10_reference")
    if artifact["variant"] not in supported:
        raise ValueError("unsupported PS portfolio variant")
    ledger = pd.read_csv(io.BytesIO(raw), compression="gzip")
    ledger = ledger.loc[ledger.portfolio_id == artifact["variant"]]
    d = _daily_frame(ledger, nav="equity", returns="portfolio_return", initial=1.0)
    _positive_finite(d, ["gross_exposure", "leg_turnover", "transaction_cost", "borrow_cost"])
    reconstructed_pnl = d.gross_pnl - d.transaction_cost - d.borrow_cost
    if not np.isfinite(reconstructed_pnl).all() or not np.allclose(d.nav_close - d.nav_open, reconstructed_pnl, atol=1e-10, rtol=0):
        raise ValueError("PS gross PnL, costs and borrow do not reconstruct daily NAV")
    # Frozen generator initializes zero legs; PnL uses previous close legs,
    # then today-close fills create the published gross_exposure.
    d["gross_open"] = d.gross_exposure.shift(1).fillna(0.0)
    d["gross_close"] = d.gross_exposure
    d["active"] = d.gross_open.gt(_EPS) | d.gross_close.gt(_EPS) | d.leg_turnover.gt(_EPS)
    return _finish(artifact, evidence, d,
                   capital_basis="Pair-segregated source NAV normalized to initial equity 1. gross_open is the prior close's post-fill gross notional / prior-close NAV; it earns today's close-to-close PnL. gross_close is today's post-fill exposure for next session.",
                   limitations=["PS opening exposure is lagged from a complete daily ledger under the hash-pinned reviewed generator; activity also includes actual leg turnover, so entry costs and closing-day risk are retained. Source nav_open denotes prior close, not the next session's opening market mark.",
                                "Pair sleeve capital, gross leg notional and broker margin differ. This source has no historical margin, locate or cross-strategy buying-power evidence; margin remains unavailable.",
                                "Initial research cash before the first selected pair is retained. Compare active-session risk alongside full-calendar risk; do not mechanically lever up a cash-diluted full-calendar volatility estimate.",
                                "Only source-declared daily sessions inside the frozen monthly artifact are included; no missing sessions are filled or inferred."])


def build_continuous_profile(recipe, artifact, archive_dir):
    """Build a NAV-reconciled daily profile for one frozen return artifact."""
    expected = {"vol": "vol_trading", "ps": "pairs_trading"}
    if recipe not in expected or artifact.get("strategy_id") != expected[recipe]:
        raise ValueError("activity recipe does not match the frozen strategy")
    evidence = _Evidence(artifact, archive_dir)
    return (_vol if recipe == "vol" else _ps)(artifact, evidence)
