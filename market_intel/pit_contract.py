"""Point-in-time data contract for historical simulation.

A backtest over a *current* constituent list, using *current* share counts and with no delisting record,
cannot support a return claim: the universe is chosen with hindsight, shares are back-projected from today's
market cap, and companies that failed have already been removed from the sample. Those are not conservative
approximations - they bias the result upward by construction, and no amount of "all arms share the bias"
makes the level meaningful.

This module states the requirements, **validates** the files that claim to satisfy them, and returns a gate.
Existence is not satisfaction: a file is accepted only if its schema, date coverage, vintage and identifiers
all check out. A gate opened by an empty-but-present or malformed file would be worse than a closed one,
because it would license a claim while changing nothing about the simulation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import config

#: Where each dataset would live once licensed, and what it must contain to count.
REQUIREMENTS = {
    "pit_universe": {
        "what": "index/universe membership with effective and removal dates",
        "why": "selecting from today's constituents is survivorship bias: failed and delisted names were never eligible to be picked",
        "path": config.PROJECT_ROOT / "data" / "pit" / "universe_membership.csv",
        "columns": ["ticker", "effective_from", "effective_to", "source"],
        "id_column": "ticker",
        "date_columns": ["effective_from"],
        "coverage_column": "effective_from",
        "consumed_as": "the eligible selection set at each rebalance date",
    },
    "historical_shares": {
        "what": "shares outstanding as reported at each date",
        "why": "deriving shares from today's market cap divided by today's price back-projects present-day capitalisation into the past, which is future information",
        "path": config.PROJECT_ROOT / "data" / "pit" / "shares_outstanding.csv",
        "columns": ["ticker", "date", "shares", "source"],
        "id_column": "ticker",
        "date_columns": ["date"],
        "coverage_column": "date",
        "consumed_as": "market capitalisation for the equilibrium prior and cap weighting at each rebalance date",
    },
    "delisting": {
        "what": "delisting dates and final proceeds / corporate-action treatment",
        "why": "without it a failed position simply disappears from the panel, which books an implicit zero-loss exit",
        "path": config.PROJECT_ROOT / "data" / "pit" / "delistings.csv",
        "columns": ["ticker", "delist_date", "reason", "final_price", "source"],
        "id_column": "ticker",
        "date_columns": ["delist_date"],
        "coverage_column": "delist_date",
        "consumed_as": "the exit price and date of a position whose bars stop",
    },
    "benchmark_weights": {
        "what": "official benchmark constituents and weights over time",
        "why": "an equal-weight proxy is not the mandate benchmark; active risk against it is not the mandate's active risk",
        "path": config.PROJECT_ROOT / "data" / "pit" / "benchmark_weights_history.csv",
        "columns": ["date", "ticker", "weight", "source"],
        "id_column": "ticker",
        "date_columns": ["date"],
        "coverage_column": "date",
        "consumed_as": "the mandate benchmark's own weights at each date",
    },
}

MIN_ROWS = 2


def validate(key: str, spec: dict, window: tuple[str, str] | None = None,
             tickers: list[str] | None = None) -> dict:
    """Whether one file actually satisfies its requirement.

    Checks, in order: presence, readability, schema, non-triviality, parseable dates, vintage (an ``as_of`` or
    ``source`` provenance column), identifier overlap with the universe being simulated, and date coverage of
    the requested window. Any failure closes the requirement and says which check failed - a file is never
    accepted merely for existing.
    """
    path: Path = spec["path"]
    out = {"what": spec["what"], "why_required": spec["why"], "expected_path": str(path),
           "expected_columns": spec["columns"], "consumed_as": spec["consumed_as"],
           "present": False, "valid": False, "checks": {}, "failed_check": None}
    ck = out["checks"]
    ck["file_present"] = path.exists() and path.stat().st_size > 0
    if not ck["file_present"]:
        out["failed_check"] = "file_present"
        out["reason"] = f"{path} is absent or empty"
        return out
    out["present"] = True
    try:
        df = pd.read_csv(path)
    except Exception as e:
        ck["readable"] = False
        out["failed_check"] = "readable"
        out["reason"] = f"unreadable: {e}"
        return out
    ck["readable"] = True
    missing_cols = [c for c in spec["columns"] if c not in df.columns]
    ck["schema"] = not missing_cols
    if missing_cols:
        out["failed_check"] = "schema"
        out["reason"] = f"missing required columns: {', '.join(missing_cols)}"
        return out
    ck["non_trivial"] = len(df) >= MIN_ROWS
    if not ck["non_trivial"]:
        out["failed_check"] = "non_trivial"
        out["reason"] = f"only {len(df)} rows; a present-but-empty file must not open the gate"
        return out
    parsed = {}
    for c in spec["date_columns"]:
        d = pd.to_datetime(df[c], errors="coerce", format="mixed")
        parsed[c] = d
        if d.notna().sum() == 0:
            ck["dates_parse"] = False
            out["failed_check"] = "dates_parse"
            out["reason"] = f"no parseable dates in {c}"
            return out
    ck["dates_parse"] = True
    ck["vintage"] = bool(df["source"].notna().any()) if "source" in df.columns else False
    if not ck["vintage"]:
        out["failed_check"] = "vintage"
        out["reason"] = "no provenance/vintage in the source column; an unattributed file cannot be point-in-time evidence"
        return out
    if tickers:
        ids = set(df[spec["id_column"]].astype(str).str.upper().str.strip())
        overlap = ids & {str(t).upper() for t in tickers}
        ck["identifiers"] = len(overlap) > 0
        out["identifier_overlap"] = len(overlap)
        if not ck["identifiers"]:
            out["failed_check"] = "identifiers"
            out["reason"] = "no identifier in this file matches the simulated universe"
            return out
    cov_col = spec["coverage_column"]
    d = parsed[cov_col].dropna()
    out["coverage"] = {"start": str(d.min().date()), "end": str(d.max().date()), "n_rows": int(len(df))}
    if window:
        w0, w1 = pd.Timestamp(window[0]), pd.Timestamp(window[1])
        ck["coverage"] = bool(d.min() <= w0 and d.max() >= w1)
        if not ck["coverage"]:
            out["failed_check"] = "coverage"
            out["reason"] = (f"covers {d.min().date()}..{d.max().date()} but the simulation needs "
                             f"{w0.date()}..{w1.date()}")
            return out
    out["valid"] = True
    return out


@dataclass(frozen=True)
class Gate:
    """Whether a historical performance claim is admissible, and why not when it is not."""
    alpha_claim_available: bool
    mechanism_diagnostic_available: bool
    satisfied: list
    missing: list
    detail: dict

    def to_dict(self) -> dict:
        return {"alpha_claim_available": self.alpha_claim_available,
                "mechanism_diagnostic_available": self.mechanism_diagnostic_available,
                "satisfied": self.satisfied, "missing": self.missing, "requirements": self.detail,
                "validation_basis": "schema, non-triviality, parseable dates, vintage, identifier overlap and "
                                    "date coverage are all checked; file existence alone never opens the gate",
                "headline": ("historical performance claim ADMISSIBLE" if self.alpha_claim_available else
                             "historical performance claim WITHHELD - " + ", ".join(self.missing) + " unavailable"),
                "permitted_use": (["performance", "alpha", "risk", "mechanism"] if self.alpha_claim_available
                                  else ["mechanism"]),
                "forbidden_use": ([] if self.alpha_claim_available else
                                  ["reported or implied historical return", "alpha", "Sharpe as a track record",
                                   "position sizing", "risk limits"])}


def check(now=None, window: tuple[str, str] | None = None, tickers: list[str] | None = None) -> Gate:
    """Validate every requirement and return the gate.

    ``window`` and ``tickers`` describe the simulation being gated, so coverage and identifier checks are
    against what the caller actually intends to run rather than against nothing in particular.
    """
    satisfied, missing, detail = [], [], {}
    for key, spec in REQUIREMENTS.items():
        res = validate(key, spec, window, tickers)
        detail[key] = res
        (satisfied if res["valid"] else missing).append(key)
    # an alpha claim needs the three that bias the level; the benchmark file only bounds active-risk claims
    level_critical = [k for k in ("pit_universe", "historical_shares", "delisting") if k in missing]
    return Gate(alpha_claim_available=not level_critical, mechanism_diagnostic_available=True,
                satisfied=satisfied, missing=missing, detail=detail)


def load(key: str) -> pd.DataFrame | None:
    """Read one validated PIT dataset. Returns None when the requirement is not satisfied."""
    spec = REQUIREMENTS[key]
    res = validate(key, spec)
    if not res["valid"]:
        return None
    df = pd.read_csv(spec["path"])
    for c in spec["date_columns"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df[spec["id_column"]] = df[spec["id_column"]].astype(str).str.upper().str.strip()
    return df
