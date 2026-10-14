"""SQLite event store (WAL mode, FTS5 full-text index). One connection per thread; writes serialised."""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from . import config
from . import stories as ST

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, uid TEXT, source TEXT, source_name TEXT, source_type TEXT,
  title TEXT NOT NULL, summary TEXT, url TEXT, published TEXT NOT NULL, fetched TEXT NOT NULL,
  tickers TEXT DEFAULT '[]', tickers_rt TEXT DEFAULT '[]', themes TEXT DEFAULT '[]',
  event_type TEXT DEFAULT 'other', direction INTEGER DEFAULT 0, materiality REAL DEFAULT 0, confidence REAL DEFAULT 0,
  horizon TEXT DEFAULT 'days', pillars TEXT DEFAULT '[]', action TEXT DEFAULT 'none', one_liner TEXT DEFAULT '',
  relevant INTEGER DEFAULT 0, llm INTEGER DEFAULT 0, alert_level TEXT, extra TEXT DEFAULT '{}', title_key TEXT, read INTEGER DEFAULT 0,
  classifier TEXT DEFAULT 'rules', provider TEXT, model TEXT, prompt_version TEXT, schema_version TEXT, rules_version TEXT,
  input_hash TEXT, output_hash TEXT, classified_at TEXT, human_override TEXT DEFAULT '{}', story_id TEXT, needs_review INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_events_pub ON events(published DESC);
