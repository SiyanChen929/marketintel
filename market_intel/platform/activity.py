"""Immutable source activity profiles and conditional research measurements."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from .service import PlatformBlocked, PlatformInputError, PlatformNotFound


def _encode(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PlatformInputError("activity profile must contain finite JSON values") from exc


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _number(value, label, nullable=False, nonnegative=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlatformInputError(f"activity {label} must be finite")
    if nonnegative and value < 0:
        raise PlatformInputError(f"activity {label} must be nonnegative")
    return float(value)


def _source_set(sources):
    if not isinstance(sources, list) or not sources:
        raise PlatformInputError("activity source anchors are required")
    result = {}
    for s in sources:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"].strip() or not re.fullmatch(r"[a-f0-9]{64}", str(s.get("sha256", ""))):
            raise PlatformInputError("activity source needs a name and SHA-256")
        if s["name"] in result:
            raise PlatformInputError("activity source names must be unique")
        result[s["name"]] = s["sha256"]
    return result


def _validate(profile, artifact):
    if not isinstance(profile, dict) or profile.get("return_artifact_id") != artifact["artifact_id"] or profile.get("strategy_id") != artifact["strategy_id"]:
        raise PlatformInputError("activity parent and strategy must match the frozen return artifact")
    if profile.get("frequency") not in ("daily", "event"):
        raise PlatformInputError("activity frequency must be daily or event")
    if "profile_id" in profile:
        raise PlatformInputError("activity profile_id is assigned by the store")
    sources = _source_set(profile.get("sources"))
    for name, checksum in _source_set(artifact["sources"]).items():
        if sources.get(name) != checksum:
            raise PlatformInputError("activity source anchors do not match the frozen return artifact")
    for key in ("capital_basis", "margin_basis"):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            raise PlatformInputError(f"activity {key} is required")
    limitations = profile.get("limitations")
    if not isinstance(limitations, list) or not limitations or any(not isinstance(x, str) or not x.strip() for x in limitations):
        raise PlatformInputError("activity limitations are required")
    rows = profile.get("rows")
    if not isinstance(rows, list) or not rows or len(rows) > 100000:
        raise PlatformInputError("activity needs 1–100000 source observations")
    last_date, last_nav, events, monthly = None, None, set(), {}
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("date", ""))):
            raise PlatformInputError("activity dates must be YYYY-MM-DD")
        try:
            current = date.fromisoformat(row["date"])
        except ValueError as exc:
            raise PlatformInputError("invalid activity date") from exc
        if last_date is not None and current <= last_date:
            raise PlatformInputError("activity dates must be unique and ordered")
        if not isinstance(row.get("active"), bool):
            raise PlatformInputError("activity active must be boolean")
        opening = _number(row.get("nav_open"), "nav_open")
        closing = _number(row.get("nav_close"), "nav_close")
        ret = _number(row.get("return"), "return")
        if opening <= 0 or closing <= 0 or ret <= -1 or not math.isclose(closing / opening - 1, ret, abs_tol=1e-10, rel_tol=1e-9):
            raise PlatformInputError("activity return does not reconcile with positive NAV")
        if last_nav is not None and not math.isclose(opening, last_nav, abs_tol=1e-8, rel_tol=1e-9):
            raise PlatformInputError("activity NAV continuity is broken")
        pnl = _number(row.get("net_pnl"), "net_pnl", nullable=True)
        if pnl is not None and not math.isclose(closing - opening, pnl, abs_tol=1e-6, rel_tol=1e-8):
            raise PlatformInputError("activity net P&L does not reconcile with NAV")
        exposures = [_number(row.get(key), key, nullable=True, nonnegative=True) for key in ("gross_open", "gross_close", "margin_open", "margin_close")]
        if not row["active"] and any(x is not None and x > 1e-12 for x in exposures):
            raise PlatformInputError("activity cannot be idle while carrying measured exposure")
        event_id = row.get("event_id")
        if profile["frequency"] == "event":
            if not isinstance(event_id, str) or not event_id or event_id in events:
                raise PlatformInputError("event activity needs unique event identifiers")
            events.add(event_id)
        elif event_id is not None:
            raise PlatformInputError("daily activity must not imply independent event identifiers")
        month = row["date"][:7]
        monthly[month] = monthly.get(month, 1.0) * (1 + ret)
        last_date, last_nav = current, closing
    for r in artifact["rows"]:
        if r["month"] not in monthly and profile["frequency"] != "event":
            raise PlatformInputError("daily activity is missing a frozen performance month")
        if not math.isclose(monthly.get(r["month"], 1.0) - 1, r["return"], abs_tol=1e-9, rel_tol=1e-9):
            raise PlatformInputError("activity does not reconcile with frozen monthly returns")
    clean = json.loads(_encode(profile))
    clean["validation_status"] = "NOT VALIDATED"
    return clean


class ActivityStore:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS activity_profiles (profile_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS activity_parent ON activity_profiles(artifact_id,created_at)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def put(self, profile, artifact):
        clean = _validate(profile, artifact)
        raw = _encode(clean)
        profile_id = "activity_" + _hash(raw)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO activity_profiles VALUES (?,?,?,?)", (profile_id, artifact["artifact_id"], raw, datetime.now(timezone.utc).isoformat()))
        return self.get(profile_id)

    def get(self, profile_id):
        with self.connect() as db:
            row = db.execute("SELECT artifact_id,payload FROM activity_profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if row is None:
            raise PlatformNotFound("activity profile not found")
        if "activity_" + _hash(row[1]) != profile_id:
            raise PlatformBlocked("activity profile checksum mismatch")
        result = json.loads(row[1])
        if result.get("return_artifact_id") != row[0]:
            raise PlatformBlocked("activity parent index does not match the frozen profile")
        return dict(result, profile_id=profile_id)

    def latest(self, artifact_id):
        with self.connect() as db:
            row = db.execute("SELECT profile_id FROM activity_profiles WHERE artifact_id=? ORDER BY created_at DESC,profile_id DESC LIMIT 1", (artifact_id,)).fetchone()
        return self.get(row[0]) if row else None


def _month_number(month):
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise PlatformInputError("activity window must use YYYY-MM")
    y, m = map(int, month.split("-"))
    return y * 12 + m - 1


def summarize(profile, artifact, start=None, end=None):
    if profile.get("return_artifact_id") != artifact["artifact_id"] or profile.get("strategy_id") != artifact["strategy_id"]:
        raise PlatformInputError("activity summary parent mismatch")
    start = start or artifact["rows"][0]["month"]
    end = end or artifact["rows"][-1]["month"]
    count = _month_number(end) - _month_number(start) + 1
    months = [r for r in artifact["rows"] if start <= r["month"] <= end]
    if count < 1 or len(months) != count:
        raise PlatformInputError("activity window must be fully covered by the frozen monthly artifact")
    rows = [r for r in profile["rows"] if start <= r["date"][:7] <= end]
    active = [r for r in rows if r["active"]]
    returns = np.asarray([r["return"] for r in active], dtype=float)
    total_nav = float(np.prod([1 + r["return"] for r in months]))
    def stat(key, method):
        values = [r[key] for r in rows if r.get(key) is not None]
        # Partial evidence is not reported as a full-window occupancy measure.
        return float(getattr(np, method)(values)) if values and len(values) == len(rows) else None
    result = {"schema_version": "platform.activity-summary.v1", "artifact_id": artifact["artifact_id"],
              "profile_id": profile["profile_id"], "name": artifact["name"], "variant": artifact["variant"],
              "strategy_id": artifact["strategy_id"], "frequency": profile["frequency"], "start": start, "end": end,
              "observations": len(rows), "active_observations": len(active),
              "active_fraction": len(active) / len(rows) if rows and profile["frequency"] == "daily" else None,
              "first_active": active[0]["date"] if active else None, "last_active": active[-1]["date"] if active else None,
              "event_count": len({r["event_id"] for r in active}) if profile["frequency"] == "event" else None,
              "active_return_mean": float(returns.mean()) if len(returns) else None,
              "active_return_std": float(returns.std(ddof=1)) if len(returns) > 1 else None,
              "active_return_worst": float(returns.min()) if len(returns) else None,
              "active_return_unit": "per recorded event, conditional on activity; not annualized" if profile["frequency"] == "event" else "per source session, conditional on activity; not annualized",
              "calendar_total_return": total_nav - 1, "calendar_cagr": total_nav ** (12 / count) - 1 if count >= 12 else None,
              "gross_open_mean": stat("gross_open", "mean"), "gross_reported_mean": stat("gross_close", "mean"),
              "gross_reported_max": stat("gross_close", "max"), "margin_reported_mean": stat("margin_close", "mean"),
              "margin_reported_max": stat("margin_close", "max"),
              "capital_basis": profile["capital_basis"], "margin_basis": profile["margin_basis"],
              "exposure_basis": profile.get("exposure_basis", profile["capital_basis"]),
              "gross_reported_clock": "Source gross_close snapshot / corresponding NAV. See exposure_basis; this is not a common intraday peak across strategies.",
              "limitations": list(dict.fromkeys(artifact.get("limitations", []) + profile["limitations"])),
              "sources": profile["sources"], "validation_status": "NOT VALIDATED"}
    for key in ("holding_period", "activity_basis", "pnl_unit"):
        if key in profile:
            result[key] = profile[key]
    if "trade_observations" in profile:
        result["trade_observations"] = [t for t in profile["trade_observations"] if t["entry_date"][:7] <= end and t["exit_date"][:7] >= start]
    if any("security_events" in r for r in rows):
        result["security_event_count"] = sum(r.get("security_events", 0) for r in rows)
    return result
