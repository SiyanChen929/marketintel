"""SEC EDGAR adapter: XBRL company facts + filing index.

Uses the free, official JSON APIs (no key required, but a User-Agent is mandatory):
  https://www.sec.gov/files/company_tickers.json
  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
  https://data.sec.gov/submissions/CIK##########.json
SEC fair-use limit is 10 requests/second; we stay well under it.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from ..config.settings import CONFIG, DATA_CACHE, SEC_USER_AGENT
from ..utils.io import file_age_hours, log, read_json, write_json

EDGAR_DIR = DATA_CACHE / "edgar"
EDGAR_DIR.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# Concept aliases (US-GAAP first, then IFRS for foreign private issuers).
CONCEPTS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "ifrs-full:Revenue"],
    "gross_profit": ["GrossProfit", "ifrs-full:GrossProfit"],
    "rnd": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
            "ifrs-full:ResearchAndDevelopmentExpense"],
    "operating_income": ["OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "ifrs-full:ProfitLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "ifrs-full:CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               "ifrs-full:Equity"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
             "ifrs-full:CashAndCashEquivalents"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
    "total_assets": ["Assets", "ifrs-full:Assets"],
    "inventory": ["InventoryNet", "ifrs-full:Inventories"],
    "eps_diluted": ["EarningsPerShareDiluted", "ifrs-full:DilutedEarningsLossPerShare"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}
FLOW_ITEMS = {"revenue", "gross_profit", "rnd", "operating_income", "net_income", "ocf", "capex", "sbc",
              "buybacks", "dividends", "eps_diluted", "shares_diluted"}


def _get(url: str, timeout: int = 30) -> dict | None:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (403, 429):
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except requests.RequestException as e:      # pragma: no cover
            log.warning("EDGAR %s: %s", url, e)
            time.sleep(2)
    return None


def cik_map(max_age_days: int = 7) -> dict[str, dict]:
    path = EDGAR_DIR / "company_tickers.json"
    if file_age_hours(path) < max_age_days * 24:
        data = read_json(path)
    else:
        data = _get("https://www.sec.gov/files/company_tickers.json")
        if data:
            write_json(data, path)
        else:
            data = read_json(path, {})
    return {v["ticker"].upper(): {"cik": int(v["cik_str"]), "name": v["title"]} for v in (data or {}).values()}


def company_facts(cik: int, max_age_days: int | None = None) -> dict | None:
    max_age = (CONFIG.edgar_cache_days if max_age_days is None else max_age_days) * 24
    path = EDGAR_DIR / f"facts_{cik}.json"
    if file_age_hours(path) < max_age:
        return read_json(path)
    data = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    if data:
        write_json(data, path)
        time.sleep(0.15)
    return data


def submissions(cik: int, max_age_days: int | None = None) -> dict | None:
    max_age = (CONFIG.edgar_cache_days if max_age_days is None else max_age_days) * 24
    path = EDGAR_DIR / f"subs_{cik}.json"
    if file_age_hours(path) < max_age:
        return read_json(path)
    data = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if data:
        # keep only what we need to keep the cache small
        rec = data.get("filings", {}).get("recent", {})
        slim = {k: data.get(k) for k in ("cik", "name", "sic", "sicDescription", "fiscalYearEnd", "stateOfIncorporation")}
        slim["recent"] = {k: rec.get(k, [])[:120] for k in ("form", "filingDate", "accessionNumber", "primaryDocument", "reportDate")}
        write_json(slim, path)
        time.sleep(0.15)
        return slim
    return None


def _concept_series(facts: dict, aliases: list[str]) -> list[dict]:
    """Return the raw fact list for the first alias with data (USD or shares)."""
    for alias in aliases:
        taxo, _, name = alias.partition(":") if ":" in alias else ("us-gaap", "", alias)
        node = facts.get("facts", {}).get(taxo, {}).get(name)
        if not node:
            continue
        units = node.get("units", {})
        for u in ("USD", "USD/shares", "shares", "EUR", "TWD", "JPY"):
            if u in units and units[u]:
                return [dict(v, unit=u) for v in units[u]]
    return []


def annual_series(facts: dict, item: str) -> pd.Series:
    """Annual fiscal-year values (10-K / 20-F) for an item, indexed by fiscal year end."""
    vals = _concept_series(facts, CONCEPTS[item])
    if not vals:
        return pd.Series(dtype=float)
    rows = []
    for v in vals:
        form = v.get("form", "")
        if form not in ("10-K", "10-K/A", "20-F", "20-F/A", "40-F"):
            continue
        if item in FLOW_ITEMS:
            # keep ~annual durations only
            try:
                d = (pd.Timestamp(v["end"]) - pd.Timestamp(v["start"])).days
            except Exception:
                continue
            if d < 340 or d > 380:
                continue
        elif v.get("fp") != "FY":
            continue
        rows.append((pd.Timestamp(v["end"]), float(v["val"]), v.get("filed", "")))
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["end", "val", "filed"]).sort_values(["end", "filed"])
    s = df.groupby("end")["val"].last()           # latest restatement wins
    return s[~s.index.duplicated()].sort_index()


def quarterly_series(facts: dict, item: str) -> pd.Series:
    """Quarterly flow values from 10-Q/10-K (derives Q4 = FY - 9M when needed)."""
    vals = _concept_series(facts, CONCEPTS[item])
    rows = []
    for v in vals:
        if v.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
            continue
        try:
            d = (pd.Timestamp(v["end"]) - pd.Timestamp(v["start"])).days
        except Exception:
            continue
        if 80 <= d <= 100:
            rows.append((pd.Timestamp(v["end"]), float(v["val"]), v.get("filed", "")))
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["end", "val", "filed"]).sort_values(["end", "filed"])
    return df.groupby("end")["val"].last().sort_index()


def edgar_snapshot(ticker: str, cmap: dict | None = None) -> dict:
    """Standardised EDGAR view for one issuer: latest annual line items, multi-year revenue,
    filing dates and links. Empty-but-well-formed if the company is not in EDGAR."""
    cmap = cmap or cik_map()
    out: dict[str, Any] = {"ticker": ticker, "in_edgar": False}
    meta = cmap.get(ticker.upper())
    if not meta:
        return out
    cik = meta["cik"]
    out.update({"in_edgar": True, "cik": cik, "sec_name": meta["name"],
                "edgar_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=10-K&dateb=&owner=include&count=40"})
    subs = submissions(cik)
    if subs:
        out["sic"] = subs.get("sic"); out["sic_desc"] = subs.get("sicDescription")
        out["fiscal_year_end"] = subs.get("fiscalYearEnd")
        rec = subs.get("recent", {})
        filings = pd.DataFrame(rec)
        if not filings.empty:
            filings["filingDate"] = pd.to_datetime(filings["filingDate"])
            cutoff = datetime.today() - timedelta(days=30)
            recent = filings[filings["filingDate"] >= cutoff]
            out["filings_30d"] = recent[["form", "filingDate"]].assign(filingDate=lambda d: d["filingDate"].dt.strftime("%Y-%m-%d")).to_dict("records")
            material = recent[recent["form"].isin(["8-K", "10-Q", "10-K", "20-F", "6-K", "S-1", "S-3", "424B5", "SC 13D", "SC 13G"])]
            out["material_filings_30d"] = len(material)
            for form in ("10-K", "20-F", "10-Q"):
                m = filings[filings["form"] == form]
                if not m.empty:
                    r = m.iloc[0]
                    acc = str(r["accessionNumber"]).replace("-", "")
                    out[f"latest_{form.replace('-', '').lower()}"] = {
                        "date": r["filingDate"].strftime("%Y-%m-%d"),
                        "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{r['primaryDocument']}"}
    facts = company_facts(cik)
    if not facts:
        return out
    annual: dict[str, dict] = {}
    for item in CONCEPTS:
        s = annual_series(facts, item)
        if not s.empty:
            annual[item] = {str(k.date()): v for k, v in s.tail(6).items()}
    out["annual"] = annual
    rev = annual_series(facts, "revenue")
    if len(rev) >= 2:
        out["fy_revenue_latest"] = float(rev.iloc[-1]); out["fy_end_latest"] = str(rev.index[-1].date())
        out["fy_revenue_growth"] = float(rev.iloc[-1] / rev.iloc[-2] - 1) if rev.iloc[-2] else np.nan
        if len(rev) >= 4 and rev.iloc[-4] > 0:
            out["rev_cagr_3y"] = float((rev.iloc[-1] / rev.iloc[-4]) ** (1 / 3) - 1)
    q = quarterly_series(facts, "revenue")
    if not q.empty:
        out["quarterly_revenue"] = {str(k.date()): v for k, v in q.tail(12).items()}
        if len(q) >= 5:
            out["q_rev_yoy_latest"] = float(q.iloc[-1] / q.iloc[-5] - 1) if q.iloc[-5] else np.nan
    def _last(item):
        s = annual_series(facts, item)
        return float(s.iloc[-1]) if not s.empty else np.nan
    ocf, capex = _last("ocf"), _last("capex")
    out["fy_fcf"] = ocf - abs(capex) if not (np.isnan(ocf) or np.isnan(capex)) else np.nan
    out["fy_sbc"] = _last("sbc"); out["fy_rnd"] = _last("rnd"); out["fy_buybacks"] = _last("buybacks")
    out["fy_op_income"] = _last("operating_income"); out["fy_net_income"] = _last("net_income")
    return out


def fetch_all_edgar(tickers: list[str]) -> dict[str, dict]:
    cmap = cik_map()
    out = {}
    for i, t in enumerate(tickers, 1):
        try:
            out[t] = edgar_snapshot(t, cmap)
        except Exception as e:                    # pragma: no cover
            log.warning("EDGAR %s failed: %s", t, e)
            out[t] = {"ticker": t, "in_edgar": False, "error": str(e)}
        if i % 25 == 0:
            log.info("edgar %d/%d", i, len(tickers))
    return out
