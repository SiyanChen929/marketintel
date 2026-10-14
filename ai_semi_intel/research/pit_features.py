"""Point-in-time (PIT) feature reconstruction from SEC EDGAR XBRL company facts.

Every fact in the companyfacts API carries the date it was *filed*. For any historical
date t we therefore know exactly what an analyst could have seen: we keep only facts with
filed <= t, take the latest filed value for each period (restatements known by t are used,
later ones are not), rebuild quarterly flows (Q4 = FY - Q1 - Q2 - Q3), and form trailing
twelve-month (TTM) aggregates. Market values use split-adjusted prices times the cover-page
share count from the 'dei' taxonomy, re-based to the same split basis.

This gives a look-ahead-free history for the value (trailing), growth (trailing), quality
and momentum pillars. Consensus-based fields cannot be reconstructed from free data and are
deliberately absent.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..config.settings import DATA_CACHE
from ..config.universe import ADR_TICKERS, universe_frame
from ..data.edgar import cik_map
from ..utils.io import log

EDGAR_DIR = DATA_CACHE / "edgar"
PRICE_DIR = DATA_CACHE / "prices"

ALIASES: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet", "RevenuesNetOfInterestExpense"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    "gross_profit": ["GrossProfit"],
    "op_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "rnd": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "st_inv": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt", "ConvertibleNotesPayableNoncurrent"],
    "st_debt": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "CommercialPaper", "ConvertibleNotesPayableCurrent"],
    "assets": ["Assets"],
    "shares": ["dei:EntityCommonStockSharesOutstanding"],
    "shares_wavg": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
}
FLOWS = {"revenue", "cogs", "gross_profit", "op_income", "net_income", "rnd", "ocf", "capex", "sbc"}
FORMS_OK = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F"}


def _tidy(facts: dict, aliases: list[str]) -> pd.DataFrame:
    rows = []
    for alias in aliases:
        taxo, _, name = alias.partition(":") if ":" in alias else ("us-gaap", "", alias)
        node = facts.get("facts", {}).get(taxo, {}).get(name)
        if not node:
            continue
        for unit, vals in node.get("units", {}).items():
            if unit not in ("USD", "shares", "USD/shares"):
                continue
            for v in vals:
                if v.get("form") not in FORMS_OK:
                    continue
                rows.append((v.get("start"), v["end"], float(v["val"]), v.get("filed"), v.get("fp")))
    if not rows:
        return pd.DataFrame(columns=["start", "end", "val", "filed", "fp"])
    df = pd.DataFrame(rows, columns=["start", "end", "val", "filed", "fp"])
    df["end"] = pd.to_datetime(df["end"]); df["filed"] = pd.to_datetime(df["filed"])
    df["start"] = pd.to_datetime(df["start"], errors="coerce")
    df = df.drop_duplicates(subset=["start", "end", "filed", "val"]).sort_values(["end", "filed"])
    return df


class CompanyPIT:
    """All facts for one issuer, pre-processed into numpy arrays for fast as-of queries.

    Point-in-time rule: for every reporting period we keep the *first-filed* value (what an
    analyst saw at the time) together with its filing date; a value becomes visible at t only
    if filed <= t. Q4 flows are derived as FY - (Q1+Q2+Q3) and inherit the 10-K filing date.
    """

    def __init__(self, ticker: str, cik: int):
        self.ticker, self.cik = ticker, cik
        path = EDGAR_DIR / f"facts_{cik}.json"
        facts = json.load(open(path)) if path.exists() else {}
        self.q: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}    # flows: (ends, vals, filed)
        self.a: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}    # annual flows
        self.i: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}    # instants
        for k, aliases in ALIASES.items():
            d = _tidy(facts, aliases)
            if d.empty:
                continue
            if k in FLOWS:
                d = d.assign(dur=(d["end"] - d["start"]).dt.days)
                q = d[(d["dur"] >= 80) & (d["dur"] <= 100)].sort_values("filed").groupby("end").first()
                a = d[(d["dur"] >= 350) & (d["dur"] <= 380)].sort_values("filed").groupby("end").first()
                derived = {}
                for e, row in a.iterrows():
                    prior = q[(q.index > e - pd.Timedelta(days=370)) & (q.index < e)]
                    if len(prior) == 3 and e not in q.index:
                        derived[e] = (row["val"] - prior["val"].sum(), max(row["filed"], prior["filed"].max()))
                if derived:
                    dq = pd.DataFrame({"val": {e: v for e, (v, _) in derived.items()}, "filed": {e: f for e, (_, f) in derived.items()}})
                    q = pd.concat([q[["val", "filed"]], dq]).sort_index()
                q = q[~q.index.duplicated()].sort_index()
                self.q[k] = (q.index.values.astype("datetime64[ns]"), q["val"].values.astype(float), q["filed"].values.astype("datetime64[ns]"))
                self.a[k] = (a.index.values.astype("datetime64[ns]"), a["val"].values.astype(float), a["filed"].values.astype("datetime64[ns]"))
            else:
                inst = d.sort_values("filed").groupby("end").first().sort_index()
                self.i[k] = (inst.index.values.astype("datetime64[ns]"), inst["val"].values.astype(float), inst["filed"].values.astype("datetime64[ns]"))
        self.ok = "revenue" in self.q and len(self.q["revenue"][0]) >= 4

    # ---- as-of queries ---------------------------------------------------------------------
    def ttm(self, k: str, t: pd.Timestamp, lag_days: int = 0) -> tuple[float, pd.Timestamp | None]:
        """Sum of the last four consecutive quarters ending <= t - lag that were filed by t."""
        t64 = np.datetime64(t, "ns"); ref = np.datetime64(t - pd.Timedelta(days=lag_days), "ns")
        if k in self.q:
            ends, vals, filed = self.q[k]
            m = (filed <= t64) & (ends <= ref)
            if m.sum() >= 4:
                e4, v4 = ends[m][-4:], vals[m][-4:]
                span = (e4[-1] - e4[0]) / np.timedelta64(1, "D")
                fresh = (ref - e4[-1]) / np.timedelta64(1, "D")
                if 240 <= span <= 300 and fresh <= 200:
                    return float(v4.sum()), pd.Timestamp(e4[-1])
        if k in self.a:
            ends, vals, filed = self.a[k]
            m = (filed <= t64) & (ends <= ref)
            if m.any():
                e, v = ends[m][-1], vals[m][-1]
                if (ref - e) / np.timedelta64(1, "D") <= 456:
                    return float(v), pd.Timestamp(e)
        return np.nan, None

    def quarterly_known(self, k: str, t: pd.Timestamp) -> tuple[np.ndarray, np.ndarray]:
        if k not in self.q:
            return np.array([], dtype="datetime64[ns]"), np.array([])
        ends, vals, filed = self.q[k]
        t64 = np.datetime64(t, "ns")
        m = (filed <= t64) & (ends <= t64)
        return ends[m], vals[m]

    def instant(self, k: str, t: pd.Timestamp, lag_days: int = 0) -> float:
        if k not in self.i:
            return np.nan
        ends, vals, filed = self.i[k]
        t64 = np.datetime64(t, "ns"); ref = np.datetime64(t - pd.Timedelta(days=lag_days), "ns")
        m = (filed <= t64) & (ends <= ref)
        if not m.any():
            return np.nan
        e, v = ends[m][-1], vals[m][-1]
        return float(v) if (ref - e) / np.timedelta64(1, "D") <= 200 else np.nan

    def shares_asof(self, t: pd.Timestamp) -> tuple[float, pd.Timestamp | None]:
        for k in ("shares", "shares_wavg"):
            if k not in self.i:
                continue
            ends, vals, filed = self.i[k]
            t64 = np.datetime64(t, "ns")
            m = (filed <= t64) & (ends <= t64)
            if m.any() and (t64 - ends[m][-1]) / np.timedelta64(1, "D") <= 200:
                return float(vals[m][-1]), pd.Timestamp(ends[m][-1])
        return np.nan, None


# ------------------------------------------------------------------------- prices & splits
@lru_cache(maxsize=1)
def load_prices() -> dict[str, pd.DataFrame]:
    out = {}
    for k in ("close", "adj_close", "volume"):
        p = PRICE_DIR / f"long_{k}.parquet"
        out[k] = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    sp = PRICE_DIR / "splits.json"
    out["splits"] = json.load(open(sp)) if sp.exists() else {}
    return out


def split_factor(ticker: str, date: pd.Timestamp, splits: dict) -> float:
    """Product of split ratios after `date` (to re-base a historical share count to today's basis)."""
    f = 1.0
    for d, r in splits.get(ticker, {}).items():
        if pd.Timestamp(d) > date and r and r > 0:
            f *= r
    return f


def price_features(ticker: str, t: pd.Timestamp, adj: pd.DataFrame, bench: str = "SOXX") -> dict:
    out = {}
    if ticker not in adj.columns:
        return out
    s = adj[ticker].dropna(); s = s[s.index <= t]
    if len(s) < 260:
        return out
    last = s.iloc[-1]
    out["mom_12_1"] = float(s.iloc[-22] / s.iloc[-253] - 1)
    out["ret_6m"] = float(last / s.iloc[-127] - 1)
    out["ret_1m"] = float(last / s.iloc[-22] - 1)
    out["vs_200dma"] = float(last / s.tail(200).mean() - 1)
    r = s.pct_change().dropna().tail(252)
    out["vol_1y"] = float(r.std() * np.sqrt(252))
    s1 = s.tail(252); out["max_dd_1y"] = float((s1 / s1.cummax() - 1).min())
    out["high_52w_prox"] = float(last / s1.max())
    out["mom_6_1"] = float(s.iloc[-22] / s.iloc[-127] - 1)
    out["mom_12_1_volscaled"] = out["mom_12_1"] / out["vol_1y"] if out["vol_1y"] > 0 else np.nan
    out["dollar_vol_proxy"] = float(last)
    if bench in adj.columns:
        b = adj[bench].dropna(); b = b[b.index <= t]
        if len(b) > 127:
            out["rel_6m_vs_soxx"] = out["ret_6m"] - float(b.iloc[-1] / b.iloc[-127] - 1)
    out["price_adj"] = float(last)
    return out


# ------------------------------------------------------------------------ feature builder
def features_asof(c: CompanyPIT, t: pd.Timestamp, prices: dict) -> dict | None:
    rev, rev_end = c.ttm("revenue", t)
    if np.isnan(rev) or rev <= 0 or rev_end is None:
        return None
    if (t - rev_end).days > 200:                      # stale filer
        return None
    f = {"ticker": c.ticker, "date": t, "rev_ttm": rev, "latest_q_end": rev_end}
    rev_1y, _ = c.ttm("revenue", t, lag_days=365)
    rev_3y, _ = c.ttm("revenue", t, lag_days=365 * 3)
    f["rev_growth_ttm"] = rev / rev_1y - 1 if rev_1y and rev_1y > 0 else np.nan
    f["rev_cagr_3y"] = (rev / rev_3y) ** (1 / 3) - 1 if rev_3y and rev_3y > 0 else np.nan
    qe, qv = c.quarterly_known("revenue", t)
    f["rev_q_yoy"] = float(qv[-1] / qv[-5] - 1) if len(qv) >= 5 and qv[-5] > 0 and (t - pd.Timestamp(qe[-1])).days <= 200 else np.nan
    f["growth_accel"] = f["rev_q_yoy"] - f["rev_growth_ttm"] if not (np.isnan(f["rev_q_yoy"]) or np.isnan(f["rev_growth_ttm"])) else np.nan
    gp, _ = c.ttm("gross_profit", t)
    if np.isnan(gp):
        cogs, _ = c.ttm("cogs", t); gp = rev - cogs if not np.isnan(cogs) else np.nan
    opi, _ = c.ttm("op_income", t); ni, _ = c.ttm("net_income", t)
    ocf, _ = c.ttm("ocf", t); capex, _ = c.ttm("capex", t); sbc, _ = c.ttm("sbc", t); rnd, _ = c.ttm("rnd", t)
    fcf = ocf - abs(capex) if not (np.isnan(ocf) or np.isnan(capex)) else (ocf if not np.isnan(ocf) else np.nan)
    f.update(gross_margin=gp / rev if not np.isnan(gp) else np.nan, op_margin=opi / rev if not np.isnan(opi) else np.nan,
             net_margin=ni / rev if not np.isnan(ni) else np.nan, fcf_margin=fcf / rev if not np.isnan(fcf) else np.nan,
             sbc_pct=sbc / rev if not np.isnan(sbc) else np.nan, rnd_pct=rnd / rev if not np.isnan(rnd) else np.nan,
             capex_pct=abs(capex) / rev if not np.isnan(capex) else np.nan)
    opi_3y, _ = c.ttm("op_income", t, lag_days=365 * 3)
    f["opm_change_3y"] = (opi / rev) - (opi_3y / rev_3y) if not any(np.isnan(x) for x in (opi, opi_3y)) and rev_3y and rev_3y > 0 else np.nan
    # one-year-lagged flows for fundamental-momentum factors
    opi_1y, _ = c.ttm("op_income", t, lag_days=365); ni_1y, _ = c.ttm("net_income", t, lag_days=365)
    gp_1y, _ = c.ttm("gross_profit", t, lag_days=365)
    if np.isnan(gp_1y):
        cogs_1y, _ = c.ttm("cogs", t, lag_days=365); gp_1y = rev_1y - cogs_1y if not (np.isnan(cogs_1y) or np.isnan(rev_1y)) else np.nan
    f.update(ni_ttm=ni, opi_ttm=opi, ocf_ttm=ocf, capex_ttm=capex, rev_ttm_1y=rev_1y, opi_ttm_1y=opi_1y, ni_ttm_1y=ni_1y)
    f["opm_change_1y"] = (opi / rev) - (opi_1y / rev_1y) if not any(np.isnan(x) for x in (opi, opi_1y)) and rev_1y and rev_1y > 0 else np.nan
    f["gm_change_1y"] = (gp / rev) - (gp_1y / rev_1y) if not any(np.isnan(x) for x in (gp, gp_1y)) and rev_1y and rev_1y > 0 else np.nan
    f["opi_growth"] = opi / opi_1y - 1 if not any(np.isnan(x) for x in (opi, opi_1y)) and opi_1y > 0 else np.nan
    f["ni_growth"] = ni / ni_1y - 1 if not any(np.isnan(x) for x in (ni, ni_1y)) and ni_1y > 0 else np.nan
    assets = c.instant("assets", t); assets_1y = c.instant("assets", t, lag_days=365)
    f["total_assets"] = assets
    f["asset_growth"] = assets / assets_1y - 1 if not (np.isnan(assets) or np.isnan(assets_1y)) and assets_1y > 0 else np.nan
    f["accruals"] = (ni - ocf) / assets if not any(np.isnan(x) for x in (ni, ocf, assets)) and assets > 0 else np.nan
    equity = c.instant("equity", t); cash = c.instant("cash", t); sti = c.instant("st_inv", t)
    ltd = c.instant("lt_debt", t); std = c.instant("st_debt", t)
    debt = (0 if np.isnan(ltd) else ltd) + (0 if np.isnan(std) else std)
    cash_tot = (0 if np.isnan(cash) else cash) + (0 if np.isnan(sti) else sti)
    f["net_debt"] = debt - cash_tot
    ic = (equity if not np.isnan(equity) else 0) + debt
    f["roic"] = opi * 0.85 / ic if not np.isnan(opi) and ic > 0 else np.nan
    f["net_debt_to_opinc"] = f["net_debt"] / opi if not np.isnan(opi) and opi > 0 else np.nan
    f["rule_of_40"] = f["rev_growth_ttm"] + f["fcf_margin"] if not (np.isnan(f["rev_growth_ttm"]) or np.isnan(f["fcf_margin"])) else np.nan
    # market values
    close = prices["close"]
    if c.ticker in close.columns:
        s = close[c.ticker].dropna(); s = s[s.index <= t]
        sh, sh_date = c.shares_asof(t)
        if len(s) and not np.isnan(sh) and sh_date is not None:
            px = float(s.iloc[-1])
            mcap = px * sh * split_factor(c.ticker, sh_date, prices["splits"])
            f["market_cap"] = mcap
            f["ev"] = mcap + f["net_debt"]
            f["ev_sales"] = f["ev"] / rev
            f["earnings_yield"] = ni / mcap if not np.isnan(ni) else np.nan
            f["fcf_yield"] = fcf / mcap if not np.isnan(fcf) else np.nan
            f["ev_sales_to_growth"] = f["ev_sales"] / (f["rev_growth_ttm"] * 100) if f["rev_growth_ttm"] and f["rev_growth_ttm"] > 0.02 else np.nan
    f.update(price_features(c.ticker, t, prices["adj_close"]))
    return f


def month_ends(start: str = "2012-01-31", end: str | None = None) -> list[pd.Timestamp]:
    adj = load_prices()["adj_close"]
    last = adj.index[-1] if end is None else pd.Timestamp(end)
    me = pd.date_range(start, last, freq="ME")
    # snap to last trading day <= month end
    idx = adj.index
    out = []
    for d in me:
        pos = idx.searchsorted(d, side="right") - 1
        if pos >= 0:
            out.append(idx[pos])
    return sorted(set(out))


def build_panel(start: str = "2012-01-31", exclude_adr: bool = True) -> pd.DataFrame:
    prices = load_prices()
    uni = universe_frame()
    cmap = cik_map()
    dates = month_ends(start)
    rows = []
    for t in uni.index:
        if exclude_adr and t in ADR_TICKERS:
            continue
        meta = cmap.get(t)
        if not meta:
            continue
        c = CompanyPIT(t, meta["cik"])
        if not c.ok:
            log.warning("no EDGAR revenue facts for %s", t); continue
        n = 0
        for d in dates:
            f = features_asof(c, d, prices)
            if f:
                f["segment"] = uni.loc[t, "segment"]; rows.append(f); n += 1
        log.info("PIT %s: %d month-ends", t, n)
    panel = pd.DataFrame(rows)
    return panel
