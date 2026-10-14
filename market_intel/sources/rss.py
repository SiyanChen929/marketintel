"""Generic RSS/Atom adapter (feedparser) with ETag/Last-Modified conditional fetches."""
from __future__ import annotations

from datetime import timedelta

import feedparser

from .base import Http, RawItem, clean_html, log, now_utc, publisher_from_url, to_utc

MAX_AGE_DAYS = 10     # stale feeds (e.g. an unmaintained WSJ feed) must not inject old headlines


def fetch_feed(http: Http, feed: dict, state: dict | None = None) -> tuple[list[RawItem], dict]:
    now = now_utc()
    st = dict(state or {})
    status, content, headers = http.get(feed["url"], state=state)
    st.update({"last_fetch": now.isoformat(), "last_status": status})
    if status == 304:
        st["n_items"] = 0
        return [], st
    if status != 200 or not content:
        st["error"] = f"HTTP {status}"
        return [], st
    st["error"] = None
    d = feedparser.parse(content)
    items: list[RawItem] = []
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    for e in d.entries[:200]:
        title = clean_html(e.get("title", ""), 300)
        if not title:
            continue
        link = (e.get("link") or "").strip()
        summary = clean_html(e.get("summary") or e.get("description") or "", 600)
        pub = e.get("published_parsed") or e.get("updated_parsed")
        published = to_utc(pub) if pub else now
        if published < cutoff:
            continue
        if published > now + timedelta(hours=1):
            published = now
        publisher = feed.get("name", feed["id"])
        src = e.get("source")
        if feed.get("publisher_from_title"):            # Google News: "Headline - Publisher"
            if isinstance(src, dict) and src.get("title"):
                publisher = src["title"]
            if " - " in title:
                head, tail = title.rsplit(" - ", 1)
                if len(tail) <= 40:
                    title = head
                    publisher = publisher if isinstance(src, dict) and src.get("title") else tail
            summary = ""
        elif feed.get("publisher_from_url"):
            publisher = publisher_from_url(link, publisher)
        elif isinstance(src, dict) and src.get("title"):
            publisher = src["title"]
        uid = e.get("id") or e.get("guid") or link or title
        items.append(RawItem(uid=str(uid), source=feed["id"], source_name=publisher, source_type=feed.get("type", "news"),
                             title=title, summary=summary, url=link, published=published, weight=float(feed.get("weight", 0.6)),
                             extra={"feed": feed.get("name", feed["id"]), "tags": list(feed.get("tags", []))}))
    st.update({"etag": headers.get("etag"), "modified": headers.get("last-modified"), "n_items": len(items)})
    log.debug("feed %s: %d items", feed["id"], len(items))
    return items, st
