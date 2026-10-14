"""Build the per-company feature table from Yahoo, EDGAR and prices.

Output: one row per ticker with growth, profitability, balance-sheet, valuation,
consensus-revision and price-momentum features. This table feeds valuation,
scoring, the deck and the dashboard.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..config.universe import universe_frame, BENCHMARKS
from ..data.yahoo import fetch_fx
from ..utils.io import log, safe_div

#: A revision must be signed by the DIRECTION of the estimate change, never by the ratio of two estimates.
#: ``cur/old - 1`` is wrong whenever both are negative: a loss widening from -1.62 to -2.52 (NBIS FY0) returns
#: +55%, i.e. a deepening loss is scored as an upgrade. ``(cur - old)/|old|`` is correct for either sign.
MIN_EPS_DENOM = 0.05        # estimates nearer zero than this make the percentage meaningless


def eps_revision(cur, old):
    """Signed consensus-EPS revision: (cur - old) / |old|.

    Returns NaN when either side is missing or the base estimate is too close to zero for a percentage to
    mean anything (a move from -0.01 to -0.20 is not "-1900% of signal"). Crossing zero is a genuine
    revision and is kept - the sign then comes from the change, as it should.
    """
    try:
        if cur is None or old is None:
            return np.nan
        c, o = float(cur), float(old)
    except (TypeError, ValueError):
        return np.nan
    if np.isnan(c) or np.isnan(o) or abs(o) < MIN_EPS_DENOM:
        return np.nan
    return (c - o) / abs(o)


def _num(x):
    try:
        if x is None:
            return np.nan
        v = float(x)
        return v
    except (TypeError, ValueError):
        return np.nan


def _stmt_series(stmt: dict, item: str) -> pd.Series:
    """{period: {item: value}} -> Series indexed by period date (ascending)."""
    if not stmt:
        return pd.Series(dtype=float)
    d = {pd.Timestamp(k): _num(v.get(item)) for k, v in stmt.items() if isinstance(v, dict)}
    ser = pd.Series(d).sort_index().dropna()
    if item in ("Total Revenue", "Operating Revenue"):
        ser = ser[ser != 0]                     # Yahoo occasionally emits 0 for an unreported quarter
    return ser


def _ttm(stmt_q: dict, item: str) -> float:
    """Sum of the last four *consecutive* reported quarters; NaN if fewer are available."""
    s = _stmt_series(stmt_q, item)
    if len(s) < 4:
        return np.nan
    last4 = s.tail(4)
    span = (last4.index[-1] - last4.index[0]).days
    return float(last4.sum()) if 240 <= span <= 300 else np.nan


def _est(snap: dict, table: str, period: str, field: str) -> float:
    return _num(snap.get(table, {}).get(period, {}).get(field))


def _fy0_weight(snap: dict, asof: datetime) -> float:
    """Fraction of the *current* fiscal year still ahead of us (0..1), used to blend
    FY0/FY1 consensus into next-twelve-months (NTM) figures."""
    periods = sorted(snap.get("income_annual", {}).keys())
    if not periods:
        return 0.5
    last_fy_end = pd.Timestamp(periods[-1])
    fy0_end = last_fy_end + pd.DateOffset(years=1)
    # Yahoo's "0y" is the first unreported fiscal year. If its year-ago base is well above
    # the last reported FY revenue, Yahoo has already rolled forward while the statements lag.
    try:
        rev_last = _num(snap["income_annual"][periods[-1]].get("Total Revenue"))
        ya0 = _num(snap.get("revenue_estimate", {}).get("0y", {}).get("yearAgoRevenue"))
        if rev_last and ya0 and ya0 > rev_last * 1.15:
            fy0_end += pd.DateOffset(years=1)
    except Exception:
        pass
    months_left = max(0.0, (fy0_end - asof).days / 30.44)
    return float(np.clip(months_left / 12.0, 0.0, 1.0))


def fx_conversion(ccy: str) -> tuple[float, bool]:
    """(multiplier local -> USD, missing). A failed FX fetch yields NaN and missing=True so the name is excluded, never 1.0."""
    if not ccy or ccy.upper() == "USD":
        return 1.0, False
    fx = fetch_fx(ccy)
    if fx is None or not fx:
        return np.nan, True
    return 1.0 / fx, False


def features_for(ticker: str, snap: dict, ed: dict, px: pd.DataFrame, asof: datetime,
                 bench: str = "SOXX") -> dict:
    info = snap.get("info", {}) or {}
    f: dict = {"ticker": ticker, "name": info.get("shortName") or ticker,
               "sector": info.get("sector"), "industry": info.get("industry"),
               "country": info.get("country")}
    price = _num(info.get("currentPrice")) or _num(info.get("previousClose"))
    if (np.isnan(price) or price == 0) and ticker in px.columns:
        price = float(px[ticker].dropna().iloc[-1])
    f["price"] = price
    f["market_cap"] = _num(info.get("marketCap"))
    # ADR-equivalent share count: consistent with the quoted price and USD market cap
    f["shares"] = safe_div(f["market_cap"], price) if f["market_cap"] and price else _num(info.get("sharesOutstanding"))
    # Financial statements (and Yahoo's EV/totalRevenue) are in the reporting currency.
    fin_ccy = (info.get("financialCurrency") or "USD").upper()
    cv, fx_missing = fx_conversion(fin_ccy)
    f["financial_currency"], f["fx_to_usd"], f["fx_missing"] = fin_ccy, cv, fx_missing
    f["ev"] = np.nan                            # recomputed below from market cap + net debt in USD
    f["beta"] = _num(info.get("beta"))
    f["n_analysts"] = _num(info.get("numberOfAnalystOpinions"))
    f["rec_mean"] = _num(info.get("recommendationMean"))
    f["short_pct_float"] = _num(info.get("shortPercentOfFloat"))
    f["summary"] = (info.get("longBusinessSummary") or "")[:600]

    # ---- statements (annual) ------------------------------------------------------
    ia, ba, ca = snap.get("income_annual", {}), snap.get("balance_annual", {}), snap.get("cashflow_annual", {})
    iq, cq = snap.get("income_quarterly", {}), snap.get("cashflow_quarterly", {})
    rev_a = _stmt_series(ia, "Total Revenue")
    f["fy_end"] = str(rev_a.index[-1].date()) if len(rev_a) else None
    f["revenue_fy"] = float(rev_a.iloc[-1]) * cv if len(rev_a) else np.nan
    f["revenue_ttm"] = _ttm(iq, "Total Revenue") * cv
    if np.isnan(f["revenue_ttm"]):
        f["revenue_ttm"] = _num(info.get("totalRevenue")) * cv
    if np.isnan(f["revenue_ttm"]):
        f["revenue_ttm"] = f["revenue_fy"]
    f["rev_cagr_3y"] = float((rev_a.iloc[-1] / rev_a.iloc[-4]) ** (1 / 3) - 1) if len(rev_a) >= 4 and rev_a.iloc[-4] > 0 else np.nan
    f["rev_growth_fy"] = float(rev_a.iloc[-1] / rev_a.iloc[-2] - 1) if len(rev_a) >= 2 and rev_a.iloc[-2] > 0 else np.nan
    # quarterly YoY (latest quarter vs same quarter a year ago)
    rev_q = _stmt_series(iq, "Total Revenue")
    f["rev_growth_q_yoy"] = float(rev_q.iloc[-1] / rev_q.iloc[-5] - 1) if len(rev_q) >= 5 and rev_q.iloc[-5] > 0 else _num(info.get("revenueGrowth"))
    gp_a, op_a = _stmt_series(ia, "Gross Profit"), _stmt_series(ia, "Operating Income")
    ebit_a = _stmt_series(ia, "EBIT")
    rnd_a, sbc_a = _stmt_series(ia, "Research And Development"), _stmt_series(ca, "Stock Based Compensation")
    fcf_a, capex_a = _stmt_series(ca, "Free Cash Flow"), _stmt_series(ca, "Capital Expenditure")
    f["gross_margin"] = _num(info.get("grossMargins"))
    if np.isnan(f["gross_margin"]) and len(gp_a) and len(rev_a):
        f["gross_margin"] = safe_div(gp_a.iloc[-1], rev_a.iloc[-1])
    f["op_margin"] = _num(info.get("operatingMargins"))
    f["ebitda_margin"] = _num(info.get("ebitdaMargins"))
    f["net_margin"] = _num(info.get("profitMargins"))
    f["gm_change_3y"] = safe_div(gp_a.iloc[-1], rev_a.iloc[-1]) - safe_div(gp_a.iloc[-4], rev_a.iloc[-4]) if len(gp_a) >= 4 and len(rev_a) >= 4 else np.nan
    f["opm_change_3y"] = safe_div(op_a.iloc[-1], rev_a.iloc[-1]) - safe_div(op_a.iloc[-4], rev_a.iloc[-4]) if len(op_a) >= 4 and len(rev_a) >= 4 else np.nan
    f["rnd_pct"] = safe_div(rnd_a.iloc[-1], rev_a.iloc[-1]) if len(rnd_a) and len(rev_a) else np.nan
    f["sbc_pct"] = safe_div(sbc_a.iloc[-1], rev_a.iloc[-1]) if len(sbc_a) and len(rev_a) else np.nan
    f["capex_pct"] = safe_div(abs(capex_a.iloc[-1]), rev_a.iloc[-1]) if len(capex_a) and len(rev_a) else np.nan
    fcf_ttm = _ttm(cq, "Free Cash Flow") * cv
    if np.isnan(fcf_ttm):
        fcf_ttm = _num(info.get("freeCashflow")) * cv
    if np.isnan(fcf_ttm) and len(fcf_a):
        fcf_ttm = float(fcf_a.iloc[-1]) * cv
    f["fcf_ttm"] = fcf_ttm
    f["fcf_margin"] = safe_div(fcf_ttm, f["revenue_ttm"])
    f["ebitda_ttm"] = _num(info.get("ebitda")) * cv
    f["ebit_ttm"] = (_ttm(iq, "EBIT") if _stmt_series(iq, "EBIT").size >= 4 else (float(ebit_a.iloc[-1]) if len(ebit_a) else np.nan)) * cv
    f["net_income_ttm"] = _num(info.get("netIncomeToCommon")) * cv
    # ---- balance sheet ------------------------------------------------------------
    bq = snap.get("balance_quarterly", {}) or ba
    debt = _stmt_series(bq, "Total Debt"); cash = _stmt_series(bq, "Cash Cash Equivalents And Short Term Investments")
    inv_cap = _stmt_series(bq, "Invested Capital"); equity = _stmt_series(bq, "Stockholders Equity")
    f["total_debt"] = (float(debt.iloc[-1]) if len(debt) else _num(info.get("totalDebt"))) * cv
    f["cash"] = (float(cash.iloc[-1]) if len(cash) else _num(info.get("totalCash"))) * cv
    f["net_debt"] = (f["total_debt"] if not np.isnan(f["total_debt"]) else 0) - (f["cash"] if not np.isnan(f["cash"]) else 0)
    f["net_debt_to_ebitda"] = safe_div(f["net_debt"], f["ebitda_ttm"]) if f["ebitda_ttm"] and f["ebitda_ttm"] > 0 else np.nan
    f["equity"] = float(equity.iloc[-1]) * cv if len(equity) else np.nan
    ic = float(inv_cap.iloc[-1]) * cv if len(inv_cap) else (f["equity"] + f["total_debt"] if not np.isnan(f["equity"]) else np.nan)
    f["invested_capital"] = ic
    f["roic"] = safe_div(f["ebit_ttm"] * (1 - 0.15), ic) if ic and ic > 0 else np.nan
    f["roe"] = _num(info.get("returnOnEquity"))
    f["roa"] = _num(info.get("returnOnAssets"))
    # ---- consensus ---------------------------------------------------------------------
    w0 = _fy0_weight(snap, asof)
    f["fy0_weight"] = w0
    rev_ccy = str(snap.get("revenue_estimate", {}).get("0y", {}).get("currency") or "USD").upper()
    eps_ccy = str(snap.get("earnings_estimate", {}).get("0y", {}).get("currency") or "USD").upper()
    rcv, rmiss = fx_conversion(rev_ccy)
    ecv, emiss = fx_conversion(eps_ccy)
    f["fx_missing"] = bool(fx_missing or rmiss or emiss)
    rev0, rev1 = _est(snap, "revenue_estimate", "0y", "avg") * rcv, _est(snap, "revenue_estimate", "+1y", "avg") * rcv
    g0, g1 = _est(snap, "revenue_estimate", "0y", "growth"), _est(snap, "revenue_estimate", "+1y", "growth")
    eps0, eps1 = _est(snap, "earnings_estimate", "0y", "avg") * ecv, _est(snap, "earnings_estimate", "+1y", "avg") * ecv
    if eps_ccy != "USD" and f["shares"] and _num(info.get("sharesOutstanding")):
        # EPS quoted per ordinary share in local currency: rescale to per-ADR
        adr_ratio = _num(info.get("sharesOutstanding")) / f["shares"]
        if adr_ratio and 0.05 < adr_ratio < 50:
            eps0, eps1 = eps0 * adr_ratio, eps1 * adr_ratio
    eg0, eg1 = _est(snap, "earnings_estimate", "0y", "growth"), _est(snap, "earnings_estimate", "+1y", "growth")
    f.update(revenue_fy0=rev0, revenue_fy1=rev1, rev_growth_fy0=g0, rev_growth_fy1=g1,
             eps_fy0=eps0, eps_fy1=eps1, eps_growth_fy0=eg0, eps_growth_fy1=eg1)
    f["revenue_ntm"] = w0 * rev0 + (1 - w0) * rev1 if not (np.isnan(rev0) or np.isnan(rev1)) else (rev0 if not np.isnan(rev0) else rev1)
    f["eps_ntm"] = w0 * eps0 + (1 - w0) * eps1 if not (np.isnan(eps0) or np.isnan(eps1)) else (eps0 if not np.isnan(eps0) else eps1)
    f["rev_growth_ntm"] = w0 * g0 + (1 - w0) * g1 if not (np.isnan(g0) or np.isnan(g1)) else (g0 if not np.isnan(g0) else g1)
    f["eps_growth_ntm"] = w0 * eg0 + (1 - w0) * eg1 if not (np.isnan(eg0) or np.isnan(eg1)) else (eg0 if not np.isnan(eg0) else eg1)
    f["ltg"] = _est(snap, "growth_estimates", "LTG", "stockTrend")
    # 2-year forward revenue CAGR from consensus
    if not np.isnan(rev1) and f["revenue_ttm"] and f["revenue_ttm"] > 0:
        yrs = 1 + w0
        f["rev_cagr_fwd2y"] = float((rev1 / f["revenue_ttm"]) ** (1 / max(yrs, 0.75)) - 1)
    else:
        f["rev_cagr_fwd2y"] = np.nan
    # revisions: current vs 30/90 days ago consensus EPS (FY1 and FY0)
    for per, tag in (("0y", "fy0"), ("+1y", "fy1")):
        cur = _est(snap, "eps_trend", per, "current")
        for days in (30, 90):
            old = _est(snap, "eps_trend", per, f"{days}daysAgo")
            f[f"eps_rev_{tag}_{days}d"] = eps_revision(cur, old)
    f["eps_rev_90d"] = np.nanmean([f["eps_rev_fy0_90d"], f["eps_rev_fy1_90d"]]) if not (np.isnan(f["eps_rev_fy0_90d"]) and np.isnan(f["eps_rev_fy1_90d"])) else np.nan
    f["eps_rev_30d"] = np.nanmean([f["eps_rev_fy0_30d"], f["eps_rev_fy1_30d"]]) if not (np.isnan(f["eps_rev_fy0_30d"]) and np.isnan(f["eps_rev_fy1_30d"])) else np.nan
    pt = snap.get("price_targets", {}) or {}
    f["target_mean"] = _num(pt.get("mean")) if pt.get("mean") else _num(info.get("targetMeanPrice"))
    f["target_median"] = _num(pt.get("median"))
    f["target_high"], f["target_low"] = _num(pt.get("high")), _num(pt.get("low"))
    f["target_upside"] = safe_div(f["target_mean"], price) - 1
    f["next_earnings"] = snap.get("next_earnings")
    # ---- valuation multiples ---------------------------------------------------------------
    ev = f["market_cap"] + f["net_debt"] if not np.isnan(f["market_cap"]) else np.nan
    f["ev"] = ev
    f["ev_used"] = ev
    f["ev_sales_ttm"] = safe_div(ev, f["revenue_ttm"])
    f["ev_sales_ntm"] = safe_div(ev, f["revenue_ntm"])
    f["ev_sales_fy1"] = safe_div(ev, rev1)
    f["ev_ebitda_ttm"] = safe_div(ev, f["ebitda_ttm"]) if f["ebitda_ttm"] and f["ebitda_ttm"] > 0 else np.nan
    f["pe_ttm"] = _num(info.get("trailingPE"))
    f["pe_ntm"] = safe_div(price, f["eps_ntm"]) if f["eps_ntm"] and f["eps_ntm"] > 0 else np.nan
    f["pe_fy1"] = safe_div(price, eps1) if eps1 and eps1 > 0 else np.nan
    f["peg"] = safe_div(f["pe_ntm"], f["eps_growth_ntm"] * 100) if f["eps_growth_ntm"] and f["eps_growth_ntm"] > 0.02 else np.nan
    f["ev_sales_to_growth"] = safe_div(f["ev_sales_ntm"], f["rev_growth_ntm"] * 100) if f["rev_growth_ntm"] and f["rev_growth_ntm"] > 0.02 else np.nan
    f["fcf_yield"] = safe_div(fcf_ttm, f["market_cap"])
    f["earnings_yield_ntm"] = safe_div(f["eps_ntm"], price)
    f["pb"] = _num(info.get("priceToBook"))
    f["rule_of_40"] = (f["rev_growth_ntm"] if not np.isnan(f["rev_growth_ntm"]) else f["rev_growth_q_yoy"]) + f["fcf_margin"] if not np.isnan(f["fcf_margin"]) else np.nan
    # fundamental momentum: latest-quarter growth vs trailing-twelve-month growth (point-in-time validated),
    # and its consensus analogue: forward (NTM) growth vs trailing growth
    rev_ttm_growth = np.nan
    if len(rev_q) >= 8 and rev_q.iloc[-8:-4].sum() > 0:
        rev_ttm_growth = float(rev_q.iloc[-4:].sum() / rev_q.iloc[-8:-4].sum() - 1)
    elif not np.isnan(f["rev_growth_fy"]):
        rev_ttm_growth = f["rev_growth_fy"]
    f["rev_growth_ttm"] = rev_ttm_growth
    f["growth_accel"] = f["rev_growth_q_yoy"] - rev_ttm_growth if not (np.isnan(f["rev_growth_q_yoy"]) or np.isnan(rev_ttm_growth)) else np.nan
    f["fwd_accel"] = f["rev_growth_ntm"] - rev_ttm_growth if not (np.isnan(f["rev_growth_ntm"]) or np.isnan(rev_ttm_growth)) else np.nan
    # ---- EDGAR cross-check / backfill --------------------------------------------------------
    f["in_edgar"] = bool(ed.get("in_edgar"))
    f["cik"] = ed.get("cik")
    f["edgar_rev_growth_fy"] = _num(ed.get("fy_revenue_growth"))
    f["edgar_rev_cagr_3y"] = _num(ed.get("rev_cagr_3y"))
    f["edgar_q_rev_yoy"] = _num(ed.get("q_rev_yoy_latest"))
    f["edgar_fcf_fy"] = _num(ed.get("fy_fcf"))
    f["edgar_sbc_fy"] = _num(ed.get("fy_sbc"))
    f["edgar_buybacks_fy"] = _num(ed.get("fy_buybacks"))
    f["filings_30d"] = int(ed.get("material_filings_30d") or 0)
    # through-cycle FCF / operating margins: last 5 fiscal years from EDGAR XBRL (ratios, so no FX needed)
    ann = ed.get("annual") or {}
    f["fcf_margin_5y"], f["op_margin_5y"], f["n_fy_history"] = np.nan, np.nan, 0
    try:
        rev_e = pd.Series(ann.get("revenue", {})).astype(float); rev_e.index = pd.to_datetime(rev_e.index)
        if len(rev_e):
            ocf_e = pd.Series(ann.get("ocf", {})).astype(float); ocf_e.index = pd.to_datetime(ocf_e.index)
            cap_e = pd.Series(ann.get("capex", {})).astype(float); cap_e.index = pd.to_datetime(cap_e.index)
            opi_e = pd.Series(ann.get("operating_income", {})).astype(float); opi_e.index = pd.to_datetime(opi_e.index)
            m = pd.concat([rev_e.rename("rev"), ocf_e.rename("ocf"), cap_e.abs().rename("capex"), opi_e.rename("opi")], axis=1)
            m = m[m["rev"] > 0].sort_index().tail(5)
            fm = ((m["ocf"] - m["capex"]) / m["rev"]).dropna()
            om = (m["opi"] / m["rev"]).dropna()
            f["fcf_margin_5y"] = float(fm.mean()) if len(fm) >= 3 else np.nan
            f["op_margin_5y"] = float(om.mean()) if len(om) >= 3 else np.nan
            f["n_fy_history"] = int(len(m))
    except Exception:
        pass
    if np.isnan(f["fcf_margin_5y"]) and len(fcf_a) >= 3 and len(rev_a) >= 3:
        j = pd.concat([fcf_a.rename("f"), rev_a.rename("r")], axis=1).dropna().tail(5)
        if len(j) >= 3:
            f["fcf_margin_5y"] = float((j["f"] / j["r"]).mean()); f["n_fy_history"] = int(len(j))
    f["latest_10k_url"] = (ed.get("latest_10k") or ed.get("latest_20f") or {}).get("url")
    f["latest_10k_date"] = (ed.get("latest_10k") or ed.get("latest_20f") or {}).get("date")
    f["latest_10q_date"] = (ed.get("latest_10q") or {}).get("date")
    if np.isnan(f["rev_cagr_3y"]) and not np.isnan(f["edgar_rev_cagr_3y"]):
        f["rev_cagr_3y"] = f["edgar_rev_cagr_3y"]
    if np.isnan(f["rev_growth_q_yoy"]) and not np.isnan(f["edgar_q_rev_yoy"]):
        f["rev_growth_q_yoy"] = f["edgar_q_rev_yoy"]
    if (np.isnan(f["sbc_pct"]) and not np.isnan(f["edgar_sbc_fy"]) and f["revenue_fy"]):
        f["sbc_pct"] = safe_div(f["edgar_sbc_fy"], f["revenue_fy"])
    # ---- price-based -----------------------------------------------------------------------
    if ticker in px.columns:
        s = px[ticker].dropna()
        if len(s) > 30:
            last = s.iloc[-1]
            def _ret(days):
                return float(last / s.iloc[-days - 1] - 1) if len(s) > days else np.nan
            f["ret_1m"], f["ret_3m"], f["ret_6m"], f["ret_12m"] = _ret(21), _ret(63), _ret(126), _ret(252)
            r = s.pct_change().dropna().tail(252)
            f["vol_1y"] = float(r.std() * np.sqrt(252))
            s1 = s.tail(252)
            f["max_dd_1y"] = float((s1 / s1.cummax() - 1).min())
            f["pct_52w_range"] = float((last - s1.min()) / (s1.max() - s1.min())) if s1.max() > s1.min() else np.nan
            f["vs_200dma"] = float(last / s.tail(200).mean() - 1)
            f["vs_50dma"] = float(last / s.tail(50).mean() - 1)
            if bench in px.columns:
                b = px[bench].dropna()
                f["rel_6m_vs_soxx"] = f["ret_6m"] - (float(b.iloc[-1] / b.iloc[-127] - 1) if len(b) > 126 else np.nan)
            f["price_history_days"] = int(len(s))
            # validated momentum forms: 12-1 month momentum scaled by volatility, and proximity to the 52-week high
            f["mom_12_1"] = float(s.iloc[-22] / s.iloc[-253] - 1) if len(s) > 253 else np.nan
            f["mom_12_1_volscaled"] = f["mom_12_1"] / f["vol_1y"] if not np.isnan(f["mom_12_1"]) and f["vol_1y"] > 0 else np.nan
            f["high_52w_prox"] = float(last / s1.max())
    return f


def build_features(yahoo_snaps: dict[str, dict], edgar_snaps: dict[str, dict], px: pd.DataFrame,
                   asof: datetime | None = None) -> pd.DataFrame:
    asof = asof or datetime.today()
    uni = universe_frame()
    rows = []
    for t in uni.index:
        snap = yahoo_snaps.get(t) or {}
        if not snap.get("ok"):
            log.warning("no Yahoo snapshot for %s; skipped", t)
            continue
        try:
            rows.append(features_for(t, snap, edgar_snaps.get(t) or {}, px, asof))
        except Exception as e:                    # pragma: no cover
            log.warning("features failed for %s: %s", t, e)
    df = pd.DataFrame(rows).set_index("ticker")
    df = df.join(uni[["segment", "sub_segment", "description", "is_adr", "segment_rank"]])
    df["industry_mom"] = df.groupby("segment")["ret_6m"].transform("mean")
    return df
