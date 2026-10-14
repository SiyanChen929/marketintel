"""Intraday abnormal-move detector on the universe (Yahoo Finance 5-minute bars, SOXX as the sector benchmark).

An event is emitted when |move vs previous close| exceeds both the absolute floor and k x 20-day daily sigma,
and again only when the move crosses the next tier (4% / 8% / 15%) on the same day.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .. import config
from .base import RawItem, log, now_utc

BENCH = "SOXX"


_YF_READY = False


def _yf():
    """yfinance with its timezone cache inside the project (the default cache dir fails under launchd / concurrent use)."""
    global _YF_READY
    import yfinance as yf   # NOTE: never pass threads=True here - each yfinance worker thread leaks a tkr-tz.db handle
    if not _YF_READY:
        try:
            d = config.DATA_DIR / "yf_cache"
            d.mkdir(parents=True, exist_ok=True)
            yf.set_tz_cache_location(str(d))
        except Exception as e:                  # pragma: no cover
            log.debug("yfinance cache location not set: %s", e)
        _YF_READY = True
    return yf


def _download(tickers: list[str], **kw) -> pd.DataFrame:
    yf = _yf()
    raw = yf.download(tickers, progress=False, threads=False, auto_adjust=True, group_by="column", **kw)
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]].rename(columns={"Close": tickers[0]})
    return close


def detect_price_moves(index, settings=config.SETTINGS, state: dict | None = None, now=None) -> tuple[list[RawItem], dict, dict]:
    """Returns (items, new_state, snapshot). ``state`` remembers the highest tier alerted per ticker-day."""
    st = dict(state or {})
    tickers = list(index.tickers)
    try:
        daily = _download(tickers + [BENCH], period="3mo", interval="1d")
        intra = _download(tickers + [BENCH], period="1d", interval="5m", prepost=True)
        if not intra.empty and intra.isna().all().mean() > 0.2:        # partial download (Yahoo throttling): one retry
            log.warning("intraday download partial (%d empty columns), retrying", int(intra.isna().all().sum()))
            intra = _download(tickers + [BENCH], period="1d", interval="5m", prepost=True)
    except Exception as e:                      # pragma: no cover - network
        log.warning("price download failed: %s", e)
        return [], st, {}
    if daily.empty or intra.empty:
        return [], st, {}
    intra = intra.dropna(how="all")
    if intra.empty:
        return [], st, {}
    last = intra.ffill().iloc[-1]
    ts = intra.index[-1]
    ts_et = ts.tz_convert(config.ET) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(config.ET)
    today: date = ts_et.date()
    daily = daily.dropna(how="all")
    prev_rows = daily[daily.index.date < today] if len(daily) else daily
    if prev_rows.empty:
        return [], st, {}
    prev = prev_rows.ffill().iloc[-1]
    sigma = daily.pct_change(fill_method=None).tail(20).std()
    bench_move = float(last.get(BENCH, np.nan) / prev.get(BENCH, np.nan) - 1) if BENCH in last.index else float("nan")
    tiers = list(settings.price_tiers)
    items, snapshot = [], {}
    for t in tickers:
        if t not in last.index or pd.isna(last[t]) or pd.isna(prev.get(t, np.nan)) or prev[t] <= 0:
            continue
        move = float(last[t] / prev[t] - 1)
        idio = move - bench_move if not np.isnan(bench_move) else move
        sg = float(sigma.get(t, np.nan))
        sig_mult = abs(move) / sg if sg and not np.isnan(sg) and sg > 0 else float("nan")
        snapshot[t] = {"pct": round(move, 4), "idio": round(idio, 4), "last": round(float(last[t]), 2), "prev": round(float(prev[t]), 2),
                       "sigma_mult": None if np.isnan(sig_mult) else round(sig_mult, 1), "ts": ts.isoformat()}
        threshold = max(settings.price_move_min_pct, (sg or 0) * settings.price_move_sigma)
        if abs(move) < threshold:
            continue
        tier_idx = max(i for i, th in enumerate(tiers) if abs(move) >= th) if abs(move) >= tiers[0] else -1
        if tier_idx < 0:
            continue
        key = f"{t}:{today.isoformat()}:{'up' if move > 0 else 'down'}"
        if st.get(key, -1) >= tier_idx:
            continue
        st[key] = tier_idx
        sm = f"{sig_mult:.1f}σ" if not np.isnan(sig_mult) else "n/a"
        title = f"{t} {move:+.1%} intraday ({idio:+.1%} vs SOXX), {sm} move"
        summary = (f"{index.name(t)} last {last[t]:.2f} vs previous close {prev[t]:.2f} at {ts_et:%H:%M ET}; "
                   f"20d daily sigma {sg:.1%}; SOXX {bench_move:+.1%}. Tier {tiers[tier_idx]:.0%}.")
        items.append(RawItem(uid=f"price:{t}:{today.isoformat()}:{tiers[tier_idx]:.2f}:{'u' if move > 0 else 'd'}", source="price_monitor",
                             source_name="Price monitor (Yahoo Finance)", source_type="price", title=title, summary=summary,
                             url=f"https://finance.yahoo.com/quote/{t}", published=ts.tz_convert("UTC").to_pydatetime() if ts.tzinfo else now_utc(),
                             tickers_hint=[t], weight=1.0,
                             extra={"pct": move, "idio": idio, "sigma_mult": None if np.isnan(sig_mult) else sig_mult, "tier": tiers[tier_idx], "bench": bench_move}))
    # keep the state small: only today's keys
    st = {k: v for k, v in st.items() if k.split(":")[1] == today.isoformat()}
    return items, st, snapshot
