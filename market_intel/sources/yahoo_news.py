"""Yahoo Finance headlines: per-ticker RSS (batched) and the search API (carries ``relatedTickers``)."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

import feedparser

from .base import Http, RawItem, clean_html, log, now_utc, publisher_from_url, to_utc

RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={syms}&region=US&lang=en-US"
SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search?q={t}&newsCount={n}&quotesCount=0"


def fetch_yahoo_rss(http: Http, tickers: list[str]) -> list[RawItem]:
    if not tickers:
        return []
    status, content, _ = http.get(RSS_URL.format(syms=quote(",".join(tickers))))
    if status != 200 or not content:
        log.warning("Yahoo RSS %s: HTTP %s", tickers[:3], status)
        return []
    d = feedparser.parse(content)
    out, now = [], now_utc()
    for e in d.entries:
        title = clean_html(e.get("title", ""), 300)
        if not title:
            continue
        link = (e.get("link") or "").split("?.tsrc=")[0]
        pub = to_utc(e.get("published_parsed")) if e.get("published_parsed") else now
        if pub < now - timedelta(days=7):
            continue
        out.append(RawItem(uid=str(e.get("id") or e.get("guid") or link), source="yahoo_rss", source_name=publisher_from_url(link, "Yahoo Finance"),
                           source_type="news", title=title, summary=clean_html(e.get("summary", ""), 500), url=link, published=pub,
                           weight=0.6, extra={"feed": "Yahoo Finance RSS", "batch": tickers}))
    return out


def fetch_yahoo_search(http: Http, ticker: str, n: int = 8) -> list[RawItem]:
    data = http.get_json(SEARCH_URL.format(t=quote(ticker), n=n))
    if not data:
        return []
    out, now = [], now_utc()
    for x in data.get("news", []):
        title = clean_html(x.get("title", ""), 300)
        if not title:
            continue
        pub = to_utc(x.get("providerPublishTime")) if x.get("providerPublishTime") else now
        if pub < now - timedelta(days=7):
            continue
        related = [t for t in (x.get("relatedTickers") or []) if not t.startswith("^")]
        out.append(RawItem(uid=str(x.get("uuid") or x.get("link")), source="yahoo_search", source_name=x.get("publisher") or "Yahoo Finance",
                           source_type="news", title=title, url=x.get("link", ""), published=pub, tickers_hint=related, weight=0.6,
                           extra={"feed": "Yahoo Finance search", "query": ticker, "content_type": x.get("type")}))
    return out
