"""Google News RSS search (supports the ``when:`` operator). Publisher comes from the <source> element."""
from __future__ import annotations

from urllib.parse import quote

from . import rss
from .base import Http, RawItem

URL = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def fetch_google_news(http: Http, qid: str, query: str, when: str = "2d", state: dict | None = None) -> tuple[list[RawItem], dict]:
    feed = {"id": f"gnews_{qid}", "name": "Google News", "url": URL.format(q=quote(f"{query} when:{when}")), "type": "news",
            "weight": 0.6, "publisher_from_title": True, "tags": ["google_news", qid]}
    items, st = rss.fetch_feed(http, feed, state)
    for it in items:
        it.extra["query"] = query
    return items, st
