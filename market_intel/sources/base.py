"""Shared plumbing for source adapters: the RawItem record, an HTTP session with conditional GET, helpers."""
from __future__ import annotations

import calendar
import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from .. import config

log = logging.getLogger("market_intel")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

PUBLISHER_BY_DOMAIN = {
    "fool.com": "Motley Fool", "247wallst.com": "24/7 Wall St.", "finance.yahoo.com": "Yahoo Finance", "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg", "investors.com": "Investor's Business Daily", "barrons.com": "Barron's", "marketwatch.com": "MarketWatch",
    "cnbc.com": "CNBC", "wsj.com": "Wall Street Journal", "benzinga.com": "Benzinga", "tipranks.com": "TipRanks", "zacks.com": "Zacks",
    "investorplace.com": "InvestorPlace", "seekingalpha.com": "Seeking Alpha", "businesswire.com": "Business Wire",
    "globenewswire.com": "GlobeNewswire", "prnewswire.com": "PR Newswire", "insidermonkey.com": "Insider Monkey",
    "simplywall.st": "Simply Wall St", "thestreet.com": "TheStreet", "ft.com": "Financial Times", "nytimes.com": "New York Times",
    "techcrunch.com": "TechCrunch", "theverge.com": "The Verge", "digitimes.com": "DigiTimes", "semianalysis.com": "SemiAnalysis",
    "tomshardware.com": "Tom's Hardware", "theinformation.com": "The Information", "sec.gov": "SEC EDGAR", "federalreserve.gov": "Federal Reserve",
    "bls.gov": "BLS", "whitehouse.gov": "White House", "trendforce.com": "TrendForce", "investing.com": "Investing.com", "gurufocus.com": "GuruFocus",
    "marketbeat.com": "MarketBeat", "barchart.com": "Barchart", "fxstreet.com": "FXStreet", "sharewise.com": "Sharewise",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def clean_html(s: str | None, limit: int = 600) -> str:
    if not s:
        return ""
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = WS_RE.sub(" ", s).strip()
    return s[:limit]


def to_utc(x) -> datetime:
    """struct_time / datetime / ISO string / epoch -> aware UTC datetime (falls back to now)."""
    if x is None:
        return now_utc()
    if isinstance(x, time.struct_time):
        return datetime.fromtimestamp(calendar.timegm(x), tz=timezone.utc)
    if isinstance(x, datetime):
        return x.astimezone(timezone.utc) if x.tzinfo else x.replace(tzinfo=timezone.utc)
    if isinstance(x, (int, float)):
        return datetime.fromtimestamp(float(x), tz=timezone.utc)
    if isinstance(x, str):
        try:
            d = datetime.fromisoformat(x.replace("Z", "+00:00"))
            return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime
                d = parsedate_to_datetime(x)
                return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except Exception:
                return now_utc()
    return now_utc()


def publisher_from_url(url: str, default: str = "") -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return default
    host = host[4:] if host.startswith("www.") else host
    for dom, name in PUBLISHER_BY_DOMAIN.items():
        if host == dom or host.endswith("." + dom):
            return name
    return default or host


@dataclass
class RawItem:
    uid: str
    source: str                    # feed id
    source_name: str               # publisher / display name
    source_type: str               # news | press | sec | macro | price | policy
    title: str
    summary: str = ""
    url: str = ""
    published: datetime = field(default_factory=now_utc)
    tickers_hint: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    weight: float = 0.6            # source credibility (0-1)


class Http:
    """requests.Session with a descriptive User-Agent, retries and conditional GET support."""

    def __init__(self, timeout: int = 15, user_agent: str = config.SEC_USER_AGENT):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept": "*/*", "Accept-Encoding": "gzip, deflate"})
        self.timeout = timeout

    def get(self, url: str, state: dict | None = None, headers: dict | None = None, retries: int = 2, timeout: int | None = None) -> tuple[int, bytes, dict]:
        h = dict(headers or {})
        if state:
            if state.get("etag"):
                h["If-None-Match"] = state["etag"]
            if state.get("modified"):
                h["If-Modified-Since"] = state["modified"]
        for attempt in range(retries + 1):
            try:
                r = self.s.get(url, headers=h, timeout=timeout or self.timeout)
                if r.status_code in (429, 503) and attempt < retries:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                return r.status_code, r.content, {k.lower(): v for k, v in r.headers.items()}
            except requests.RequestException as e:
                if attempt >= retries:
                    log.warning("GET %s failed: %s", url[:90], e)
                    return 0, b"", {}
                time.sleep(1.5 * (attempt + 1))
        return 0, b"", {}

    def get_json(self, url: str, **kw):
        status, content, _ = self.get(url, **kw)
        if status != 200 or not content:
            return None
        import json
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None
