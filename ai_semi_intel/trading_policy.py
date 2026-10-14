"""Daily target clock. A target is never evidence of a filled position."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from .config.settings import CONFIG
from .utils.io import DataUnavailable

ET = ZoneInfo("America/New_York")
FINALITY_BUFFER = timedelta(minutes=20)


@lru_cache(maxsize=1)
def calendar():
    return xcals.get_calendar("XNYS", start="1990-01-01", end="2035-12-31")


def aware(value: datetime | str) -> pd.Timestamp:
    value = pd.Timestamp(value)
    if value.tzinfo is None:
        raise DataUnavailable("daily policy requires timezone-aware timestamps")
    return value.tz_convert("UTC")


def next_open(now: datetime | str) -> tuple[pd.Timestamp, pd.Timestamp]:
    now = aware(now)
    cal = calendar()
    session = cal.date_to_session(now.tz_convert(ET).date(), direction="next")
    if cal.session_open(session) <= now:
        session = cal.next_session(session)
    return session, cal.session_open(session)


def confirmed_prices(px: pd.DataFrame, acquired_at: datetime | str,
                     now: datetime | str) -> tuple[pd.DataFrame, dict]:
    """Filter by acquisition-time finality, without mutating the raw vendor cache."""
    acquired, current = aware(acquired_at), aware(now)
    if acquired > current:
        raise DataUnavailable("price acquisition is later than the decision clock")
    if px.empty or px.index.has_duplicates or not px.index.is_monotonic_increasing:
        raise DataUnavailable("daily policy requires sorted unique price sessions")
    cal = calendar()
    idx = pd.DatetimeIndex(px.index)
    if idx.tz is not None:
        idx = idx.tz_convert(ET).tz_localize(None)
    idx = idx.normalize()
    if idx.has_duplicates:
        raise DataUnavailable("multiple observations map to one daily session")
    keep = []
    for d in idx:
        keep.append(cal.is_session(d) and cal.session_close(d) + FINALITY_BUFFER <= acquired)
    result = px.loc[keep].copy()
    result.index = idx[keep]
    if result.empty:
        raise DataUnavailable("no completed price sessions at acquisition time")
    latest = cal.date_to_session(current.tz_convert(ET).date(), direction="previous")
    if cal.session_close(latest) + FINALITY_BUFFER > current:
        latest = cal.previous_session(latest)
    return result, {"calendar": "XNYS", "calendar_version": xcals.__version__,
                    "acquired_at": acquired.isoformat(), "confirmed_through": str(result.index[-1].date()),
                    "required_through": str(latest.date()),
                    "fresh_complete_session": result.index[-1] == latest,
                    "dropped_sessions": [str(d.date()) for d, ok in zip(idx, keep) if not ok],
                    "finality_rule": "snapshot acquired after exchange close + 20 minutes"}


def policy(generated_at: datetime | str, price_session: str) -> dict:
    session, opening = next_open(generated_at)
    prior = calendar().previous_session(session)
    ready = price_session == str(prior.date())
    return {"version": CONFIG.portfolio.execution_policy_version,
            "frequency": "daily", "calendar": "XNYS", "calendar_version": xcals.__version__,
            "timezone": str(ET), "signal_generated_at": aware(generated_at).isoformat(),
            "price_session": price_session, "required_price_session": str(prior.date()),
            "execution_session": str(session.date()), "not_before": opening.isoformat(),
            "expires_at": (opening + timedelta(minutes=CONFIG.portfolio.execution_window_minutes)).isoformat(),
            "ready": ready,
            "reason": None if ready else "target lacks the immediately preceding completed price session",
            "execution": "first eligible regular-session quote after open; actual fill price and costs required",
            "target_update": "recompute selection and target weights once per session; trade position differences",
            "pnl_basis": "continuous executed shares, cash, fees and corporate actions; target returns are not a ledger",
            "live_trading": False}


def execution_gate(run: dict, now: datetime | str) -> None:
    """Old runs remain inspectable; only correctly clocked new targets can advance a book."""
    p = run.get("rebalance_policy")
    if not isinstance(p, dict) or p.get("version") != CONFIG.portfolio.execution_policy_version:
        raise DataUnavailable("run has no current daily execution policy; generate a fresh target")
    if p.get("frequency") != "daily" or p.get("ready") is not True:
        raise DataUnavailable("daily target is not ready for execution")
    expected = policy(run["generated_at"], p["price_session"])
    for key in ("execution_session", "not_before", "expires_at", "required_price_session", "ready"):
        if p.get(key) != expected[key]:
            raise DataUnavailable("daily execution clock does not match its source run")
    now = aware(now)
    if not aware(p["not_before"]) <= now < aware(p["expires_at"]):
        raise DataUnavailable("outside the daily opening execution window; no retrospective or late fill")


def current_status(run: dict | None, now: datetime | str) -> dict:
    """Read-time target eligibility; never mutate a frozen generation-time verdict.

    OPEN only describes the target clock and saved input gates. It does not
    approve a broker order, quotes, account, or continuous paper ledger.
    """
    current = aware(now)
    out = {"status": "MISSING", "actionable_at_generation": False,
           "actionable_now": False, "execution_session": None,
           "not_before": None, "expires_at": None, "reasons": [],
           "live_trading": False, "scope": "target_clock_and_saved_input_gates"}
    if not isinstance(run, dict) or not run:
        out["reasons"] = ["no saved target run"]
        return out
    out["actionable_at_generation"] = run.get("actionable") is True
    p = run.get("rebalance_policy")
    if not isinstance(p, dict):
        out.update(status="INVALID_POLICY", reasons=["run has no daily execution policy"])
        return out
    for key in ("execution_session", "not_before", "expires_at"):
        out[key] = p.get(key)
    try:
        if p.get("version") != CONFIG.portfolio.execution_policy_version or p.get("frequency") != "daily":
            raise ValueError("unsupported daily execution policy")
        expected = policy(run["generated_at"], p["price_session"])
        for key in ("execution_session", "not_before", "expires_at", "required_price_session", "ready"):
            if p.get(key) != expected[key]:
                raise ValueError("daily execution clock does not match its source run")
        opening, expiry = aware(p["not_before"]), aware(p["expires_at"])
    except (KeyError, TypeError, ValueError, DataUnavailable):
        out.update(status="INVALID_POLICY", reasons=["daily execution clock is missing, unsupported, or inconsistent"])
        return out
    if current < aware(run["generated_at"]):
        out.update(status="INVALID_POLICY", reasons=["run was generated after the current clock"])
    elif current >= expiry:
        out.update(status="EXPIRED", reasons=["daily target execution window has expired; generate a fresh target"])
    elif not out["actionable_at_generation"] or p.get("ready") is not True:
        reasons = run.get("not_actionable_reasons") or [p.get("reason") or "saved input gates block this target"]
        out.update(status="BLOCKED", reasons=list(reasons))
    elif current < opening:
        out.update(status="PENDING_OPEN", reasons=["target awaits its regular-session execution window"])
    else:
        out.update(status="OPEN", actionable_now=True)
    return out


def preparation_window(now: datetime | str) -> dict:
    now = aware(now).tz_convert(ET)
    cal = calendar()
    session = pd.Timestamp(now.date())
    if not cal.is_session(session):
        upcoming = cal.date_to_session(session, direction="next")
        prepare = datetime.combine(upcoming.date(), time(7, 15), ET)
        return {"due": False, "session": str(now.date()), "reason": "exchange closed",
                "next_preparation_at": prepare.isoformat()}
    start = pd.Timestamp(datetime.combine(now.date(), time(7, 15), ET))
    deadline = cal.session_open(session).tz_convert(ET) - timedelta(minutes=15)
    upcoming = cal.next_session(session) if now >= deadline else session
    prepare = datetime.combine(upcoming.date(), time(7, 15), ET)
    return {"due": start <= now < deadline, "session": str(now.date()),
            "reason": "preparation window" if start <= now < deadline else "outside preparation window",
            "prepare_from": start.isoformat(), "prepare_before": deadline.isoformat(),
            "execution_open": cal.session_open(session).isoformat(),
            "next_preparation_at": prepare.isoformat()}
