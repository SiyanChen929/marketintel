"""Macro calendar: FRED release dates (scheduled), the FOMC meeting calendar and universe earnings dates."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .. import config
from .base import Http, log

# (substring of the FRED release name, importance 1-3, typical release time ET)
KEY_RELEASES = [
    ("Employment Situation", 3, "08:30"), ("Consumer Price Index", 3, "08:30"), ("Personal Income and Outlays", 3, "08:30"),
    ("Gross Domestic Product", 3, "08:30"), ("Producer Price Index", 2, "08:30"), ("Advance Monthly Sales for Retail", 2, "08:30"),
    ("Job Openings and Labor Turnover", 2, "10:00"), ("Unemployment Insurance Weekly Claims", 1, "08:30"),
    ("G.17 Industrial Production", 1, "09:15"), ("Productivity and Costs", 1, "08:30"), ("Import and Export Price", 1, "08:30"),
    ("New Residential Construction", 1, "08:30"), ("New Residential Sales", 1, "10:00"), ("Empire State Manufacturing", 1, "08:30"),
    ("Advance Economic Indicators", 1, "08:30"), ("H.4.1 Factors Affecting Reserve", 1, "16:30"), ("Z.1 Financial Accounts", 1, "12:00"),
    ("H.6 Money Stock", 1, "13:00"), ("Chicago Fed National Activity", 1, "08:30"), ("Manufacturers' Shipments, Inventories", 2, "10:00"),
    ("Durable Goods", 2, "08:30"), ("Employment Cost Index", 2, "08:30"), ("Philadelphia Fed Manufacturing", 1, "08:30"),
    ("University of Michigan", 2, "10:00"), ("Consumer Sentiment", 2, "10:00"), ("Kansas City Fed Labor Market", 1, "10:00"),
    ("Monthly Treasury Statement", 1, "14:00"), ("Treasury International Capital", 1, "16:00"), ("Existing Home Sales", 1, "10:00"),
    ("Consumer Credit", 1, "15:00"), ("Beige Book", 2, "14:00"), ("Senior Loan Officer", 1, "14:00"),
]
# FOMC decision days (second day of each 2026 meeting); statements at 14:00 ET
FOMC_MEETINGS_2026 = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
FRED_URL = ("https://api.stlouisfed.org/fred/releases/dates?api_key={key}&file_type=json&include_release_dates_with_no_data=true"
            "&realtime_start={start}&realtime_end={end}&limit=1000&sort_order=asc")


def _importance(name: str) -> tuple[int, str] | None:
    for sub, imp, t in KEY_RELEASES:
        if sub.lower() in name.lower():
            return imp, t
    return None


def fred_calendar(http: Http, days_ahead: int = 14, days_back: int = 1, today: date | None = None) -> list[dict]:
    today = today or config.now_et().date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    rows: list[dict] = []
    key = config.fred_api_key()
    if key:
        data = http.get_json(FRED_URL.format(key=key, start=start.isoformat(), end=end.isoformat()))
        if data:
            seen = set()
            for r in data.get("release_dates", []):
                name = r.get("release_name", "")
                imp = _importance(name)
                if not imp or "FOMC" in name:
                    continue
                k = (r["date"], name)
                if k in seen:
                    continue
                seen.add(k)
                rows.append({"date": r["date"], "name": name, "importance": imp[0], "time_et": imp[1], "source": "FRED", "kind": "macro",
                             "release_id": r.get("release_id"), "ticker": None})
        else:
            log.warning("FRED release calendar unavailable")
    for d in FOMC_MEETINGS_2026:
        if start.isoformat() <= d <= end.isoformat():
            rows.append({"date": d, "name": "FOMC decision & press conference", "importance": 3, "time_et": "14:00", "source": "Federal Reserve",
                         "kind": "macro", "release_id": None, "ticker": None})
    return sorted(rows, key=lambda r: (r["date"], r["time_et"], -r["importance"]))


def earnings_calendar(index, days_ahead: int = 14, days_back: int = 1, today: date | None = None) -> list[dict]:
    today = today or config.now_et().date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    rows = []
    for t, c in index.companies.items():
        if not c.next_earnings:
            continue
        d = str(c.next_earnings)[:10]
        if start.isoformat() <= d <= end.isoformat():
            rows.append({"date": d, "name": f"{t} earnings - {c.name}", "importance": 3 if c.is_holding else 1, "time_et": "",
                         "source": "Yahoo Finance", "kind": "earnings", "release_id": None, "ticker": t})
    return sorted(rows, key=lambda r: (r["date"], -r["importance"]))
