"""Read frozen Commodity/MSCI evidence without refitting either strategy.

Capital/exposure observations retain their source timing and denominator.  These
profiles are research diagnostics, never execution or margin availability proof.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


class FrozenEvidence:
    def __init__(self, artifact, archive):
        self.artifact = artifact
        self.archive = Path(archive)
        self.sources = []

    def read(self, path, expected):
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
            raise ValueError("source needs a frozen SHA-256")
        target = self.archive / expected
        raw = target.read_bytes() if target.exists() else Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"frozen source checksum mismatch: {Path(path).name}")
        self.archive.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            try:
                with target.open("xb") as handle:
                    handle.write(raw)
            except FileExistsError:
                if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                    raise ValueError("frozen archive checksum mismatch")
        item = {"name": str(path), "sha256": expected}
        if item not in self.sources:
            self.sources.append(item)
        return raw

    def primary(self, name):
        matches = [s for s in self.artifact["sources"] if Path(s["name"]).name == name]
        if len(matches) != 1:
            raise ValueError(f"frozen performance artifact must identify exactly one {name}")
        source = matches[0]
        return Path(source["name"]), self.read(source["name"], source["sha256"])

    def extra(self, root, manifest, name):
        # Only manifest-bound, root-relative artifacts may extend the snapshot.
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("manifest paths must stay inside the source root")
        matches = [s for s in manifest["files"] if s["path"] == name]
        if len(matches) != 1:
            raise ValueError(f"source manifest must identify exactly one {name}")
        return self.read(root / name, matches[0]["sha256"])


def _frame(raw):
    return pd.read_csv(io.BytesIO(raw))


def _dates(frame, column):
    d = pd.to_datetime(frame[column], errors="raise")
    if d.isna().any() or d.duplicated().any() or not d.is_monotonic_increasing:
        raise ValueError("source dates must be present, sorted and unique")
    return d.dt.strftime("%Y-%m-%d").tolist()


def _numbers(frame, column, nonnegative=False):
    values = pd.to_numeric(frame[column], errors="raise").to_numpy(float)
    if not np.isfinite(values).all() or (nonnegative and (values < 0).any()):
        raise ValueError(f"invalid source {column}")
    return values


def _nav(frame, date, column, initial):
    dates = _dates(frame, date)
    closing = _numbers(frame, column)
    opening = np.r_[initial, closing[:-1]]
    if not np.isfinite(initial) or initial <= 0 or (closing <= 0).any():
        raise ValueError("positive, finite source NAV is required")
    return dates, opening, closing, closing / opening - 1


def _reconcile_months(rows, artifact, events=False):
    grouped = {}
    for row in rows:
        month = row["date"][:7]
        grouped[month] = grouped.get(month, 1.0) * (1 + row["return"])
    for row in artifact["rows"]:
        if row["month"] not in grouped and not events:
            raise ValueError("activity profile is missing a frozen performance month")
        reconstructed = grouped.get(row["month"], 1.0) - 1
        if not np.isclose(reconstructed, row["return"], rtol=0, atol=1e-10):
            raise ValueError("activity profile does not reconcile with frozen monthly NAV")


def _commodity(s, artifact):
    manifest_path, raw = s.primary("manifest.json")
    manifest = json.loads(raw)
    daily_path, raw = s.primary("stitched_daily.csv")
    expected = [f["sha256"] for f in manifest["files"] if f["path"] == "stitched_daily.csv"]
    if expected != [hashlib.sha256(raw).hexdigest()] or daily_path.parent != manifest_path.parent:
        raise ValueError("commodity daily source does not match its frozen manifest")
    d = _frame(raw)
    initial_series = _numbers(d, "initial_capital_cny")
    initial = float(initial_series[0])
    if not (initial_series == initial).all():
        raise ValueError("commodity fixed initial capital must be constant")
    dates, opening, closing, returns = _nav(d, "date", "equity_cny", initial)
    pnl = _numbers(d, "net_pnl_cny")
    if not np.allclose(closing, initial + np.cumsum(pnl), rtol=0, atol=1e-6):
        raise ValueError("commodity NAV does not reconcile with cumulative net P&L")
    if not np.allclose(_numbers(d, "net_return"), pnl / initial, rtol=0, atol=1e-10):
        raise ValueError("commodity fixed-capital source returns do not reconcile")
    gross = _numbers(d, "gross_notional_cny", True)
    margin = _numbers(d, "margin_cny", True)
    costs = _numbers(d, "transaction_cost_cny", True)
    carried_gross, carried_margin = np.r_[0.0, gross[:-1]], np.r_[0.0, margin[:-1]]
    rows = [{"date": date, "return": float(returns[i]),
             "active": bool(carried_gross[i] > 0 or gross[i] > 0 or costs[i] > 0),
             "gross_open": float(carried_gross[i] / opening[i]),
             "gross_close": float(gross[i] / closing[i]),
             "margin_open": float(carried_margin[i] / opening[i]),
             "margin_close": float(margin[i] / closing[i]),
             "net_pnl": float(pnl[i]), "nav_open": float(opening[i]), "nav_close": float(closing[i]),
             "event_id": None} for i, date in enumerate(dates)]
    _reconcile_months(rows, artifact)
    trades = _frame(s.extra(manifest_path.parent, manifest, "stitched_trades.csv"))
    trade_rows = []
    for i, t in trades.iterrows():
        entry, exit_ = pd.Timestamp(t["entry_date"]), pd.Timestamp(t["exit_date"])
        if pd.isna(entry) or pd.isna(exit_) or exit_ < entry:
            raise ValueError("invalid commodity trade chronology")
        holding = float(t["holding_days"])
        if not np.isfinite(holding) or holding < 0 or not holding.is_integer():
            raise ValueError("invalid commodity holding-session count")
        trade_rows.append({"trade_id": f"{t['fold_id']}:{t['portfolio_slot']}:{t['trade_number']}",
                           "entry_date": entry.strftime("%Y-%m-%d"), "exit_date": exit_.strftime("%Y-%m-%d"),
                           "holding_sessions": int(holding), "calendar_days": int((exit_ - entry).days),
                           "organic_completed": bool(t["organic_completed"]),
                           "basket_id": str(t["basket_id"]),
                           "portfolio_net_pnl": float(t["portfolio_net_pnl_contribution_cny"])})
    if len({t["trade_id"] for t in trade_rows}) != len(trade_rows):
        raise ValueError("commodity trade identities are not unique")
    return {"frequency": "daily", "capital_basis": "CNY source NAV; net P&L divided by prior NAV, not fixed-initial-capital returns compounded",
            "margin_basis": "Source modeled, unnetted post-trade margin in CNY divided by contemporaneous NAV; not broker collateral or available capital",
            "exposure_basis": "Close fields are the current session's post-trade quantities marked at source opening prices, divided by ending NAV. Open fields carry the prior session's snapshot divided by opening NAV. Neither is an intraday peak or a current close-price valuation.",
            "activity_basis": "Prior or current positive gross notional, or positive transaction cost; zero P&L never implies idle capital.",
            "pnl_unit": "CNY", "rows": rows, "trade_observations": trade_rows,
            "limitations": ["Daily snapshots do not establish intraday or overnight collateral peaks, variation-margin calls, liquidity buffers, or deployable shared cash.",
                            "Continuous-dominant futures proxy and source margin assumptions; no effective-dated broker margin or cross-sleeve offsets.",
                            "Basket trades overlap in time and underlying contracts; trade count does not prove statistical independence.",
                            "Exposure and margin snapshots are diagnostic denominators, not alternative self-financing NAVs. Do not compound P&L divided by average margin."]}


def _msci(s, artifact):
    s.primary("first_minute_alpha_run_manifest.json")
    _, raw = s.primary("first_minute_alpha_portfolio.csv")
    d = _frame(raw)
    match = re.search(r"·\s*([0-9.]+)\s*bps$", artifact["variant"])
    if not match:
        raise ValueError("MSCI artifact does not identify its frozen cost scenario")
    cost = float(match.group(1))
    d = d.loc[d.cost_bps.eq(cost)].copy()
    if d.empty:
        raise ValueError("MSCI frozen cost scenario is absent")
    dates, opening, closing, returns = _nav(d, "effective_close_date", "equity", 1.0)
    exposure = _numbers(d, "gross_exposure", True)
    if not np.allclose(returns, _numbers(d, "net_return"), rtol=0, atol=1e-10):
        raise ValueError("MSCI event NAV does not reconcile with net returns")
    if not np.allclose(_numbers(d, "gross_return") - exposure * cost / 10000, returns, rtol=0, atol=1e-10):
        raise ValueError("MSCI gross exposure and cost do not reconcile")
    names = _numbers(d, "n_names", True)
    if not np.all(names == np.floor(names)):
        raise ValueError("MSCI security-event counts must be integers")
    rows = [{"date": date, "return": float(returns[i]), "active": bool(exposure[i] > 0),
             "gross_open": float(exposure[i]), "gross_close": 0.0,
             "margin_open": None, "margin_close": None,
             "net_pnl": float(closing[i] - opening[i]),
             "nav_open": float(opening[i]), "nav_close": float(closing[i]),
             "event_id": date, "security_events": int(names[i])} for i, date in enumerate(dates)]
    _reconcile_months(rows, artifact, events=True)
    return {"frequency": "event", "capital_basis": "Source event NAV normalized to 1 initially; reserved-capital event return, no dollar account or capital reuse",
            "margin_basis": "unavailable", "pnl_unit": "normalized source NAV units",
            "exposure_basis": "Source event gross notional / event opening NAV; additions and deletions each receive 50%, an absent side stays cash. Close is flat under the source same-event exit contract. Margin is unknown.",
            "activity_basis": "One observation per recorded review date; no daily calendar is fabricated. Security-event counts share the same review-date shock.",
            "holding_period": "Open of the 15:49 start-stamped minute bar to close of the 15:50 start-stamped minute bar (the latter covers through 15:50:59); approximately two minutes of bar coverage. Actual execution timestamps are unavailable.",
            "rows": rows,
            "limitations": ["Event dates, not securities or idle months, are the independent sampling unit for event-return inference.",
                            "No source dollar notional, executable borrow, margin occupancy, or actual fill timestamps; a same-event bar window is not a capital-reservation window.",
                            "Event calendar completeness is source-declared. Missing calendar days are not synthesized by this event profile.",
                            "Minute-bar trade-price proxies and post-hoc window selection remain NOT VALIDATED; do not annualize active-minute returns or infer safely reusable capital."]}


def build_event_profile(recipe, artifact, archive_dir):
    """Return a source-bound activity profile for one frozen return artifact."""
    expected = {"commodity": "commodity_arbitrage", "msci": "msci_rebalance"}
    if recipe not in expected or artifact.get("strategy_id") != expected[recipe]:
        raise ValueError("activity recipe does not match the frozen strategy")
    s = FrozenEvidence(artifact, archive_dir)
    for source in artifact["sources"]:
        s.read(source["name"], source["sha256"])
    profile = (_commodity if recipe == "commodity" else _msci)(s, artifact)
    return {"schema_version": "platform.event-activity.v1", "return_artifact_id": artifact["artifact_id"],
            "strategy_id": artifact["strategy_id"], "validation_status": "NOT VALIDATED",
            "sources": s.sources, **profile}
