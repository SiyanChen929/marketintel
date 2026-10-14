"""SEC EDGAR: the real-time 'current filings' Atom feed plus a full-text-search backfill per universe CIK.

The current feed (``browse-edgar?action=getcurrent``) carries the 8-K item codes in its summary, which is
enough to classify the filing without downloading the document. EFTS (``efts.sec.gov``) is used as a
safety net (startup / gaps) and for forms the current feed is not polled for.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import feedparser

from .. import config
from .base import Http, RawItem, clean_html, log, now_utc, to_utc

CURRENT_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&count=100&output=atom"
EFTS_URL = "https://efts.sec.gov/LATEST/search-index?q=%22%22&forms={forms}&ciks={ciks}&dateRange=custom&startdt={start}&enddt={end}"
CURRENT_FORMS = ("8-K", "10-Q", "10-K", "SC 13D", "424B5")
BACKFILL_FORMS = ("8-K", "10-Q", "10-K", "SC 13D", "424B5", "424B2")   # EFTS rejects form names containing "/"

ITEM_DESCRIPTIONS = {
    "1.01": "Entry into a Material Definitive Agreement", "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership", "1.05": "Material Cybersecurity Incident",
    "2.01": "Completion of Acquisition or Disposition of Assets", "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation", "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities", "2.06": "Material Impairments",
    "3.01": "Notice of Delisting / Continued Listing Rule", "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders", "4.01": "Change in Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements", "5.01": "Change in Control of Registrant",
    "5.02": "Departure/Election of Directors or Officers", "5.03": "Amendments to Articles/Bylaws; Fiscal Year Change",
    "5.07": "Submission of Matters to a Vote of Security Holders", "5.08": "Shareholder Director Nominations",
    "7.01": "Regulation FD Disclosure", "8.01": "Other Events", "9.01": "Financial Statements and Exhibits",
}
# item -> (event_type, direction prior, materiality)
ITEM_EVENT = {
    "2.02": ("earnings", 0, 0.9), "1.01": ("contract_customer", 1, 0.7), "1.02": ("contract_customer", -1, 0.6),
    "1.03": ("litigation_investigation", -2, 1.0), "1.05": ("litigation_investigation", -1, 0.8), "2.01": ("m_and_a", 0, 0.8),
    "2.03": ("capital_raise", 0, 0.6), "2.04": ("capital_raise", -1, 0.8), "2.05": ("management", -1, 0.7),
    "2.06": ("guidance", -2, 0.9), "3.01": ("litigation_investigation", -2, 0.9), "3.02": ("capital_raise", -1, 0.7),
    "4.01": ("management", -1, 0.6), "4.02": ("litigation_investigation", -2, 1.0), "5.01": ("m_and_a", 0, 0.9),
    "5.02": ("management", 0, 0.6), "5.07": ("filing", 0, 0.2), "7.01": ("filing", 0, 0.45), "8.01": ("filing", 0, 0.45),
    "9.01": ("filing", 0, 0.1),
}
FORM_EVENT = {
    "10-Q": ("filing", 0, 0.5), "10-K": ("filing", 0, 0.6), "SC 13D": ("insider_holder", 1, 0.7), "SC 13D/A": ("insider_holder", 0, 0.5),
    "SC 13G": ("insider_holder", 0, 0.3), "SC 13G/A": ("insider_holder", 0, 0.2), "424B5": ("capital_raise", -1, 0.7),
    "424B2": ("capital_raise", -1, 0.5), "S-3ASR": ("capital_raise", -1, 0.4), "8-K/A": ("filing", 0, 0.3), "8-K": ("filing", 0, 0.4),
}


def filing_event_type(form: str, items: list[str]) -> tuple[str, int, float]:
    """Best (event_type, direction prior, materiality) for a filing from its form and 8-K item codes."""
    if form.startswith("8-K") and items:
        best = max((ITEM_EVENT.get(i, ("filing", 0, 0.2)) for i in items), key=lambda x: x[2])
        return best
    return FORM_EVENT.get(form, ("filing", 0, 0.4))


def _items_from_summary(summary: str) -> list[str]:
    return sorted(set(re.findall(r"Item (\d+\.\d+)", summary)))


def _filing_item(index, form: str, cik: int, name: str, items: list[str], acc: str, url: str, published: datetime, source: str) -> RawItem | None:
    ticker = index.by_cik.get(cik)
    if not ticker:
        return None
    etype, direction, mat = filing_event_type(form, items)
    desc = "; ".join(ITEM_DESCRIPTIONS.get(i, f"Item {i}") for i in items if i != "9.01") or form
    company = index.name(ticker)
    title = f"{company} files {form}: {desc}" if form.startswith("8-K") else f"{company} files {form}"
    summary = f"Form {form} filed by {name} (CIK {cik}). Items: {', '.join(items) or 'n/a'}. Accession {acc}."
    return RawItem(uid=f"sec:{acc}", source=source, source_name="SEC EDGAR", source_type="sec", title=title, summary=summary, url=url,
                   published=published, tickers_hint=[ticker], weight=1.0,
                   extra={"form": form, "items": items, "accession": acc, "cik": cik, "rule_type": etype, "rule_direction": direction, "rule_materiality": mat})


def fetch_current_filings(http: Http, index, forms: tuple[str, ...] = CURRENT_FORMS) -> list[RawItem]:
    out: list[RawItem] = []
    for form in forms:
        status, content, _ = http.get(CURRENT_URL.format(form=quote(form)))
        if status != 200 or not content:
            log.warning("SEC current feed %s: HTTP %s", form, status)
            continue
        d = feedparser.parse(content)
        for e in d.entries:
            title = e.get("title", "")
            m = re.search(r"\((\d{10})\)", title)
            if not m:
                continue
            cik = int(m.group(1))
            if cik not in index.by_cik:
                continue
            summary_html = e.get("summary", "")
            items = _items_from_summary(summary_html)
            form_type = title.split(" - ")[0].strip() or form
            name = title.split(" - ", 1)[1].split(" (")[0] if " - " in title else ""
            acc = ""
            mm = re.search(r"accession-number=([\d-]+)", e.get("id", ""))
            if mm:
                acc = mm.group(1)
            else:
                mm = re.search(r"AccNo:</b>\s*([\d-]+)", summary_html)
                acc = mm.group(1) if mm else e.get("link", "")
            published = to_utc(e.get("updated_parsed")) if e.get("updated_parsed") else now_utc()
            item = _filing_item(index, form_type, cik, name, items, acc, e.get("link", ""), published, "sec_current")
            if item:
                out.append(item)
        time.sleep(0.25)
    return out


def backfill_filings(http: Http, index, since: date, forms: tuple[str, ...] = BACKFILL_FORMS, batch: int = 20) -> list[RawItem]:
    """EFTS full-text search restricted to the universe CIKs (batched). Returns one item per accession."""
    ciks = sorted(index.by_cik)
    seen: set[str] = set()
    out: list[RawItem] = []
    end = date.today() + timedelta(days=1)
    for i in range(0, len(ciks), batch):
        chunk = ciks[i:i + batch]
        url = EFTS_URL.format(forms=quote(",".join(forms)), ciks=",".join(f"{c:010d}" for c in chunk), start=since.isoformat(), end=end.isoformat())
        data = http.get_json(url)
        if not data:
            continue
        for h in data.get("hits", {}).get("hits", []):
            s = h.get("_source", {})
            adsh = s.get("adsh")
            if not adsh or adsh in seen:
                continue
            seen.add(adsh)
            try:
                cik = int(s.get("ciks", ["0"])[0])
            except ValueError:
                continue
            form = s.get("root_forms", [s.get("form", "")])[0] or s.get("form", "")
            items = sorted(set(s.get("items") or []))
            fdate = s.get("file_date") or date.today().isoformat()
            published = datetime.fromisoformat(fdate).replace(hour=16, minute=0, tzinfo=config.ET).astimezone(timezone.utc)
            name = (s.get("display_names") or [""])[0].split("  (")[0]
            url_ = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{adsh}-index.htm"
            item = _filing_item(index, form, cik, name, items, adsh, url_, published, "sec_efts")
            if item:
                out.append(item)
        time.sleep(0.3)
    return out
