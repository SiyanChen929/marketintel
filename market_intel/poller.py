"""Polling orchestration: fetch -> normalise -> classify (rules, then the LLM layer) -> impacts -> scores/overlay -> alerts -> notify."""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import yaml

from . import alerts as alerts_mod
from . import config, scoring, tunnel
from .classify import rules
from .classify.llm import LLM
from .entities import ALIASES, build_index
from .sources import google_news, macro, prices, rss, sec, yahoo_news
from .sources.base import Http, RawItem
from .store import Store, event_id

log = logging.getLogger("market_intel")


def setup_logging(level: int = logging.INFO, to_file: bool = True) -> None:
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
        h = logging.StreamHandler()
        h.setFormatter(fmt)
        log.addHandler(h)
        if to_file:
            fh = logging.FileHandler(config.LOGS / "market_intel.log")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
            log.addHandler(fh)
    log.setLevel(level)


def _open_fds() -> int | None:
    """Number of open file descriptors of this process (macOS / Linux), for the health panel."""
    import os
    for d in ("/dev/fd", "/proc/self/fd"):
        try:
            return len(os.listdir(d))
        except OSError:
            continue
    return None


class Poller:
    def __init__(self, settings: config.Settings = config.SETTINGS, store: Store | None = None, index=None, llm: LLM | None = None,
                 http: Http | None = None, feeds: dict | None = None):
        self.settings = settings
        self.store = store or Store()
        self.index = index or build_index()
        self.llm = llm or LLM(settings, self.index)
        self.llm.index = self.index
        self.http = http or Http(timeout=settings.http_timeout)
        self.feeds = feeds or yaml.safe_load((config.PKG_DIR / "feeds.yaml").read_text())
        self.cycle_no = int(self.store.kv_get("cycle_no", 0) or 0)
        self.last_stats: dict = self.store.kv_get("last_cycle", {}) or {}
        self._snapshot_mtime = self._snap_mtime()
        self.running = False

    # ---- helpers ---------------------------------------------------------------------------------
    @staticmethod
    def _snap_mtime() -> float:
        p = config.LATEST / "snapshot.json"
        return p.stat().st_mtime if p.exists() else 0.0

    def maybe_refresh_index(self) -> None:
        m = self._snap_mtime()
        if m != self._snapshot_mtime:
            self.index = build_index()
            self.llm.index = self.index
            self._snapshot_mtime = m
            log.info("index refreshed: %d companies, holdings %s", len(self.index.companies), ",".join(self.index.holdings))

    def _due(self, every: int) -> bool:
        return self.cycle_no % max(1, int(every or 1)) == 0

    def _holding_queries(self) -> list[str]:
        names = []
        for t in self.index.holdings:
            a = ALIASES.get(t) or [self.index.name(t)]
            names.append(a[0])
        return [" OR ".join(f'"{n}"' for n in names[i:i + 5]) for i in range(0, len(names), 5)]

    # ---- fetch tasks ---------------------------------------------------------------------------------
    def _feed_task(self, feed: dict) -> list[RawItem]:
        try:
            st = self.store.feed_state(feed["id"])
            items, st2 = rss.fetch_feed(self.http, feed, st)
            self.store.set_feed_state(feed["id"], st2)
            return items
        finally:
            self.store.close()                           # pooled thread: release its SQLite handle

    def _gnews_task(self, qid: str, query: str) -> list[RawItem]:
        fid = f"gnews_{qid}"
        try:
            items, st2 = google_news.fetch_google_news(self.http, qid, query, state=self.store.feed_state(fid))
            self.store.set_feed_state(fid, st2)
            return items
        finally:
            self.store.close()

    def _sec_task(self) -> list[RawItem]:
        try:
            return self._sec_task_inner()
        finally:
            self.store.close()

    def _sec_task_inner(self) -> list[RawItem]:
        items = sec.fetch_current_filings(self.http, self.index)
        last = self.store.kv_get("sec_backfill_at")
        due = not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)) > timedelta(hours=self.settings.sec_backfill_hours)
        if due:
            since = (datetime.now(timezone.utc) - timedelta(days=3 if last else 10)).date()
            items += sec.backfill_filings(self.http, self.index, since)
            self.store.kv_set("sec_backfill_at", datetime.now(timezone.utc).isoformat())
        return items

    def _price_task(self) -> list[RawItem]:
        try:
            return self._price_task_inner()
        finally:
            self.store.close()

    def _price_task_inner(self) -> list[RawItem]:
        st = self.store.kv_get("price_state", {}) or {}
        items, st2, snap = prices.detect_price_moves(self.index, self.settings, st)
        self.store.kv_set("price_state", st2)
        if snap:
            old = self.store.kv_get("price_snapshot", {}) or {}
            day = next(iter(snap.values()))["ts"][:10]
            merged = {t: v for t, v in old.items() if str(v.get("ts", ""))[:10] == day}   # keep same-day values a partial download missed
            merged.update(snap)
            self.store.kv_set("price_snapshot", merged)
            self.store.kv_set("price_snapshot_at", datetime.now(timezone.utc).isoformat())
        return items

    def _price_due(self) -> bool:
        t = config.now_et()
        return t.weekday() < 5 and 4 <= t.hour < 20

    def fetch_all(self) -> tuple[list[RawItem], dict]:
        tasks: list[tuple[str, callable, tuple]] = []
        for feed in self.feeds.get("feeds", []):
            if self._due(feed.get("every", 1)):
                tasks.append((feed["id"], self._feed_task, (feed,)))
        if self._due(self.settings.google_every_n_cycles):
            n = 0
            for q in self.feeds.get("google_news", []):
                if not self._due(q.get("every", 1)):
                    continue
                if "{holdings}" in q["query"]:
                    for i, sub in enumerate(self._holding_queries()):
                        tasks.append((f"gnews_{q['id']}_{i}", self._gnews_task, (f"{q['id']}_{i}", sub))); n += 1
                else:
                    tasks.append((f"gnews_{q['id']}", self._gnews_task, (q["id"], q["query"]))); n += 1
                if n >= self.settings.google_max_queries:
                    break
        # Yahoo RSS: holdings every cycle + one rotating batch of the rest of the universe
        hold, rest = self.index.holdings, [t for t in self.index.tickers if t not in self.index.holdings]
        batches = [hold[i:i + self.settings.yahoo_batch] for i in range(0, len(hold), self.settings.yahoo_batch)]
        ptr = int(self.store.kv_get("yahoo_ptr", 0) or 0)
        for _ in range(max(0, self.settings.yahoo_batches_per_cycle - len(batches))):
            if rest:
                batches.append([rest[(ptr + j) % len(rest)] for j in range(self.settings.yahoo_batch)])
                ptr = (ptr + self.settings.yahoo_batch) % len(rest)
        self.store.kv_set("yahoo_ptr", ptr)
        for i, b in enumerate(batches):
            tasks.append((f"yahoo_rss_{i}", yahoo_news.fetch_yahoo_rss, (self.http, b)))
        if self._due(3):
            for t in hold:
                tasks.append((f"yahoo_search_{t}", yahoo_news.fetch_yahoo_search, (self.http, t)))
        tasks.append(("sec", self._sec_task, ()))
        items: list[RawItem] = []
        stats: dict = {}
        with ThreadPoolExecutor(max_workers=self.settings.workers) as ex:
            futs = {ex.submit(fn, *args): name for name, fn, args in tasks}
            for fut in as_completed(futs):
                name = futs[fut]
                try:
                    got = fut.result() or []
                    items.extend(got)
                    stats[name] = len(got)
                except Exception as e:
                    log.warning("task %s failed: %s", name, e)
                    stats[name] = f"error: {e}"[:120]
        if self._price_due():                            # after the pool: Yahoo throttles concurrent requests
            try:
                got = self._price_task()
                items.extend(got)
                stats["prices"] = len(got)
            except Exception as e:
                log.warning("price task failed: %s", e)
                stats["prices"] = f"error: {e}"[:120]
        return items, stats

    # ---- processing ---------------------------------------------------------------------------------
    def process(self, items: list[RawItem]) -> dict:
        rows, cls_by_id, edges_by_id = [], {}, {}
        cap = int(getattr(self.settings, "readthrough_cap", 12))
        for it in items:
            cls = rules.classify(it, self.index)
            eid = event_id(it.uid, it.source)
            imp = rules.impacts(cls, self.index)
            detail = cls.get("readthrough") or {}
            needs_review = int(len(detail) > cap)            # rule-engine fan-out above the cap: direct impact only until the LLM or an analyst confirms the edges
            if needs_review:
                imp = [x for x in imp if x[1] == "direct"]
            edges_by_id[eid] = [{"ticker": t, "via_theme": d["theme"], "causal_edge": d["edge"], "affected_kpi": d["kpi"], "exposure_weight": d["weight"], "sign": d["sign"],
                                 "source": "rules", "status": "needs_review" if needs_review else "auto_rules"} for t, d in detail.items()]
            row = dict(id=eid, uid=it.uid, source=it.source, source_name=it.source_name, source_type=it.source_type, title=it.title,
                       summary=it.summary, url=it.url, published=it.published, tickers=list(cls["tickers"]), tickers_rt=list(cls["tickers_rt"]),
                       themes=cls["themes"], event_type=cls["event_type"], direction=cls["direction"], materiality=cls["materiality"],
                       confidence=cls["confidence"], horizon=cls["horizon"], pillars=cls["pillars"], action=cls["action"], one_liner="",
                       relevant=int(cls["relevant"]), llm=0 if (cls["relevant"] and not cls["commentary"]) else -1,
                       needs_review=needs_review, classifier="rules", rules_version=rules.RULES_VERSION, input_hash=hashlib.sha256(f"{it.title}|{it.summary}".encode()).hexdigest()[:16],
                       classified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       extra={**it.extra, "rules": {"sentiment": cls["sentiment"], "source_factor": cls["source_factor"]}})
            row["alert_level"] = scoring.alert_level(row, imp, self.index, self.settings) if cls["relevant"] else None
            rows.append(row)
            cls_by_id[eid] = imp
        new_ids = self.store.upsert_events(rows)
        for eid in new_ids:
            self.store.set_impacts(eid, cls_by_id[eid])
            self.store.set_readthrough(eid, edges_by_id.get(eid, []))
        self.store.expire_pending(days=3)
        n_llm = self.enrich()
        alert_rows, fresh = [], []
        if new_ids:                                     # alert only on fresh events (backfills of old filings are stored silently)
            fresh_cut = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            for e in self.store.events(ids=new_ids, limit=len(new_ids) + 1):
                if e["published"] >= fresh_cut:
                    alert_rows += alerts_mod.alerts_for_event(e, self.index)
                    fresh.append(e)
        n_alerts = self.store.save_alerts(alert_rows)
        n_cases = self._open_cases(fresh)
        relevant_new = sum(1 for r in rows if r["id"] in set(new_ids) and r["relevant"])
        return {"items": len(rows), "new": len(new_ids), "new_relevant": relevant_new, "llm_classified": n_llm, "alerts": n_alerts, "cases": n_cases}

    def _open_cases(self, fresh: list[dict]) -> int:
        """Review cases (P1 W2) for warning/critical fresh events on target-list names; one per story x name, never fatal for the cycle."""
        if not fresh:
            return 0
        from . import cases as cases_mod                 # lazy: keeps the poller importable without the case workflow
        targets = set(self.index.holdings)
        n = 0
        for e in fresh:
            if e.get("alert_level") not in ("warning", "critical"):
                continue
            try:
                n += len(cases_mod.ensure_for_event(self.store, e, targets, self.settings))
            except Exception as ex:                     # pragma: no cover - defensive
                log.warning("case creation failed for %s: %s", e.get("id"), ex)
        return n

    def enrich(self, limit: int | None = None) -> int:
        if not self.llm.available:
            return 0
        pending = self.store.pending_llm(limit or self.settings.llm_max_items_per_cycle, self.settings.llm_min_materiality)
        if not pending:
            return 0
        hold = set(self.index.holdings)
        pending.sort(key=lambda e: (not (set(e.get("tickers") or []) & hold), -float(e.get("materiality") or 0)))
        n = 0
        bs = max(1, self.settings.llm_batch_size)
        batches = [pending[i:i + bs] for i in range(0, len(pending), bs)]
        with ThreadPoolExecutor(max_workers=min(3, len(batches))) as ex:      # batches in parallel: one slow call must not stall the cycle
            results = list(ex.map(self.llm.classify, batches))
        for batch, res in zip(batches, results):
            if not res:
                continue                                # call failed: keep items pending for the next cycle
            for e in batch:
                r = res.get(e["id"])
                if not r:
                    self.store.mark_llm([e["id"]], 1)
                    continue
                r = dict(r); lin = r.pop("_lineage", {}) or {}
                themes = sorted(set(e.get("themes") or []) | set(r["themes"]))
                r["tickers_rt"] = list(r["tickers_rt"])[:cap] if (cap := int(getattr(self.settings, "readthrough_cap", 12))) else list(r["tickers_rt"])
                cls = {"direction": r["direction"], "materiality": r["materiality"], "confidence": r["confidence"],
                       "tickers": {t: 0.9 for t in r["tickers"]}, "tickers_rt": {t: (0.5, 0) for t in r["tickers_rt"]}}
                imp = rules.impacts(cls, self.index) if r["relevant"] else []
                row = {**e, **r, "tickers": r["tickers"], "themes": themes, "relevant": int(r["relevant"])}
                level = scoring.alert_level(row, imp, self.index, self.settings) if r["relevant"] else None
                self.store.update_event(e["id"], relevant=int(r["relevant"]), event_type=r["event_type"], tickers=r["tickers"], tickers_rt=r["tickers_rt"],
                                        direction=r["direction"], materiality=r["materiality"], confidence=r["confidence"], horizon=r["horizon"],
                                        themes=themes, pillars=r["pillars"], action=r["action"], one_liner=r["one_liner"], alert_level=level, llm=1,
                                        classifier="llm", provider=lin.get("provider"), model=lin.get("model"), prompt_version=lin.get("prompt_version"),
                                        schema_version=lin.get("schema_version"), output_hash=lin.get("output_hash"), classified_at=lin.get("classified_at"), needs_review=0)
                self.store.set_impacts(e["id"], imp)
                sign = 1 if r["direction"] > 0 else -1 if r["direction"] < 0 else 0
                self.store.set_readthrough(e["id"], [{"ticker": t, "via_theme": ",".join(themes) or "llm", "causal_edge": "LLM value-chain read-through (see one-liner)",
                                                      "affected_kpi": "|".join(r["pillars"]) or "n/a", "exposure_weight": 0.5, "sign": sign, "source": "llm", "status": "auto_llm"}
                                                     for t in r["tickers_rt"] if r["relevant"]])
                n += 1
        return n

    # ---- periodic jobs -------------------------------------------------------------------------------
    def maybe_calendar(self, force: bool = False) -> None:
        today = config.now_et().date().isoformat()
        if not force and self.store.kv_get("calendar_date") == today:
            return
        rows = macro.fred_calendar(Http(timeout=45)) + macro.earnings_calendar(self.index)
        self.store.save_calendar(rows)
        self.store.kv_set("calendar_date", today)
        log.info("calendar refreshed: %d entries", len(rows))

    def maybe_brief(self, force: bool = False) -> str | None:
        t = config.now_et()
        today = t.date().isoformat()
        if not force and (t.weekday() >= 5 or t.hour < self.settings.brief_hour_et or self.store.kv_get("brief_date") == today):
            return None
        from . import brief
        text = brief.build(self.store, self.index, self.settings, self.llm)
        self.store.kv_set("brief_date", today)
        return text

    def maybe_url_change(self) -> None:
        """Quick tunnels change hostname on every reconnect: persist the current URL and notify when it changes."""
        try:
            url = tunnel.public_url()
        except Exception:
            return
        prev = self.store.kv_get("public_url")
        if url == prev:
            return
        self.store.kv_set("public_url", url)
        try:
            tunnel.LAST_URL_FILE.write_text((url or "") + "\n")
        except Exception:
            pass
        log.info("public URL changed: %s -> %s", prev, url)
        if url and self.settings.notify:
            alerts_mod.notify_macos("Market Intel · new public URL", url, sound=False)

    def maybe_prune(self) -> None:
        last = self.store.kv_get("last_prune")
        if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)) < timedelta(days=7):
            return
        n = self.store.prune(self.settings.keep_days)
        self.store.kv_set("last_prune", datetime.now(timezone.utc).isoformat())
        log.info("pruned %d old events", n)

    # ---- the cycle -----------------------------------------------------------------------------------
    def cycle(self) -> dict:
        t0 = time.time()
        self.running = True
        try:
            self.maybe_refresh_index()
            reaped = self.store.reap()
            self.cycle_no += 1
            self.store.kv_set("cycle_no", self.cycle_no)
            items, fetch_stats = self.fetch_all()
            proc = self.process(items)
            scores = scoring.compute_scores(self.store, self.index, self.settings)
            overlay = scoring.write_overlay(scores, self.store, self.index, self.settings)
            sent = alerts_mod.dispatch(self.store, self.settings)
            try:
                self.maybe_calendar()
            except Exception as e:                    # pragma: no cover - network
                log.warning("calendar refresh failed: %s", e)
            now = datetime.now(timezone.utc)
            stats = {"cycle": self.cycle_no, "at": now.isoformat(timespec="seconds"), "at_et": now.astimezone(config.ET).strftime("%Y-%m-%d %H:%M ET"),
                     "duration_s": round(time.time() - t0, 1), "fetched": len(items), "sources": fetch_stats, **proc, "notified": sent,
                     "llm_available": self.llm.available, "llm_calls": self.llm.calls, "llm_tokens_in": self.llm.tokens_in, "llm_tokens_out": self.llm.tokens_out,
                     "flagged_holdings": overlay["n_holdings_flagged"], "errors": [k for k, v in fetch_stats.items() if isinstance(v, str)],
                     "db_connections": self.store.n_connections, "reaped": reaped, "open_fds": _open_fds()}
            self.last_stats = stats
            self.store.kv_set("last_cycle", stats)
            self.maybe_url_change()
            log.info("cycle %d: %d fetched, %d new (%d relevant), %d LLM, %d alerts, %d notified, %.0fs", self.cycle_no, len(items), proc["new"],
                     proc["new_relevant"], proc["llm_classified"], proc["alerts"], sent, stats["duration_s"])
            return stats
        finally:
            self.running = False

    def loop(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.cycle()
            except Exception:
                log.exception("cycle failed")
            for job in (self.maybe_brief, self.maybe_prune):
                try:
                    job()
                except Exception:
                    log.exception("%s failed", job.__name__)
            interval = self.settings.poll_interval_market if config.is_market_hours(settings=self.settings) else self.settings.poll_interval_off
            stop.wait(interval)
