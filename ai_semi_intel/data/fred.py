"""FRED (St. Louis Fed) adapter for the macro backdrop and the risk-free rate."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

from ..config.settings import CONFIG, DATA_CACHE, FRED_SERIES, load_fred_key
from ..utils.io import DataUnavailable, file_age_hours, log, record_status

FRED_DIR = DATA_CACHE / "fred"
FRED_DIR.mkdir(parents=True, exist_ok=True)
STATUS_PATH = FRED_DIR / "_status.json"          # fetch outcomes per series (ok / error / served_from_cache)
BASE = "https://api.stlouisfed.org/fred/series/observations"


def _cached(path: Path, series_id: str, status: str, reason: str | None = None) -> pd.Series:
    s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
    s.name = series_id
    s.attrs.update({"status": status, "stale_reason": reason, "fetched_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"), "source": "cache"})
    return s


def fetch_series(series_id: str, start: str = "2010-01-01", max_age_hours: float | None = None) -> pd.Series:
    """FRED observations. Never hides a failure: the returned series carries ``attrs['status']`` in
    {fresh, stale, unavailable} and a ``stale_reason``; failures are recorded in ``_status.json`` for the run manifest."""
    max_age = CONFIG.fred_cache_hours if max_age_hours is None else max_age_hours
    path = FRED_DIR / f"{series_id}.csv"
    if file_age_hours(path) < max_age:
        return _cached(path, series_id, "fresh")
    params = {"series_id": series_id, "api_key": load_fred_key(), "file_type": "json",
              "observation_start": start}
    try:
        r = requests.get(BASE, params=params, timeout=30)
        r.raise_for_status()
        obs = r.json().get("observations", [])
    except Exception as e:                        # pragma: no cover - network
        from ..utils.redaction import redact_sensitive_text
        diagnostic = redact_sensitive_text(str(e), secrets=[params["api_key"]])
        log.warning("FRED %s failed: %s", series_id, diagnostic)
        record_status(STATUS_PATH, series_id, ok=False, error=diagnostic[:200], served_from_cache=path.exists())
        if path.exists():
            reason = f"fetch failed ({diagnostic[:80]}); serving cache from {datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M}"
            return _cached(path, series_id, "stale", reason)
        s = pd.Series(dtype=float, name=series_id)
        s.attrs.update({"status": "unavailable", "stale_reason": f"fetch failed and no cache: {diagnostic[:80]}", "fetched_at": None, "source": None})
        return s
    s = pd.Series({pd.Timestamp(o["date"]): (np.nan if o["value"] == "." else float(o["value"])) for o in obs},
                  name=series_id).dropna()
    s.to_csv(path)
    record_status(STATUS_PATH, series_id, ok=True, error=None, served_from_cache=False, n_obs=int(len(s)), last_obs=str(s.index[-1].date()) if len(s) else None)
    s.attrs.update({"status": "fresh", "stale_reason": None, "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"), "source": "api"})
    return s


def macro_panel() -> dict[str, pd.Series]:
    panel = {}
    for sid in FRED_SERIES:
        s = fetch_series(sid)
        if not s.empty:
            panel[sid] = s
    # derived YoY series
    for sid, name in (("CPIAUCSL", "CPI_YOY"), ("PCEPILFE", "COREPCE_YOY"), ("IPG3344S", "SEMI_IP_YOY"),
                      ("PCU33443344", "SEMI_PPI_YOY"), ("INDPRO", "INDPRO_YOY"), ("NEWORDER", "CAPGOODS_YOY")):
        if sid in panel:
            panel[name] = (panel[sid].pct_change(12) * 100).dropna()
    if "GDPC1" in panel:
        panel["GDP_YOY"] = (panel["GDPC1"].pct_change(4) * 100).dropna()
    return panel


def risk_free_rate(panel: dict[str, pd.Series] | None = None) -> float:
    """Latest DGS10 / 100. Fails closed: no series means no defensible discount rate, so the run stops."""
    panel = panel or {"DGS10": fetch_series("DGS10")}
    s = panel.get("DGS10")
    if s is None or s.dropna().empty:
        raise DataUnavailable("risk-free rate (FRED DGS10) unavailable: fetch failed and no cache")
    return float(s.dropna().iloc[-1]) / 100


def macro_snapshot(panel: dict[str, pd.Series]) -> pd.DataFrame:
    """Latest value, 3M/12M change for each headline series - used in deck & dashboard."""
    rows = []
    labels = dict(FRED_SERIES, CPI_YOY="CPI YoY (%)", COREPCE_YOY="Core PCE YoY (%)", SEMI_IP_YOY="Semi Industrial Production YoY (%)",
                  SEMI_PPI_YOY="Semi PPI YoY (%)", INDPRO_YOY="Industrial Production YoY (%)", GDP_YOY="Real GDP YoY (%)",
                  CAPGOODS_YOY="Core Capital Goods Orders YoY (%)")
    for sid, s in panel.items():
        s = s.dropna()
        if s.empty:
            continue
        last_dt = s.index[-1]
        def _at(months):
            sub = s[s.index <= last_dt - pd.DateOffset(months=months)]
            return float(sub.iloc[-1]) if not sub.empty else np.nan
        rows.append({"series": sid, "label": labels.get(sid, sid), "date": last_dt.strftime("%Y-%m-%d"),
                     "value": float(s.iloc[-1]), "chg_3m": float(s.iloc[-1]) - _at(3), "chg_12m": float(s.iloc[-1]) - _at(12)})
    return pd.DataFrame(rows).set_index("series")
