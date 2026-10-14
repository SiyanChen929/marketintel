"""Yahoo Finance adapter (via yfinance): prices, statements, consensus estimates.

All calls are cached on disk (data_cache/prices, data_cache/fundamentals) with an
age threshold so the daily job only refreshes what is stale.
"""
from __future__ import annotations

import concurrent.futures as cf
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from ..config.settings import CONFIG, DATA_CACHE
from ..utils.io import file_age_hours, log, read_json, record_status, write_json

PRICE_DIR = DATA_CACHE / "prices"
FUND_DIR = DATA_CACHE / "fundamentals"
PRICE_DIR.mkdir(parents=True, exist_ok=True)
FUND_DIR.mkdir(parents=True, exist_ok=True)

INFO_FIELDS = [
    "shortName", "longName", "sector", "industry", "country", "currency", "financialCurrency", "exchange",
    "marketCap", "enterpriseValue", "sharesOutstanding", "floatShares", "currentPrice",
    "previousClose", "fiftyDayAverage", "twoHundredDayAverage", "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow", "beta", "trailingPE", "forwardPE", "priceToBook",
    "enterpriseToRevenue", "enterpriseToEbitda", "trailingPegRatio", "totalRevenue",
    "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth", "grossMargins",
    "operatingMargins", "ebitdaMargins", "profitMargins", "returnOnEquity",
    "returnOnAssets", "freeCashflow", "operatingCashflow", "totalDebt", "totalCash",
    "ebitda", "netIncomeToCommon", "trailingEps", "forwardEps", "bookValue",
    "debtToEquity", "currentRatio", "dividendYield", "payoutRatio",
    "targetMeanPrice", "targetMedianPrice", "targetHighPrice", "targetLowPrice",
    "numberOfAnalystOpinions", "recommendationMean", "recommendationKey",
    "shortPercentOfFloat", "heldPercentInstitutions", "fullTimeEmployees",
    "averageVolume", "longBusinessSummary",
]


# ----------------------------------------------------------------------------- prices
def _price_cache_path(tag: str) -> Path:
    return PRICE_DIR / f"prices_{tag}.parquet"


def fetch_prices(tickers: list[str], years: int | None = None, tag: str = "universe",
                 max_age_hours: float | None = None, force: bool = False) -> pd.DataFrame:
    """Adjusted close panel (columns = tickers). Cached to parquet."""
    years = years or CONFIG.price_history_years
    max_age = CONFIG.yahoo_cache_hours if max_age_hours is None else max_age_hours
    path = _price_cache_path(tag)
    if not force and file_age_hours(path) < max_age:
        df = pd.read_parquet(path)
        missing = [t for t in tickers if t not in df.columns]
        if not missing:
            return df[tickers]
    start = (datetime.today() - timedelta(days=365 * years + 10)).strftime("%Y-%m-%d")
    log.info("Downloading prices for %d tickers from %s", len(tickers), start)
    frames = []
    for i in range(0, len(tickers), 40):          # chunk to be polite to Yahoo
        chunk = tickers[i:i + 40]
        for attempt in range(3):
            try:
                raw = yf.download(chunk, start=start, auto_adjust=True, progress=False,
                                  threads=True, group_by="column")
                break
            except Exception as e:                # pragma: no cover - network
                log.warning("price download retry %d: %s", attempt, e)
                time.sleep(5 * (attempt + 1))
                raw = pd.DataFrame()
        if raw.empty:
            continue
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(columns={"Close": chunk[0]})
        frames.append(close)
    if not frames:
        raise RuntimeError("Price download failed for all chunks")
    df = pd.concat(frames, axis=1)
    df = df.loc[:, ~df.columns.duplicated()]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.dropna(how="all")
    df.to_parquet(path)
    return df[[t for t in tickers if t in df.columns]]


# ------------------------------------------------------------------------ fundamentals
def _frame_records(df: pd.DataFrame | None, max_cols: int = 6) -> dict[str, dict[str, Any]]:
    """Statement DataFrame (rows = line items, cols = period) -> {period: {item: value}}."""
    if df is None or df.empty:
        return {}
    out = {}
    for col in list(df.columns)[:max_cols]:
        key = str(pd.Timestamp(col).date())
        s = df[col]
        out[key] = {str(k): (None if pd.isna(v) else float(v)) for k, v in s.items()}
    return out


