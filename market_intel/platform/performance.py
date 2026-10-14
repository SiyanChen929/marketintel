"""Immutable research-return imports and auditable, monthly sleeve scenarios.

This is a return-level research adapter, independent of the execution engine.
No strategies are refit, no calendars are compressed, and no capital is netted
across sleeves. Importing a result does not validate its research or fills.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .service import PlatformBlocked, PlatformInputError, PlatformNotFound

SCHEMA = "platform.performance-import.v1"
CALCULATOR_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CATALOG = (
    ("stable_alpha", "Stable Alpha", "Awaiting an identified, unsealed portfolio NAV artifact; factor scores and synthetic runs cannot supply returns."),
    ("vol_trading", "Vol Trading", "Awaiting a frozen strategy return artifact."),
    ("commodity_arbitrage", "Commodity Arbitrage", "Awaiting a frozen futures portfolio NAV artifact."),
    ("msci_rebalance", "MSCI Rebalance", "Awaiting an event return artifact and explicit idle-capital convention."),
    ("pairs_trading", "PS Trading", "Awaiting a frozen pair-portfolio NAV artifact."),
    ("stock_select", "Stock Select", "Current target weights are connected. A comparable historical strategy NAV has not been imported."),
    ("alpha_engine", "Alpha Engine", "Awaiting a frozen portfolio NAV artifact."),
)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encode(value).encode()).hexdigest()


def month_number(month):
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise PlatformInputError("month must be YYYY-MM")
    y, m = map(int, month.split("-"))
    if not 1900 <= y <= 2200:
        raise PlatformInputError("month year must be 1900–2200")
    return y * 12 + m - 1


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlatformInputError(f"{name} must be a finite number")
    return float(value)


def validate_import(body):
    if body.get("schema_version") != SCHEMA:
        raise PlatformInputError(f"schema_version must be {SCHEMA}")
    metadata = {}
    for key in ("strategy_id", "name", "variant", "run_id", "currency", "asset_class", "calendar", "costs", "clock", "return_construction"):
        value = body.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 3000:
            raise PlatformInputError(f"{key} is required (1–3000 characters)")
        metadata[key] = value.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", metadata["strategy_id"]):
        raise PlatformInputError("strategy_id must be a lowercase identifier")
    if not re.fullmatch(r"[A-Z]{3}", metadata["currency"]):
        raise PlatformInputError("currency must be a three-letter uppercase code")
    if body.get("frequency") != "monthly" or body.get("return_basis") != "net_nav":
        raise PlatformInputError("only complete monthly net_nav returns are accepted; percentages must be decimal fractions")
    if body.get("sample_type") not in ("historical_research", "paper", "live"):
        raise PlatformInputError("sample_type must be historical_research, paper or live (source-declared)")
    limitations = body.get("limitations")
    if not isinstance(limitations, list) or not limitations or any(not isinstance(x, str) or not x.strip() or len(x) > 3000 for x in limitations) or len(limitations) > 50:
        raise PlatformInputError("limitations must contain 1–50 explicit research limitations")
    sources = body.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 30:
        raise PlatformInputError("sources must identify 1–30 source files and SHA-256 checksums")
    for s in sources:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"].strip() or len(s["name"]) > 2000 or not re.fullmatch(r"[a-f0-9]{64}", str(s.get("sha256", ""))):
            raise PlatformInputError("each source needs a name and lowercase SHA-256")
    rows = body.get("rows")
    if not isinstance(rows, list) or not 2 <= len(rows) <= 2400:
        raise PlatformInputError("rows must contain 2–2400 complete monthly observations")
    clean_rows, previous = [], None
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    for row in rows:
        if not isinstance(row, dict):
            raise PlatformInputError("each row must be an object")
        number = month_number(row.get("month"))
        if row["month"] >= current_month:
            raise PlatformInputError("unfinished or future months cannot be imported")
        if previous is not None and number != previous + 1:
            raise PlatformInputError("months must be sorted, unique and contiguous; missing months cannot be filled implicitly")
        r = finite(row.get("return"), "return")
        if r <= -1 or r > 1e6:
            raise PlatformInputError("return outside supported range (-100%, 100000000%]; insolvent sleeves need separate default accounting")
        clean_rows.append({"month": row["month"], "return": r})
        previous = number
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        levels = np.cumprod([1 + r["return"] for r in clean_rows])
    if not np.isfinite(levels).all() or (levels < 1e-12).any() or (levels > 1e12).any():
        raise PlatformInputError("cumulative NAV is outside the supported finite range")
    source_verdict = body.get("source_verdict", "NOT VALIDATED")
    if source_verdict not in ("NOT VALIDATED", "INVALID", "VALIDATED"):
        raise PlatformInputError("unknown source_verdict")
    return dict(metadata, schema_version=SCHEMA, frequency="monthly", return_basis="net_nav", sample_type=body["sample_type"],
                source_verdict=source_verdict, validation_status="NOT VALIDATED", limitations=limitations,
                sources=[{"name": s["name"], "sha256": s["sha256"]} for s in sources], rows=clean_rows)


class PerformanceStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS performance_artifacts (
                    artifact_id TEXT PRIMARY KEY, payload TEXT NOT NULL, imported_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS performance_receipts (
                    operation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, artifact_id TEXT NOT NULL);
            """)
        from .activity import ActivityStore
        self.activity = ActivityStore(self.path.with_name("activity.sqlite"))

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def import_artifact(self, body):
        payload = validate_import(body)
        raw = encode(payload)
        artifact_id = "perf_" + digest(raw.encode())
        operation = body.get("_operation_id")
        request_hash = body.get("_request_sha256", digest(payload))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if operation:
                existing = db.execute("SELECT request_hash,artifact_id FROM performance_receipts WHERE operation_id=?", (operation,)).fetchone()
                if existing and (existing[0] != request_hash or existing[1] != artifact_id):
                    raise PlatformBlocked("idempotency key was already used with different performance inputs")
            db.execute("INSERT OR IGNORE INTO performance_artifacts VALUES (?,?,?)", (artifact_id, raw, datetime.now(timezone.utc).isoformat()))
            if operation:
                db.execute("INSERT OR IGNORE INTO performance_receipts VALUES (?,?,?)", (operation, request_hash, artifact_id))
        return {"status": "IMPORTED", "artifact_id": artifact_id, "validation_status": "NOT VALIDATED", "live_trading_supported": False}

    def get(self, artifact_id):
        with self.connect() as db:
            row = db.execute("SELECT payload,imported_at FROM performance_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if row is None:
            raise PlatformNotFound("performance artifact not found")
        if "perf_" + digest(row[0].encode()) != artifact_id:
            raise PlatformBlocked("performance artifact checksum mismatch")
        return dict(json.loads(row[0]), artifact_id=artifact_id, imported_at=row[1])

    def catalog(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT artifact_id FROM performance_artifacts ORDER BY imported_at DESC,artifact_id")]
        artifacts = [self.get(i) for i in ids]
        strategies = []
        known = [(sid, name, reason) for sid, name, reason in CATALOG]
        known += [(a["strategy_id"], a["name"], "") for a in artifacts if a["strategy_id"] not in {s[0] for s in known}]
        seen = set()
        for sid, name, reason in known:
            if sid in seen:
                continue
            seen.add(sid)
            variants = []
            for a in artifacts:
                if a["strategy_id"] == sid:
                    variants.append({**{k: v for k, v in a.items() if k != "rows"}, "start": a["rows"][0]["month"], "end": a["rows"][-1]["month"], "observations": len(a["rows"]), "metrics": metrics([r["return"] for r in a["rows"]])})
            strategies.append({"strategy_id": sid, "name": name, "research_connected": bool(variants),
                               "execution_supported": sid == "stock_select", "variants": variants, "reason": reason if not variants else "Return-level research adapter; source trading logic remains in its project."})
        return {"schema_version": "platform.performance-catalog.v1", "validation_status": "NOT VALIDATED", "strategies": strategies,
                "frequency": "monthly", "live_trading_supported": False}

    def _activity_selection(self, body):
        ids = body.get("artifact_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 30 or any(not isinstance(i, str) for i in ids):
            raise PlatformInputError("select 1–30 strategy artifacts")
        artifacts = [self.get(i) for i in ids]
        if len({a["strategy_id"] for a in artifacts}) != len(artifacts):
            raise PlatformInputError("select at most one variant per strategy")
        pins = body.get("profile_ids")
        if pins is not None and (not isinstance(pins, list) or len(pins) != len(ids) or any(not isinstance(i, str) for i in pins)):
            raise PlatformInputError("profile_ids must match the selected artifacts")
        profiles = [self.activity.get(pins[i]) if pins is not None else self.activity.latest(a["artifact_id"])
                    for i, a in enumerate(artifacts)]
        for a, p in zip(artifacts, profiles):
            if p and p.get("return_artifact_id") != a["artifact_id"]:
                raise PlatformBlocked("activity profile does not match its monthly artifact")
        return artifacts, profiles

    def activity_report(self, body):
        from .activity import summarize
        artifacts, profiles = self._activity_selection(body)
        start = max(a["rows"][0]["month"] for a in artifacts)
        end = min(a["rows"][-1]["month"] for a in artifacts)
        for key in ("start", "end"):
            if body.get(key):
                month_number(body[key])
                if not start <= body[key] <= end:
                    raise PlatformBlocked(f"requested {key} lies outside complete common history ({start}–{end})")
        start, end = body.get("start") or start, body.get("end") or end
        if start > end:
            raise PlatformBlocked("no overlapping complete calendar")
        summaries = [summarize(p, a, start, end) if p else {
            "artifact_id": a["artifact_id"], "strategy_id": a["strategy_id"], "name": a["name"],
            "status": "ACTIVITY_UNAVAILABLE", "limitations": ["Import a reconciled activity ledger to measure exposure and active-session risk."]}
            for a, p in zip(artifacts, profiles)]
        result = {"schema_version": "platform.activity-report.v1", "start": start, "end": end, "profiles": summaries,
                  "validation_status": "NOT VALIDATED", "live_trading_supported": False,
                  "limitations": ["Activity returns are conditional observations, not annualized calendar performance.",
                      "Exposure and margin snapshots use each source's stated clock; they do not establish intraday peak funding.",
                      "Shared capital is not modeled. Dated margin, collateral, variation margin, settlement and FX are required before cash can be reused."]}
        result["report_id"] = "activity_report_" + digest(result)
        return result

    def exposure_report(self, body):
        """Fixed sensitivity grid over a pinned policy; never replace its targets."""
        from .exposure import evaluate_exposure
        from .capital_expansion import evaluate_capital_expansion
        from .event_sensitivity import evaluate_event_sensitivity
        request = body.get("base_request")
        if not isinstance(request, dict) or request.get("method") != "activity_risk_budget":
            raise PlatformInputError("exposure comparison requires a completed activity risk-budget scenario")
        base = self.analyze(request)
        if body.get("base_analysis_id") and body["base_analysis_id"] != base["analysis_id"]:
            raise PlatformBlocked("base analysis changed; recalculate before comparing exposure")
        artifacts, profiles = self._activity_selection(base["request"])
        selected_ids = {a["strategy_id"] for a in artifacts}
        candidates = selected_ids & {"commodity_arbitrage", "msci_rebalance", "pairs_trading"}
        if not candidates:
            raise PlatformInputError("select Commodity, MSCI or PS to compare increased exposure")
        presets = [("baseline", "Current risk budget · 1× positions", {})]
        for sid, name in (("commodity_arbitrage", "Commodity"), ("msci_rebalance", "MSCI"), ("pairs_trading", "PS")):
            if sid in candidates:
                presets.append((sid + "_2x", name + " positions ×2", {sid: 2}))
        if len(candidates) > 1:
            presets += [("selected_1_5x", "Selected Commodity / MSCI / PS ×1.5", {s: 1.5 for s in sorted(candidates)}),
                        ("selected_2x", "Selected Commodity / MSCI / PS ×2", {s: 2 for s in sorted(candidates)})]
        if "msci_rebalance" in candidates:
            presets += [("msci_3x", "MSCI positions ×3", {"msci_rebalance": 3}),
                        ("msci_5x", "MSCI positions ×5", {"msci_rebalance": 5})]
        scenarios = [{"id": sid, "label": label, **evaluate_exposure(artifacts, profiles, base, multipliers=multipliers)}
                     for sid, label, multipliers in presets]
        capital = evaluate_capital_expansion(artifacts, base)
        common_start = max(a["rows"][0]["month"] for a in artifacts)
        event = None
        for artifact, profile in zip(artifacts, profiles):
            if artifact["strategy_id"] == "msci_rebalance":
                event = {"full_common": evaluate_event_sensitivity(artifact, profile, common_start, base["end"]),
                         "full_source": evaluate_event_sensitivity(artifact, profile, artifact["rows"][0]["month"], base["end"]),
                         "evaluation": evaluate_event_sensitivity(artifact, profile, base["start"], base["end"])}
        result = {"schema_version": "platform.exposure-report.v1", "status": "RETROSPECTIVE_SENSITIVITY",
                  "validation_status": "NOT VALIDATED", "funding_validated": False, "live_trading_supported": False,
                  "start": base["start"], "end": base["end"], "observations": base["observations"],
                  "currency_label": base["currency_label"], "base_analysis_id": base["analysis_id"],
                  "request": {"base_request": base["request"], "base_analysis_id": base["analysis_id"]},
                  "position_scenarios": scenarios, "capital_scenarios": capital, "msci_event": event,
                  "selected": [{"name": a["name"], "strategy_id": a["strategy_id"], "artifact_id": a["artifact_id"],
                                "profile_id": p["profile_id"]} for a, p in zip(artifacts, profiles)],
                  "calculator": {"integration_sha256": CALCULATOR_HASH},
                  "assumptions": ["Fixed comparison grid, no ranking or selection of an optimal exposure.",
                      "Position multipliers preserve baseline capital decisions and source holding windows; source observation returns and embedded costs are linearly scaled before monthly compounding.",
                      "Capital expansion transfers only unallocated portfolio cash. It explicitly overrides the original allocation caps for comparison.",
                      "Position scaling excludes additional financing, borrow, market impact and margin calls; it does not establish executable leverage or buying power.",
                      "The MSCI event cost grid adds bps per scaled source event gross. Other strategies lack comparable turnover evidence for this extra-cost grid.",
                      "A different MSCI entry or exit window requires a separate return and funding ledger; no earlier entry is simulated here.",
                      "All histories and the source variant choices have already been inspected. This is not independent prospective evidence."]}
        result["report_id"] = "exposure_" + digest(result)
        return result

    def analyze(self, body):
        method = body.get("method", "manual")
        if method == "activity_risk_budget":
            from .allocation import evaluate_policy
            artifacts, profiles = self._activity_selection(body)
            missing = [a["name"] for a, p in zip(artifacts, profiles) if p is None]
            if missing:
                raise PlatformBlocked("activity ledger required for risk budgeting: " + ", ".join(missing))
            if body.get("start"):
                raise PlatformInputError("risk policy uses 36 complete common warmup months; evaluation start is determined by that history")
            result = evaluate_policy(artifacts, profiles, currency_mode=body.get("currency_mode", "constant_fx"),
                                     allocation_cost_bps=body.get("allocation_cost_bps", 0), end=body.get("end") or None)
            result["request"]["profile_ids"] = [p["profile_id"] for p in profiles]
            result["activity_profiles"] = [{"artifact_id": a["artifact_id"], "profile_id": p["profile_id"],
                                            "sources": p["sources"]} for a, p in zip(artifacts, profiles)]
            result["calculator"]["integration_sha256"] = CALCULATOR_HASH
            result.pop("analysis_id", None)
            result["analysis_id"] = "allocation_" + digest(result)
            return result
        if method != "manual":
            raise PlatformInputError("method must be manual or activity_risk_budget")
        sleeves = body.get("sleeves")
        if not isinstance(sleeves, list) or not 1 <= len(sleeves) <= 30:
            raise PlatformInputError("select 1–30 strategy sleeves")
        artifacts, weights, ids, strategy_ids = [], [], set(), set()
        for sleeve in sleeves:
            if not isinstance(sleeve, dict):
                raise PlatformInputError("each sleeve must be an object")
            w = finite(sleeve.get("weight"), "weight")
            if not 0 <= w <= 1:
                raise PlatformInputError("each capital allocation must be between 0 and 100%")
            if w == 0:
                continue
            a = self.get(str(sleeve.get("artifact_id", "")))
            if a["artifact_id"] in ids or a["strategy_id"] in strategy_ids:
                raise PlatformInputError("select at most one variant per strategy")
            if a["source_verdict"] == "INVALID":
                raise PlatformBlocked(f"{a['name']} is marked INVALID by its source")
            ids.add(a["artifact_id"]); strategy_ids.add(a["strategy_id"])
            artifacts.append(a); weights.append(w)
        if not artifacts or sum(weights) > 1 + 1e-10:
            raise PlatformInputError("positive allocations are required and total allocation cannot exceed 100%")
        if len({a["sample_type"] for a in artifacts}) > 1:
            raise PlatformBlocked("historical research, paper and live observations cannot be combined into one history")
        mode = body.get("currency_mode", "native")
        if mode not in ("native", "constant_fx"):
            raise PlatformInputError("currency_mode must be native or constant_fx")
        currencies = sorted({a["currency"] for a in artifacts})
        if mode == "native" and len(currencies) > 1:
            raise PlatformBlocked("mixed currencies require a dated FX adapter; choose the explicit constant-FX research scenario to isolate strategy returns")
        rebalance = body.get("rebalance", "none")
        if rebalance not in ("none", "monthly"):
            raise PlatformInputError("rebalance must be none or monthly")
        cost_bps = finite(body.get("allocation_cost_bps", 0), "allocation_cost_bps")
        if not 0 <= cost_bps <= 1000:
            raise PlatformInputError("allocation_cost_bps must be 0–1000")
        start = max(a["rows"][0]["month"] for a in artifacts)
        end = min(a["rows"][-1]["month"] for a in artifacts)
        if body.get("start"):
            month_number(body["start"])
            if body["start"] < start:
                raise PlatformBlocked(f"requested start precedes complete common history ({start})")
            start = body["start"]
        if body.get("end"):
            month_number(body["end"])
            if body["end"] > end:
                raise PlatformBlocked(f"requested end exceeds complete common history ({end})")
            end = body["end"]
        if start > end or month_number(end) - month_number(start) < 2:
            raise PlatformBlocked("fewer than three complete overlapping months; joint statistics unavailable")
        months = [r["month"] for r in artifacts[0]["rows"] if start <= r["month"] <= end]
        series = [[r["return"] for r in a["rows"] if start <= r["month"] <= end] for a in artifacts]
        if any(len(s) != len(months) for s in series) or len(months) != month_number(end) - month_number(start) + 1:
            raise PlatformBlocked("common calendar has missing months")
        matrix = np.array(series, dtype=float).T
        target = np.array(weights + [max(0.0, 1 - sum(weights))])
        capital = target.copy()
        nav, cash_cost, accumulated = 1.0, 0.0, np.zeros(len(artifacts))
        rows, contribution_returns = [], []
        paths = np.ones(len(artifacts))
        for t, month in enumerate(months):
            opening_nav = nav
            pnl = capital[:-1] * matrix[t]
            capital[:-1] += pnl
            accumulated += pnl
            nav = float(capital.sum())
            if not math.isfinite(nav) or not 1e-12 <= nav <= 1e12:
                raise PlatformBlocked("combined NAV is outside the supported finite range")
            # Transfers occur at the completed month-end, for the following month.
            # No initial or terminal redemption cost; those belong in the source NAV.
            cost = 0.0
            if rebalance == "monthly" and t < len(months) - 1:
                # Convention: bps per absolute change in non-cash sleeve allocation,
                # based on pre-fee NAV. A disclosed platform fee, not fill simulation.
                turnover = float(np.abs(target[:-1] * nav - capital[:-1]).sum())
                cost = turnover * cost_bps / 10000
                nav -= cost
                capital = target * nav
            cash_cost += cost
            paths *= 1 + matrix[t]
            cr = np.append(pnl / opening_nav, -cost / opening_nav)
            contribution_returns.append(cr)
            rows.append({"month": month, "nav": nav, "return": nav / opening_nav - 1,
                         "sleeve_nav": paths.tolist(), "capital_weights": (capital / nav).tolist(),
                         "pnl_contribution": accumulated.tolist(), "allocation_cost": cost,
                         "cumulative_allocation_cost": cash_cost})
        returns = [r["return"] for r in rows]
        cr = np.array(contribution_returns)
        risk = [None] * (len(artifacts) + 1)
        variance = float(np.var(returns, ddof=1))
        if variance > 1e-20:
            risk = [float(np.cov(cr[:, j], returns, ddof=1)[0, 1] / variance) for j in range(cr.shape[1])]
        correlation = [[None if np.std(matrix[:, i]) < 1e-12 or np.std(matrix[:, j]) < 1e-12 else float(np.corrcoef(matrix[:, i], matrix[:, j])[0, 1]) for j in range(len(artifacts))] for i in range(len(artifacts))]
        peak = 1.0
        for row in rows:
            peak = max(peak, row["nav"])
            row["drawdown"] = row["nav"] / peak - 1
        selected = [{"artifact_id": a["artifact_id"], "strategy_id": a["strategy_id"], "name": a["name"], "variant": a["variant"], "run_id": a["run_id"],
                     "currency": a["currency"], "weight": weights[i], "metrics": metrics(series[i]),
                     "pnl_contribution": float(accumulated[i]), "risk_contribution": risk[i], "limitations": a["limitations"], "sources": a["sources"],
                     "full_start": a["rows"][0]["month"], "full_end": a["rows"][-1]["month"]} for i, a in enumerate(artifacts)]
        assumptions = ["Complete monthly observations; month-end drawdown can miss intramonth losses.",
                       "Independent strategy capital accounts; idle capital stays reserved in its sleeve. No cross-margin, position netting or shared leverage.",
                       "Source strategy costs are embedded; platform cash and risk-free rate are 0%. Sharpe is a diagnostic.",
                       "A rescaled historical NAV scenario assumes linear capacity. It does not rerun trades or model liquidity.",
                       "Imported source checksums identify evidence; platform import does not independently certify source data or fills."]
        if mode == "constant_fx":
            assumptions.insert(0, "FIXED FX SCENARIO: currency moves, conversion costs and hedging are excluded. This is not a base-currency investment track record.")
        if rebalance == "monthly":
            assumptions.append(f"Monthly transfer fee: {cost_bps:g} bps per absolute non-cash sleeve allocation change at pre-fee NAV; initial funding and terminal redemption excluded.")
        request = {"sleeves": [{"artifact_id": a["artifact_id"], "weight": w} for a, w in zip(artifacts, weights)], "currency_mode": mode,
                   "rebalance": rebalance, "allocation_cost_bps": cost_bps, "start": start, "end": end}
        result = {"schema_version": "platform.joint-performance.v1", "validation_status": "NOT VALIDATED", "status": "RESEARCH_SCENARIO", "request": request,
                  "calculator": {"code_sha256": CALCULATOR_HASH, "numpy_version": np.__version__,
                                 "method": "monthly independent capital sleeves; geometric NAV; covariance contribution"},
                  "currency_label": currencies[0] if len(currencies) == 1 and mode == "native" else "Fixed FX · normalized capital",
                  "start": start, "end": end, "observations": len(months), "cash_weight": float(target[-1]), "selected": selected,
                  "metrics": metrics(returns), "correlation": correlation, "rows": rows, "allocation_cost": cash_cost,
                  "allocation_cost_risk_contribution": risk[-1], "assumptions": assumptions,
                  "accounting_error": float(abs(nav - (1 + accumulated.sum() - cash_cost))),
                  "live_trading_supported": False}
        result["analysis_id"] = "analysis_" + digest(result)
        return result


def metrics(returns):
    r = np.asarray(returns, dtype=float)
    path = np.r_[1.0, np.cumprod(1 + r)]
    vol = float(np.std(r, ddof=1) * np.sqrt(12)) if len(r) > 1 else None
    return {"total_return": float(path[-1] - 1), "cagr": float(path[-1] ** (12 / len(r)) - 1) if len(r) >= 12 else None,
            "annualized_volatility": vol, "sharpe_zero_rf": float(np.mean(r) * 12 / vol) if vol and vol > 1e-12 else None,
            "max_drawdown_monthly": float(np.min(path / np.maximum.accumulate(path) - 1)),
            "worst_month": float(np.min(r)), "positive_month_fraction": float(np.mean(r > 0))}