CREATE INDEX IF NOT EXISTS ix_events_titlekey ON events(title_key);
CREATE INDEX IF NOT EXISTS ix_events_llm ON events(llm, relevant);
CREATE TABLE IF NOT EXISTS event_tickers (
  event_id TEXT NOT NULL, ticker TEXT NOT NULL, relation TEXT NOT NULL, impact REAL NOT NULL,
  PRIMARY KEY (event_id, ticker), FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_et_ticker ON event_tickers(ticker);
CREATE TABLE IF NOT EXISTS event_readthrough (
  event_id TEXT NOT NULL, ticker TEXT NOT NULL, via_theme TEXT, causal_edge TEXT, affected_kpi TEXT, exposure_weight REAL, sign INTEGER,
  source TEXT, status TEXT DEFAULT 'auto', PRIMARY KEY (event_id, ticker)
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, level TEXT NOT NULL, ticker TEXT, event_id TEXT,
  message TEXT NOT NULL, notified INTEGER DEFAULT 0, UNIQUE(event_id, ticker)
);
CREATE TABLE IF NOT EXISTS ticker_scores (
  ticker TEXT PRIMARY KEY, asof TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_state (feed_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar (
  date TEXT NOT NULL, name TEXT NOT NULL, importance INTEGER, time_et TEXT, source TEXT, kind TEXT, ticker TEXT, PRIMARY KEY (date, name)
);
CREATE TABLE IF NOT EXISTS briefs (date TEXT PRIMARY KEY, created TEXT NOT NULL, text TEXT NOT NULL, meta TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stories (
  story_id TEXT PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL, first_published TEXT NOT NULL, last_published TEXT NOT NULL,
  primary_event_id TEXT, primary_source TEXT, title TEXT, event_type TEXT, key_tickers TEXT DEFAULT '[]', n_events INTEGER DEFAULT 1,
  n_sources INTEGER DEFAULT 1, n_independent INTEGER DEFAULT 1, families TEXT DEFAULT '{}', status TEXT DEFAULT 'active',
  tokens TEXT DEFAULT '[]', bigrams TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_stories_first ON stories(first_published DESC);
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, created_by TEXT, updated TEXT NOT NULL, story_id TEXT, event_id TEXT, ticker TEXT, title TEXT, level TEXT,
  owner TEXT, due TEXT, sla_hours REAL, status TEXT DEFAULT 'open', ack_at TEXT, ack_by TEXT, evidence TEXT DEFAULT '[]', decision TEXT, no_trade_reason TEXT,
  execution_ref TEXT, signoff_by TEXT, signoff_at TEXT, notes TEXT DEFAULT '[]', UNIQUE(story_id, ticker)
);
CREATE INDEX IF NOT EXISTS ix_cases_status ON cases(status, due);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(title, summary, one_liner, content='events', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
  INSERT INTO events_fts(rowid, title, summary, one_liner) VALUES (new.rowid, new.title, new.summary, new.one_liner);
END;
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
  INSERT INTO events_fts(events_fts, rowid, title, summary, one_liner) VALUES ('delete', old.rowid, old.title, old.summary, old.one_liner);
END;
CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
  INSERT INTO events_fts(events_fts, rowid, title, summary, one_liner) VALUES ('delete', old.rowid, old.title, old.summary, old.one_liner);
  INSERT INTO events_fts(rowid, title, summary, one_liner) VALUES (new.rowid, new.title, new.summary, new.one_liner);
END;
"""
JSON_COLS = ("tickers", "tickers_rt", "themes", "pillars", "extra", "human_override")
LINEAGE_COLS = ("classifier", "provider", "model", "prompt_version", "schema_version", "rules_version", "input_hash", "output_hash", "classified_at", "human_override")
EVENT_COLS = ("id", "uid", "source", "source_name", "source_type", "title", "summary", "url", "published", "fetched", "tickers", "tickers_rt",
              "themes", "event_type", "direction", "materiality", "confidence", "horizon", "pillars", "action", "one_liner", "relevant", "llm",
              "alert_level", "extra", "title_key", "read", *LINEAGE_COLS, "story_id", "needs_review")
# columns added after the first release (name -> DDL); applied idempotently by Store._migrate()
ADDED_COLS = {"classifier": "TEXT DEFAULT 'rules'", "provider": "TEXT", "model": "TEXT", "prompt_version": "TEXT", "schema_version": "TEXT", "rules_version": "TEXT",
              "input_hash": "TEXT", "output_hash": "TEXT", "classified_at": "TEXT", "human_override": "TEXT DEFAULT '{}'", "story_id": "TEXT", "needs_review": "INTEGER DEFAULT 0"}


def event_id(uid: str, source: str) -> str:
    return hashlib.sha1(f"{source}|{uid}".encode()).hexdigest()[:16]


def title_key(title: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower())
    t = re.sub(r"\b(the|a|an|of|to|in|on|for|and|as|at|by|its|is|are|with|from|after|amid|says|say)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:90]


def _iso(d: datetime | str | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, str):
        return d
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str = config.DB_PATH):
        self.path = str(path)
        self._local = threading.local()
        self._wlock = threading.RLock()
        self._conns: dict[int, tuple[threading.Thread, sqlite3.Connection]] = {}    # thread id -> (thread, connection): reaped when the thread is gone
        self._reg_lock = threading.Lock()
        self.init()

    # ---- connection handling ---------------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        """One connection per thread. Pooled / short-lived threads leak their handle unless reaped: every new
        connection first closes the connections of threads that no longer exist (see ``reap``)."""
        c = getattr(self._local, "conn", None)
        if c is None:
            self.reap()
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
            with self._reg_lock:
                self._conns[threading.get_ident()] = (threading.current_thread(), c)
        return c

    def reap(self) -> int:
        """Close connections owned by threads that have exited (ThreadPoolExecutor workers, server worker threads)."""
        n = 0
        with self._reg_lock:
            dead = [tid for tid, (th, _) in self._conns.items() if not th.is_alive()]
            for tid in dead:
                _, c = self._conns.pop(tid)
                try:
                    c.close()
                except Exception:
                    pass
                n += 1
        return n

    @property
    def n_connections(self) -> int:
        with self._reg_lock:
            return len(self._conns)

    def init(self) -> None:
        with self._wlock:
            self.conn.executescript(SCHEMA)
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(cases)")}
            if "created_by" not in cols:                       # migration: immutable author on existing rows
                self.conn.execute("ALTER TABLE cases ADD COLUMN created_by TEXT")
            self._commit()
            self._migrate()

    def _migrate(self) -> None:
        """Idempotent schema/value migrations (safe to run on every start)."""
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(events)").fetchall()}
        for col, ddl in ADDED_COLS.items():                                                           # S5-S7: lineage, story, review columns
            if col not in have:
                self.conn.execute(f"ALTER TABLE events ADD COLUMN {col} {ddl}")
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_events_story ON events(story_id)")
        for old, new in (("thesis check", "analyst review"), ("re-run model", "refresh model inputs")):     # S3: analyst vocabulary
            self.conn.execute("UPDATE events SET action=? WHERE action=?", (new, old))
        # rows classified before lineage existed: llm=1 rows came from the LLM layer, everything else from the rules
        self.conn.execute("UPDATE events SET classifier='llm' WHERE llm=1 AND (classifier IS NULL OR classifier='rules') AND provider IS NULL AND prompt_version IS NULL")
        self.conn.execute("UPDATE events SET classifier='rules' WHERE classifier IS NULL")
        self._commit()

    def close(self) -> None:
        """Close this thread's connection (call at the end of a pooled task)."""
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None
            with self._reg_lock:
                self._conns.pop(threading.get_ident(), None)

    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in JSON_COLS:
            if k in d and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except json.JSONDecodeError:
                    d[k] = [] if k != "extra" else {}
        return d

    # ---- events -----------------------------------------------------------------------------
    def upsert_events(self, rows: Iterable[dict]) -> list[str]:
        """Insert new events (dedup by id and by normalised title within 3 days). Returns the new ids."""
        new: list[str] = []
        with self._wlock:
            c = self.conn
            for r in rows:
                r = dict(r)
                r.setdefault("id", event_id(r["uid"], r["source"]))
                r["title_key"] = title_key(r["title"])
                r["published"] = _iso(r["published"])
                r["fetched"] = _iso(r.get("fetched") or datetime.now(timezone.utc))
                tick_list = list(r.get("tickers") or [])
                for k in JSON_COLS:
                    v = r.get(k, [] if k != "extra" else {})
                    r[k] = json.dumps(v, default=str) if not isinstance(v, str) else v
                if c.execute("SELECT 1 FROM events WHERE id=?", (r["id"],)).fetchone():
                    continue
                if r["title_key"]:
                    lo = (datetime.fromisoformat(r["published"]) - timedelta(days=3)).isoformat(timespec="seconds")
                    dup = c.execute("SELECT id FROM events WHERE title_key=? AND published>=? LIMIT 1", (r["title_key"], lo)).fetchone()
                    if dup:
                        continue
                cols = [k for k in EVENT_COLS if k in r and k != "story_id"]
                c.execute(f"INSERT INTO events ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [r[k] for k in cols])
                self._assign_story(r["id"], r["title"], r.get("one_liner") or "", tick_list, r.get("event_type") or "other", r["published"], r.get("source_name") or "")
                new.append(r["id"])
            c.commit()
        return new

    # ---- stories (S6) -----------------------------------------------------------------------------
    def _assign_story(self, event_id: str, title: str, one_liner: str, tickers: list, event_type: str, published: str, source_name: str) -> str:
        """Attach the event to an existing story (same facts, other outlet) or open a new one. Caller holds the write lock."""
        from .entities import ALIASES
        c = self.conn
        sig = ST.signature(title, one_liner, [str(t) for t in tickers], ALIASES)
        pub = datetime.fromisoformat(published)
        lo, hi = _iso(pub - timedelta(hours=ST.WINDOW_H)), _iso(pub + timedelta(hours=ST.WINDOW_H))
        best, best_sim = None, -1.0
        for row in c.execute("SELECT story_id, event_type, key_tickers, tokens, bigrams FROM stories WHERE first_published BETWEEN ? AND ?", (lo, hi)).fetchall():
            cand = {"tokens": set(json.loads(row["tokens"] or "[]")), "bigrams": set(json.loads(row["bigrams"] or "[]")), "tickers": set(json.loads(row["key_tickers"] or "[]"))}
            ok, sim = ST.same_story(sig, cand, event_type, row["event_type"])
            if ok and sim > best_sim:
                best, best_sim = row["story_id"], sim
        if best is None:
            best = "st_" + event_id
            now = _iso(datetime.now(timezone.utc))
            c.execute("INSERT OR IGNORE INTO stories(story_id, created, updated, first_published, last_published, primary_event_id, primary_source, title, event_type, key_tickers, tokens, bigrams) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (best, now, now, published, published, event_id, source_name, title, event_type, json.dumps(sorted(sig["tickers"])),
                                                          json.dumps(sorted(sig["tokens"])[:80]), json.dumps(sorted(sig["bigrams"]))))
        c.execute("UPDATE events SET story_id=? WHERE id=?", (best, event_id))
        self.refresh_story(best)
        return best

    def refresh_story(self, story_id: str) -> None:
        """Recompute a story's aggregates from its copies (counts, families, status, token signature)."""
        from .entities import ALIASES
        c = self.conn
        rows = c.execute("SELECT id, title, one_liner, tickers, event_type, published, source_name FROM events WHERE story_id=? ORDER BY published", (story_id,)).fetchall()
        if not rows:
            c.execute("DELETE FROM stories WHERE story_id=?", (story_id,))
            return
        toks, bigs, ticks, types, statuses, sources = set(), set(), set(), {}, [], []
        for r in rows:
            tk = json.loads(r["tickers"] or "[]")
            tk = list(tk.keys()) if isinstance(tk, dict) else list(tk)
            sig = ST.signature(r["title"], r["one_liner"], tk, ALIASES)
            toks |= sig["tokens"]; bigs |= sig["bigrams"]; ticks |= set(tk)
            if r["event_type"] not in (None, "other", "market_news", "commentary"):
                types[r["event_type"]] = types.get(r["event_type"], 0) + 1
            statuses.append(ST.copy_status(r["title"])); sources.append(r["source_name"] or "")
        n_ind, fam = ST.independent_sources(sources)
        etype = max(types, key=types.get) if types else rows[0]["event_type"]
        c.execute("UPDATE stories SET updated=?, first_published=?, last_published=?, primary_event_id=?, primary_source=?, title=?, event_type=?, key_tickers=?, n_events=?, "
                  "n_sources=?, n_independent=?, families=?, status=?, tokens=?, bigrams=? WHERE story_id=?",
                  (_iso(datetime.now(timezone.utc)), rows[0]["published"], rows[-1]["published"], rows[0]["id"], rows[0]["source_name"], rows[0]["title"], etype,
                   json.dumps(sorted(ticks)), len(rows), len(set(sources)), n_ind, json.dumps(fam), ST.story_status(statuses), json.dumps(sorted(toks)[:120]),
                   json.dumps(sorted(bigs)), story_id))

    def recluster(self, event_id: str) -> str | None:
        """Detach an event from its story and re-run the assignment (after the LLM changed tickers/type/one-liner)."""
        e = self.get_event(event_id)
        if not e:
            return None
        with self._wlock:
            old = e.get("story_id")
            if old:
                self.conn.execute("UPDATE events SET story_id=NULL WHERE id=?", (event_id,))
                self.refresh_story(old)
            tk = e.get("tickers") or []
            tk = list(tk.keys()) if isinstance(tk, dict) else list(tk)
            sid = self._assign_story(event_id, e["title"], e.get("one_liner") or "", tk, e.get("event_type") or "other", e["published"], e.get("source_name") or "")
            self._commit()
            return sid

    def backfill_stories(self, days: int = 90) -> int:
        """Assign stories to events stored before clustering existed (oldest first, so the first copy anchors the story)."""
        since = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        rows = self.conn.execute("SELECT id, title, one_liner, tickers, event_type, published, source_name FROM events WHERE story_id IS NULL AND published>=? ORDER BY published", (since,)).fetchall()
        n = 0
        with self._wlock:
            for r in rows:
                tk = json.loads(r["tickers"] or "[]")
                tk = list(tk.keys()) if isinstance(tk, dict) else list(tk)
                self._assign_story(r["id"], r["title"], r["one_liner"] or "", tk, r["event_type"] or "other", r["published"], r["source_name"] or "")
                n += 1
            self._commit()
        return n

    def story(self, story_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM stories WHERE story_id=?", (story_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        for k in ("key_tickers", "families", "tokens", "bigrams"):
            try:
                d[k] = json.loads(d.get(k) or ("{}" if k == "families" else "[]"))
            except json.JSONDecodeError:
                pass
        return d

    def stories_for(self, ids: Iterable[str]) -> dict[str, dict]:
        ids = [i for i in set(ids) if i]
        if not ids:
            return {}
        out = {}
        for r in self.conn.execute(f"SELECT story_id, n_events, n_sources, n_independent, status, first_published, last_published, primary_source FROM stories WHERE story_id IN ({','.join('?' * len(ids))})", ids).fetchall():
            out[r["story_id"]] = dict(r)
        return out

    def _commit(self) -> None:
        """Commit unless an outer :meth:`transaction` owns the unit of work."""
        if not getattr(self._local, "in_txn", False):
            self.conn.commit()

    @contextlib.contextmanager
    def transaction(self):
        """One SQLite transaction for a business mutation AND its audit event.

        Previously the two lived in different databases, so an audit failure could leave a committed but
        unaudited state change. Anything raised inside rolls both back together.
        """
        c = self.conn
        with self._wlock:
            if getattr(self._local, "in_txn", False):          # already inside one: join it
                yield c
                return
            try:
                c.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                pass
            self._local.in_txn = True
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                self._local.in_txn = False

    def get_event(self, id_: str) -> dict | None:
        return self._row(self.conn.execute("SELECT * FROM events WHERE id=?", (id_,)).fetchone())

    def update_event(self, id_: str, **fields: Any) -> None:
        if not fields:
            return
        f = dict(fields)
        for k in JSON_COLS:
            if k in f and not isinstance(f[k], str):
                f[k] = json.dumps(f[k], default=str)
        with self._wlock:
            self.conn.execute(f"UPDATE events SET {', '.join(k + '=?' for k in f)} WHERE id=?", [*f.values(), id_])
            self._commit()
        if {"tickers", "event_type", "one_liner", "title"} & set(fields):
            self.recluster(id_)

    def set_impacts(self, id_: str, rows: Iterable[tuple[str, str, float]]) -> None:
        with self._wlock:
            self.conn.execute("DELETE FROM event_tickers WHERE event_id=?", (id_,))
            self.conn.executemany("INSERT OR REPLACE INTO event_tickers(event_id, ticker, relation, impact) VALUES (?,?,?,?)",
                                  [(id_, t, rel, float(imp)) for t, rel, imp in rows])
            self._commit()

    # ---- read-through edges (S7) ---------------------------------------------------------------------
    def set_readthrough(self, id_: str, rows: Iterable[dict]) -> None:
        with self._wlock:
            self.conn.execute("DELETE FROM event_readthrough WHERE event_id=?", (id_,))
            self.conn.executemany("INSERT OR REPLACE INTO event_readthrough(event_id, ticker, via_theme, causal_edge, affected_kpi, exposure_weight, sign, source, status) VALUES (?,?,?,?,?,?,?,?,?)",
                                  [(id_, r["ticker"], r.get("via_theme"), r.get("causal_edge"), r.get("affected_kpi"), float(r.get("exposure_weight") or 0.0), int(r.get("sign") or 0),
                                    r.get("source", "rules"), r.get("status", "auto")) for r in rows])
            self._commit()

    def readthrough_for(self, ids: Iterable[str]) -> dict[str, list[dict]]:
        ids = [i for i in set(ids) if i]
        out: dict[str, list[dict]] = {i: [] for i in ids}
        if not ids:
            return out
        for r in self.conn.execute(f"SELECT * FROM event_readthrough WHERE event_id IN ({','.join('?' * len(ids))}) ORDER BY exposure_weight DESC, ticker", ids).fetchall():
            out.setdefault(r["event_id"], []).append(dict(r))
        return out

    def impacts_for(self, ids: Iterable[str]) -> dict[str, list[dict]]:
        ids = [i for i in set(ids) if i]
        out: dict[str, list[dict]] = {i: [] for i in ids}
        if not ids:
            return out
        for r in self.conn.execute(f"SELECT event_id, ticker, relation, impact FROM event_tickers WHERE event_id IN ({','.join('?' * len(ids))}) ORDER BY relation, ABS(impact) DESC", ids).fetchall():
            out.setdefault(r["event_id"], []).append({"ticker": r["ticker"], "relation": r["relation"], "impact": float(r["impact"])})
        return out

    def apply_readthrough_cap(self, cap: int) -> int:
        """Retroactively withhold rule-engine read-through above the cap (events never confirmed by the LLM or an analyst)."""
        rows = self.conn.execute("SELECT t.event_id, COUNT(*) n, e.classifier FROM event_tickers t JOIN events e ON e.id=t.event_id WHERE t.relation='readthrough' "
                                 "GROUP BY t.event_id HAVING n>?", (int(cap),)).fetchall()
        n = 0
        with self._wlock:
            for r in rows:
                if r["classifier"] in ("llm", "human"):
                    continue
                self.conn.execute("DELETE FROM event_tickers WHERE event_id=? AND relation='readthrough'", (r["event_id"],))
                self.conn.execute("UPDATE events SET needs_review=1 WHERE id=?", (r["event_id"],))
                self.conn.execute("UPDATE event_readthrough SET status='needs_review' WHERE event_id=?", (r["event_id"],))
                n += 1
            self._commit()
        return n

    def events(self, since: datetime | str | None = None, until: datetime | str | None = None, ticker: str | None = None,
               types: Iterable[str] | None = None, source_types: Iterable[str] | None = None, min_materiality: float | None = None,
               levels: Iterable[str] | None = None, holdings: Iterable[str] | None = None, relevant_only: bool = False,
               ids: Iterable[str] | None = None, limit: int = 200, offset: int = 0, order: str = "published", collapse: bool = False) -> list[dict]:
        where, args = [], []
        if collapse:                                     # one representative copy per story: the most material, then the most recent
            where.append("e.id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY COALESCE(story_id, id) ORDER BY materiality DESC, published DESC) rn FROM events) WHERE rn=1)")
        if since:
            where.append("e.published>=?"); args.append(_iso(since))
        if until:
            where.append("e.published<=?"); args.append(_iso(until))
        if ticker:
            where.append("e.id IN (SELECT event_id FROM event_tickers WHERE ticker=?)"); args.append(ticker.upper())
        if holdings:
            hs = list(holdings)
            where.append(f"e.id IN (SELECT event_id FROM event_tickers WHERE ticker IN ({','.join('?' * len(hs))}))"); args.extend(hs)
        if types:
            ts = list(types); where.append(f"e.event_type IN ({','.join('?' * len(ts))})"); args.extend(ts)
        if source_types:
            ss = list(source_types); where.append(f"e.source_type IN ({','.join('?' * len(ss))})"); args.extend(ss)
        if min_materiality is not None:
            where.append("e.materiality>=?"); args.append(float(min_materiality))
        if levels:
            ls = list(levels); where.append(f"e.alert_level IN ({','.join('?' * len(ls))})"); args.extend(ls)
        if relevant_only:
            where.append("e.relevant=1")
        if ids:
            i2 = list(ids); where.append(f"e.id IN ({','.join('?' * len(i2))})"); args.extend(i2)
        sql = "SELECT e.* FROM events e" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY " + ("e.materiality DESC, e.published DESC" if order == "materiality" else "e.published DESC")
        sql += " LIMIT ? OFFSET ?"; args += [int(limit), int(offset)]
        return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    def search(self, q: str, limit: int = 50) -> list[dict]:
        """FTS5 search: all terms (prefix) first; if nothing matches, any term ranked by bm25."""
        toks = [re.sub(r'["*]', "", t) for t in re.split(r"\s+", q.strip()) if len(re.sub(r'["*]', "", t)) >= 2]
        if not toks:
            return []
        terms = ['"' + t + '"*' for t in toks]
        out: list[dict] = []
        for expr in (" AND ".join(terms), " OR ".join(terms)):
            try:
                rows = self.conn.execute(
                    "SELECT e.* FROM events_fts f JOIN events e ON e.rowid=f.rowid WHERE events_fts MATCH ? ORDER BY bm25(events_fts), e.published DESC LIMIT ?",
                    (expr, int(limit))).fetchall()
            except sqlite3.OperationalError:
                rows = []
            out = [self._row(r) for r in rows]
            if out:
                break
        out.sort(key=lambda e: e["published"], reverse=True)
        return out

    def expire_pending(self, days: int = 3) -> None:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        with self._wlock:
            self.conn.execute("UPDATE events SET llm=-1 WHERE llm=0 AND published<?", (cutoff,))
            self._commit()

    def pending_llm(self, limit: int = 60, min_materiality: float = 0.0, exclude_types: Iterable[str] = ("commentary",)) -> list[dict]:
        ex = list(exclude_types)
        rows = self.conn.execute(
            f"SELECT * FROM events WHERE llm=0 AND relevant=1 AND materiality>=? AND event_type NOT IN ({','.join('?' * len(ex))}) "
            "ORDER BY materiality DESC, published DESC LIMIT ?", [float(min_materiality), *ex, int(limit)]).fetchall()
        return [self._row(r) for r in rows]

    def mark_llm(self, ids: Iterable[str], value: int = 1) -> None:
        ids = list(ids)
        if not ids:
            return
        with self._wlock:
            self.conn.executemany("UPDATE events SET llm=? WHERE id=?", [(value, i) for i in ids])
            self._commit()

    def mark_read(self, id_: str, read: int = 1) -> None:
        self.update_event(id_, read=read)

    def mark_read_many(self, ids: Iterable[str], read: int = 1) -> int:
        """Bulk read/unread flag (no re-clustering; the read flag is not part of the story signature)."""
        ids = [str(i) for i in ids if i]
        if not ids:
            return 0
        with self._wlock:
            cur = self.conn.executemany("UPDATE events SET read=? WHERE id=?", [(int(read), i) for i in ids])
            self._commit()
            return int(cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(ids))

    # ---- cases (P1 W2): one review case per story x target-list name ----------------------------------
    CASE_JSON = ("evidence", "notes")

    def _case_row(self, r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in self.CASE_JSON:
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except (json.JSONDecodeError, TypeError):
                d[k] = []
        return d

    def insert_case(self, row: dict) -> dict:
        """Insert a case (idempotent on story_id + ticker: an existing case is returned unchanged)."""
        r = dict(row)
        now = _iso(datetime.now(timezone.utc))
        r.setdefault("created", now); r.setdefault("updated", now); r.setdefault("status", "open")
        for k in self.CASE_JSON:
            v = r.get(k, [])
            r[k] = json.dumps(v, default=str) if not isinstance(v, str) else v
        cols = [k for k in ("created", "created_by", "updated", "story_id", "event_id", "ticker", "title", "level", "owner", "due", "sla_hours", "status", "ack_at", "ack_by",
                            "evidence", "decision", "no_trade_reason", "execution_ref", "signoff_by", "signoff_at", "notes") if k in r]
        with self._wlock:
            if r.get("story_id") and r.get("ticker"):
                hit = self.find_case(r["story_id"], r["ticker"])
                if hit:
                    return hit
            cur = self.conn.execute(f"INSERT INTO cases ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [r[k] for k in cols])
            self._commit()
            return self.get_case(int(cur.lastrowid))

    def get_case(self, id_: int) -> dict | None:
        return self._case_row(self.conn.execute("SELECT * FROM cases WHERE id=?", (int(id_),)).fetchone())

    def find_case(self, story_id: str, ticker: str) -> dict | None:
        return self._case_row(self.conn.execute("SELECT * FROM cases WHERE story_id=? AND ticker=?", (story_id, ticker)).fetchone())

    def cases_for(self, pairs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """{(story_id, ticker): case} for the given pairs (one query)."""
        want = {(s, t) for s, t in pairs if s and t}
        if not want:
            return {}
        sids = sorted({s for s, _ in want})
        out = {}
        for r in self.conn.execute(f"SELECT * FROM cases WHERE story_id IN ({','.join('?' * len(sids))})", sids).fetchall():
            d = self._case_row(r)
            if (d["story_id"], d["ticker"]) in want:
                out[(d["story_id"], d["ticker"])] = d
        return out

    def update_case_row(self, id_: int, **fields: Any) -> dict | None:
        if not fields:
            return self.get_case(id_)
        f = dict(fields)
        for k in self.CASE_JSON:
            if k in f and not isinstance(f[k], str):
                f[k] = json.dumps(f[k], default=str)
        f["updated"] = _iso(datetime.now(timezone.utc))
        with self._wlock:
            self.conn.execute(f"UPDATE cases SET {', '.join(k + '=?' for k in f)} WHERE id=?", [*f.values(), int(id_)])
            self._commit()
        return self.get_case(id_)

    def list_cases(self, status: str | Iterable[str] | None = None, ticker: str | None = None, limit: int = 100, overdue_before: datetime | str | None = None) -> list[dict]:
        where, args = [], []
        if status:
            ss = [status] if isinstance(status, str) else list(status)
            where.append(f"status IN ({','.join('?' * len(ss))})"); args.extend(ss)
        if ticker:
            where.append("ticker=?"); args.append(ticker.upper())
        if overdue_before:
            where.append("due IS NOT NULL AND due<?"); args.append(_iso(overdue_before))
        sql = "SELECT * FROM cases" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'acknowledged' THEN 1 WHEN 'in_review' THEN 2 WHEN 'decided' THEN 3 ELSE 4 END, COALESCE(due, updated), id DESC LIMIT ?"
        args.append(int(limit))
        return [self._case_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def count_cases(self, statuses: Iterable[str]) -> int:
        ss = list(statuses)
        if not ss:
            return 0
        return int(self.conn.execute(f"SELECT COUNT(*) FROM cases WHERE status IN ({','.join('?' * len(ss))})", ss).fetchone()[0])

    def event_tickers_since(self, since: datetime | str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT t.event_id, t.ticker, t.relation, t.impact, e.published, e.event_type, e.materiality, e.direction, e.title, e.url, "
            "e.source_name, e.alert_level, e.one_liner, e.pillars, e.action, e.story_id, e.needs_review, s.n_independent, s.status AS story_status, "
            "s.first_published AS story_first FROM event_tickers t JOIN events e ON e.id=t.event_id LEFT JOIN stories s ON s.story_id=e.story_id "
            "WHERE e.published>=? AND e.relevant=1 ORDER BY e.published DESC", (_iso(since),)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["pillars"] = json.loads(d.get("pillars") or "[]")
            except json.JSONDecodeError:
                d["pillars"] = []
            out.append(d)
        return out

    # ---- alerts -----------------------------------------------------------------------------
    def save_alerts(self, rows: Iterable[dict]) -> int:
        n = 0
        with self._wlock:
            for r in rows:
                cur = self.conn.execute("INSERT OR IGNORE INTO alerts(created, level, ticker, event_id, message, notified) VALUES (?,?,?,?,?,0)",
                                        (_iso(r.get("created") or datetime.now(timezone.utc)), r["level"], r.get("ticker"), r.get("event_id"), r["message"]))
                n += cur.rowcount
            self._commit()
        return n

    def alerts(self, since: datetime | str | None = None, unnotified: bool = False, limit: int = 100) -> list[dict]:
        where, args = [], []
        if since:
            where.append("a.created>=?"); args.append(_iso(since))
        if unnotified:
            where.append("a.notified=0")
        sql = ("SELECT a.*, e.title, e.url, e.source_name, e.published, e.event_type FROM alerts a LEFT JOIN events e ON e.id=a.event_id"
               + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY a.id DESC LIMIT ?")
        args.append(int(limit))
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def mark_notified(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        if not ids:
            return
        with self._wlock:
            self.conn.executemany("UPDATE alerts SET notified=1 WHERE id=?", [(i,) for i in ids])
            self._commit()

    # ---- scores / state / calendar / briefs ------------------------------------------------------
    def set_scores(self, rows: Iterable[dict], asof: datetime | str) -> None:
        with self._wlock:
            self.conn.executemany("INSERT OR REPLACE INTO ticker_scores(ticker, asof, data) VALUES (?,?,?)",
                                  [(r["ticker"], _iso(asof), json.dumps(r, default=str)) for r in rows])
            self._commit()

    def scores(self) -> list[dict]:
        return [json.loads(r["data"]) for r in self.conn.execute("SELECT data FROM ticker_scores").fetchall()]

    def feed_state(self, feed_id: str) -> dict:
        r = self.conn.execute("SELECT data FROM feed_state WHERE feed_id=?", (feed_id,)).fetchone()
        return json.loads(r["data"]) if r else {}

    def set_feed_state(self, feed_id: str, state: dict) -> None:
        with self._wlock:
            self.conn.execute("INSERT OR REPLACE INTO feed_state(feed_id, data) VALUES (?,?)", (feed_id, json.dumps(state, default=str)))
            self._commit()

    def all_feed_states(self) -> dict[str, dict]:
        return {r["feed_id"]: json.loads(r["data"]) for r in self.conn.execute("SELECT feed_id, data FROM feed_state").fetchall()}

    def save_calendar(self, rows: Iterable[dict], kinds: Iterable[str] = ("macro", "earnings")) -> None:
        rows = list(rows)
        with self._wlock:
            for kind in kinds:
                self.conn.execute("DELETE FROM calendar WHERE kind=?", (kind,))
            self.conn.executemany("INSERT OR REPLACE INTO calendar(date, name, importance, time_et, source, kind, ticker) VALUES (?,?,?,?,?,?,?)",
                                  [(r["date"], r["name"], r.get("importance", 1), r.get("time_et", ""), r.get("source", ""), r.get("kind", "macro"), r.get("ticker")) for r in rows])
            self._commit()

    def calendar(self, start: str, end: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM calendar WHERE date>=? AND date<=? ORDER BY date, importance DESC, time_et", (start, end)).fetchall()]

    def save_brief(self, date: str, text: str, meta: dict | None = None) -> None:
        with self._wlock:
            self.conn.execute("INSERT OR REPLACE INTO briefs(date, created, text, meta) VALUES (?,?,?,?)",
                              (date, _iso(datetime.now(timezone.utc)), text, json.dumps(meta or {}, default=str)))
            self._commit()

    def latest_brief(self) -> dict | None:
        r = self.conn.execute("SELECT * FROM briefs ORDER BY date DESC LIMIT 1").fetchone()
        if not r:
            return None
        d = dict(r)
        d["meta"] = json.loads(d.get("meta") or "{}")
        return d

    def kv_get(self, k: str, default: Any = None) -> Any:
        r = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def kv_set(self, k: str, v: Any) -> None:
        with self._wlock:
            self.conn.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?,?)", (k, json.dumps(v, default=str)))
            self._commit()

    OPEN_CASE_STATUSES = ("open", "acknowledged", "in_review")

    def escalations_unread(self, targets: Iterable[str] | None = None, hours: int = 48) -> int:
        """Unread warning/critical relevant events of the last ``hours`` whose direct tickers include a target-list name."""
        ts = sorted(set(targets)) if targets is not None else sorted(config.holdings())
        if not ts:
            return 0
        since = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
        like = " OR ".join("e.tickers LIKE ?" for _ in ts)                    # direct tickers = the JSON list column (same field the action feed uses)
        return int(self.conn.execute(
            f"SELECT COUNT(*) FROM events e WHERE e.published>=? AND e.relevant=1 AND e.read=0 AND e.alert_level IN ('warning','critical') AND ({like})",
            [since, *[f'%"{t}"%' for t in ts]]).fetchone()[0])

    def stats(self, targets: Iterable[str] | None = None) -> dict:
        c = self.conn
        now = datetime.now(timezone.utc)
        d1 = _iso(now - timedelta(days=1))
        return {"events_total": c.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                "events_24h": c.execute("SELECT COUNT(*) FROM events WHERE published>=?", (d1,)).fetchone()[0],
                "relevant_24h": c.execute("SELECT COUNT(*) FROM events WHERE published>=? AND relevant=1", (d1,)).fetchone()[0],
                "llm_24h": c.execute("SELECT COUNT(*) FROM events WHERE published>=? AND llm=1", (d1,)).fetchone()[0],
                "alerts_24h": c.execute("SELECT COUNT(*) FROM alerts WHERE created>=?", (d1,)).fetchone()[0],
                "unread_alerts": c.execute("SELECT COUNT(*) FROM alerts a JOIN events e ON e.id=a.event_id WHERE e.read=0 AND a.created>=?", (d1,)).fetchone()[0],
                "cases_open": self.count_cases(self.OPEN_CASE_STATUSES),
                "escalations_unread": self.escalations_unread(targets)}

    def prune(self, days: int = 90) -> int:
        """Delete events older than ``days`` **except any still referenced as case evidence**.

        Retention must never destroy the evidence an audit trail points at: a closed case whose supporting
        events had been pruned could not be reconstructed, which defeats the purpose of keeping the case.
        Returns the number of events actually deleted.
        """
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        with self._wlock:
            referenced: set[str] = set()
            try:
                for (ev,) in self.conn.execute("SELECT evidence FROM cases WHERE evidence IS NOT NULL"):
                    try:
                        referenced.update(str(x) for x in (json.loads(ev) or []))
                    except (json.JSONDecodeError, TypeError):
                        continue
                for (eid,) in self.conn.execute("SELECT event_id FROM cases WHERE event_id IS NOT NULL"):
                    referenced.add(str(eid))
            except sqlite3.OperationalError:
                pass                                    # no cases table yet: nothing to protect
            if referenced:
                marks = ",".join("?" * len(referenced))
                cur = self.conn.execute(f"DELETE FROM events WHERE published<? AND id NOT IN ({marks})", [cutoff, *referenced])
            else:
                cur = self.conn.execute("DELETE FROM events WHERE published<?", (cutoff,))
            self.conn.execute("DELETE FROM alerts WHERE created<? AND (event_id IS NULL OR event_id NOT IN (SELECT id FROM events))", (cutoff,))
            self._commit()
        return cur.rowcount