def _estimate_records(df: pd.DataFrame | None) -> dict:
    if df is None or getattr(df, "empty", True):
        return {}
    d = {}
    for idx, row in df.iterrows():
        d[str(idx)] = {str(k): (None if (isinstance(v, float) and np.isnan(v)) else (v if isinstance(v, (int, float, str)) else str(v)))
                       for k, v in row.items()}
    return d


def fetch_fundamentals(ticker: str, max_age_hours: float | None = None, force: bool = False) -> dict:
    """Snapshot of everything we need from Yahoo for one ticker, cached as JSON."""
    max_age = CONFIG.yahoo_cache_hours if max_age_hours is None else max_age_hours
    path = FUND_DIR / f"{ticker}.json"
    if not force and file_age_hours(path) < max_age:
        cached = read_json(path)
        if cached and cached.get("ok"):
            return cached
    snap: dict[str, Any] = {"ticker": ticker, "asof": datetime.utcnow().isoformat(), "ok": False}
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        snap["info"] = {k: info.get(k) for k in INFO_FIELDS}
        snap["income_annual"] = _frame_records(tk.financials)
        snap["balance_annual"] = _frame_records(tk.balance_sheet)
        snap["cashflow_annual"] = _frame_records(tk.cashflow)
        snap["income_quarterly"] = _frame_records(tk.quarterly_financials, 8)
        snap["cashflow_quarterly"] = _frame_records(tk.quarterly_cashflow, 8)
        snap["balance_quarterly"] = _frame_records(tk.quarterly_balance_sheet, 4)
        for name in ("earnings_estimate", "revenue_estimate", "growth_estimates", "eps_trend",
                     "eps_revisions", "recommendations_summary"):
            try:
                snap[name] = _estimate_records(getattr(tk, name))
            except Exception:
                snap[name] = {}
        try:
            snap["price_targets"] = tk.analyst_price_targets or {}
        except Exception:
            snap["price_targets"] = {}
        try:
            cal = tk.calendar or {}
            ed = cal.get("Earnings Date")
            snap["next_earnings"] = str(ed[0]) if isinstance(ed, (list, tuple)) and ed else (str(ed) if ed else None)
        except Exception:
            snap["next_earnings"] = None
        snap["ok"] = bool(snap["info"].get("marketCap") or snap["income_annual"])
    except Exception as e:                        # pragma: no cover - network
        snap["error"] = str(e)
    write_json(snap, path)
    return snap


def fetch_all_fundamentals(tickers: list[str], workers: int = 4, **kw) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_fundamentals, t, **kw): t for t in tickers}
        for i, f in enumerate(cf.as_completed(futs), 1):
            t = futs[f]
            try:
                out[t] = f.result()
            except Exception as e:                # pragma: no cover
                out[t] = {"ticker": t, "ok": False, "error": str(e)}
            if i % 20 == 0:
                log.info("fundamentals %d/%d", i, len(tickers))
    bad = [t for t, s in out.items() if not s.get("ok")]
    if bad:
        log.warning("fundamentals missing for: %s", bad)
    return out


# ------------------------------------------------------------------------------- FX
FX_STATUS = PRICE_DIR / "fx_status.json"


def fetch_fx(ccy: str, max_age_hours: float | None = None) -> float | None:
    """Units of `ccy` per 1 USD (Yahoo '<CCY>=X'). Returns 1.0 for USD and **None** on failure (never a silent 1.0),
    recording the outcome in fx_status.json for the run manifest."""
    if not ccy or ccy.upper() == "USD":
        return 1.0
    max_age = CONFIG.yahoo_cache_hours if max_age_hours is None else max_age_hours
    path = PRICE_DIR / "fx.json"
    cache = read_json(path, {}) if file_age_hours(path) < max_age else {}
    if ccy in cache:
        return float(cache[ccy])
    try:
        h = yf.Ticker(f"{ccy}=X").history(period="5d")
        rate = float(h["Close"].dropna().iloc[-1])
    except Exception as e:                        # pragma: no cover - network
        log.warning("FX %s failed: %s - name will be marked fx_missing", ccy, e)
        record_status(FX_STATUS, ccy, ok=False, error=str(e)[:200])
        return None
    cache = read_json(path, {}) or {}
    cache[ccy] = rate
    write_json(cache, path)
    record_status(FX_STATUS, ccy, ok=True, error=None, rate=rate)
    return rate
