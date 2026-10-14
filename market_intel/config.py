"""Configuration: paths, credentials, tunables and the watchlist tiers.

Credentials
-----------
* Anthropic: ``ANTHROPIC_API_KEY`` (or ``ANTHROPIC_AUTH_TOKEN``) env var, else a file named
  ``Anthropic API Key.txt`` / ``anthropic_api_key.txt`` in the project root. Without it the system still
  runs: classification falls back to the rule engine and Q&A returns retrieved evidence without synthesis.
* FRED: reused from the ``ai_semi_intel`` settings (``FRED API Key.txt``).
* SEC EDGAR: needs a User-Agent with contact info (``SEC_USER_AGENT`` env var or the analyst's email).
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data_cache" / "market_intel"
DB_PATH = DATA_DIR / "market_intel.sqlite"
OUTPUTS = Path(os.environ.get("AI_SEMI_OUTPUTS") or (PROJECT_ROOT / "outputs")).resolve()   # same override as ai_semi_intel
LATEST = OUTPUTS / "latest"
MI_OUT = OUTPUTS / "market_intel"
LOGS = OUTPUTS / "logs"
for _p in (DATA_DIR, LATEST, MI_OUT, LOGS):
    _p.mkdir(parents=True, exist_ok=True)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

ANTHROPIC_KEY_FILES = ("Anthropic API Key.txt", "anthropic_api_key.txt", ".anthropic_api_key")
OPENAI_KEY_FILES = ("OpenAI API Key.txt", "openai_api_key.txt", ".openai_api_key")
FRED_KEY_FILES = ("FRED API Key.txt", "fred_api_key.txt")


def _read_key_file(names: tuple[str, ...]) -> str | None:
    for name in names:
        p = PROJECT_ROOT / name
        if p.exists():
            key = p.read_text().strip()
            if key:
                return key
    return None


def anthropic_api_key() -> str | None:
    """API key from the environment or a key file in the project root (exported to the env when found)."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    key = _read_key_file(ANTHROPIC_KEY_FILES)
    if key:
        os.environ.setdefault("ANTHROPIC_API_KEY", key)
    return key


def openai_api_key() -> str | None:
    """OpenAI key from the environment or a key file in the project root (exported to the env when found)."""
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return os.environ["OPENAI_API_KEY"].strip()
    key = _read_key_file(OPENAI_KEY_FILES)
    if key:
        os.environ.setdefault("OPENAI_API_KEY", key)
    return key


def llm_provider() -> str | None:
    """'anthropic' | 'openai' | None. MARKET_INTEL_LLM_PROVIDER forces one; otherwise Anthropic wins when both keys exist."""
    if os.environ.get("MARKET_INTEL_NO_LLM"):
        return None
    forced = os.environ.get("MARKET_INTEL_LLM_PROVIDER", "").strip().lower()
    if forced in ("anthropic", "openai"):
        return forced if (anthropic_api_key() if forced == "anthropic" else openai_api_key()) else None
    if anthropic_api_key():
        return "anthropic"
    if openai_api_key():
        return "openai"
    return None


def llm_available() -> bool:
    return llm_provider() is not None


def fred_api_key() -> str | None:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        return key
    return _read_key_file(FRED_KEY_FILES)


SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "MarketIntel Research siyan_chen@ucsb.edu")
SETTINGS_FILE = DATA_DIR / "settings.json"          # persistent user choices (e.g. model) read by the service and the CLI


def user_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    except json.JSONDecodeError:
        return {}


def save_user_setting(key: str, value) -> dict:
    d = user_settings()
    if value in (None, ""):
        d.pop(key, None)
    else:
        d[key] = value
    SETTINGS_FILE.write_text(json.dumps(d, indent=1))
    return d


def _default_model() -> str:
    return os.environ.get("MARKET_INTEL_MODEL", "") or str(user_settings().get("model", "") or "")


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = int(os.environ.get("MARKET_INTEL_PORT") or os.environ.get("PORT") or 8787)   # PORT: assigned by dev-preview tools
    poll_interval_market: int = 300          # seconds, 06:00-20:00 ET on weekdays
    poll_interval_off: int = 900             # seconds, otherwise
    market_hours: tuple = (6, 20)            # extended-hours window in ET
    model: str = field(default_factory=_default_model)   # empty = provider default (claude-opus-5 / best available OpenAI model); MARKET_INTEL_MODEL or settings.json
    llm_batch_size: int = 20                 # headlines per classification call
    llm_max_items_per_cycle: int = 60
    llm_effort: str = "low"                  # classification is a routine task
    llm_min_materiality: float = 0.25        # rule-engine materiality needed before spending an LLM call
    half_life_hours: float = 36.0            # decay of an event's contribution to the ticker score
    readthrough_cap: int = 12                # rule-engine read-through above this many names is withheld pending review (LLM confirms <= 8)
    lookback_days: int = 7
    notify: bool = True
    notify_min_level: str = "warning"        # info | warning | critical
    notify_max_per_cycle: int = 4
    price_move_min_pct: float = 0.04
    price_move_sigma: float = 2.5            # move must also exceed this many 20d daily sigmas
    price_tiers: tuple = (0.04, 0.08, 0.15)
    brief_hour_et: int = 7
    keep_days: int = 90
    yahoo_batch: int = 10
    yahoo_batches_per_cycle: int = 3
    google_every_n_cycles: int = 2
    google_max_queries: int = 8
    sec_backfill_hours: float = 6.0
    http_timeout: int = 15
    workers: int = 8
    case_sla_hours: dict = field(default_factory=lambda: {"critical": 4, "warning": 24})   # review-case SLA per alert level (P1 W2); due = created + SLA


SETTINGS = Settings()

LEVEL_RANK = {"info": 0, "warning": 1, "critical": 2}


def now_et(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(ET)


def is_market_hours(now: datetime | None = None, settings: Settings = SETTINGS) -> bool:
    t = now_et(now)
    return t.weekday() < 5 and settings.market_hours[0] <= t.hour < settings.market_hours[1]


# ----------------------------------------------------------------------------- watchlists
def _json_file(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        value = json.loads(p.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


_pinned_run = ContextVar("market_intel_pinned_run", default=None)


def run_folder(latest: Path | None = None) -> Path:
    """Resolve one published identity to its immutable archive when available."""
    source = Path(latest) if latest is not None else LATEST
    pinned = _pinned_run.get()
    if pinned is not None and pinned[0] == source:
        return pinned[1]
    run = _json_file(source / "run.json")
    rid = run.get("run_id")
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", rid):
        return source
    folder = source.parent / "history" / "runs" / rid
    archived = _json_file(folder / "run.json")
    snapshot = _json_file(folder / "snapshot.json")
    if archived == run and all(run.get(k) and snapshot.get(k) == run[k]
                               for k in ("run_id", "generated_at", "asof_session")):
        return folder
    return source


@contextmanager
def pinned_run(latest: Path | None = None):
    """Hold one run across all file reads made by a single API request."""
    source = Path(latest) if latest is not None else LATEST
    token = _pinned_run.set((source, run_folder(source)))
    try:
        yield
    finally:
        _pinned_run.reset(token)


def run_file(path: Path) -> Path:
    if path.parent != LATEST:
        return path
    # Live overlays and separately generated experiments retain their own clock.
    from ai_semi_intel.runs import RUN_FILES
    if path.name not in RUN_FILES:
        return path
    folder = run_folder()
    archived = folder / path.name
    if archived.exists() or (LATEST / "run_publishing.json").exists():
        return archived
    return path  # Older archives did not freeze every display artifact.


def _snapshot() -> dict:
    return _json_file(run_file(LATEST / "snapshot.json"))


def holdings() -> dict[str, float]:
    """Target list (ticker -> executable target weight) from the canonical run snapshot.

    Weights are the recommended construction scaled to the volatility target, so they sum to 1 - cash.
    Legacy snapshots written before the run manifest existed are returned as stored (see run_info()['basis']).
    """
    snap = _snapshot()
    w = snap.get("weights") or {}
    return {t: float(w.get(t, 0.0)) for t in snap.get("top", [])}


def holdings_model() -> dict[str, float]:
    """Fully-invested model weights of the primary construction (before the vol target), for display next to the executable weight."""
    snap = _snapshot()
    w = snap.get("weights_model") or snap.get("weights") or {}
    return {t: float(w.get(t, 0.0)) for t in snap.get("top", [])}


VALIDATION_STATUS = "NOT VALIDATED · exploratory"


def run_info(now: datetime | None = None, *, latest: Path | None = None,
             blocked_dir: Path | None = None) -> dict:
    """Identity and evidence state of the canonical run every page, API, brief and alert must quote."""
    folder = Path(latest) if latest is not None else run_folder()
    r = _json_file(folder / "run.json")
    snap = _json_file(folder / "snapshot.json")
    gate_dir = Path(blocked_dir) if blocked_dir is not None else Path(latest) if latest is not None else LATEST
    b = _json_file(gate_dir / "run_blocked.json")
    blocked = ({"reasons": ["latest failed-run record could not be read"]}
               if not b and (gate_dir / "run_blocked.json").exists() else None)
    if b:
        try:
            failed_at = datetime.fromisoformat(b["at"])
            generated_at = datetime.fromisoformat(r["generated_at"]) if r else None
            if failed_at.tzinfo is None or (generated_at is not None and generated_at.tzinfo is None):
                raise ValueError("run clock missing timezone")
            blocked = b if generated_at is None or failed_at > generated_at else None
        except (KeyError, TypeError, ValueError):
            blocked = {"at": b.get("at"), "reasons": ["latest failed-run clock could not be verified"]}
    if (gate_dir / "run_publishing.json").exists():
        blocked = {"reasons": ["target publication is in progress or needs recovery"]}
    if r:
        from ai_semi_intel.trading_policy import current_status
        target_clock = current_status(r, now or datetime.now(UTC))
        if blocked:
            target_clock = dict(target_clock, actionable_now=False,
                reasons=[*target_clock.get("reasons", []), "a later pipeline attempt is blocked"])
            if target_clock["status"] in ("OPEN", "PENDING_OPEN"):
                target_clock["status"] = "BLOCKED"
        pf = r.get("portfolio") if isinstance(r.get("portfolio"), dict) else {}
        return {"blocked": blocked, "run_id": r.get("run_id"), "portfolio_id": r.get("portfolio_id"), "asof_session": r.get("asof_session"), "generated_at": r.get("generated_at"),
                "supersedes": r.get("supersedes"), "approved_prior_run_id": r.get("approved_prior_run_id"), "status": r.get("status"),
                "validation_status": r.get("validation_status") or VALIDATION_STATUS, "validation": r.get("validation") or {}, "actionable": r.get("actionable"),
                "current_target_status": target_clock, "actionable_at_generation": r.get("actionable"),
                "actionable_now": target_clock["actionable_now"],
                "not_actionable_reasons": r.get("not_actionable_reasons") or [], "data_health": r.get("data_health") or {},
                "cash": pf.get("cash"), "invested": pf.get("invested"), "construction": pf.get("construction"), "vol_target": pf.get("vol_target"),
                "reconciles": pf.get("reconciles"), "wording": "target", "basis": "run_manifest", "code_hash": r.get("code_hash"), "config_hash": r.get("config_hash")}
    if snap:
        return {"run_id": snap.get("run_id") or f"legacy-{snap.get('date')}", "portfolio_id": snap.get("portfolio_id"), "asof_session": snap.get("date"),
                "generated_at": snap.get("generated_at"), "supersedes": snap.get("supersedes"), "approved_prior_run_id": None, "status": "legacy",
                "validation_status": snap.get("validation_status") or VALIDATION_STATUS, "validation": {}, "actionable": None, "actionable_now": False, "actionable_at_generation": None,
                "current_target_status": {"status": "INVALID_POLICY", "actionable_now": False, "reasons": ["legacy run has no verified execution clock"]}, "not_actionable_reasons": [],
                "data_health": {}, "cash": snap.get("cash"), "invested": None, "construction": snap.get("construction"), "vol_target": snap.get("vol_target"),
                "reconciles": None, "wording": "target", "basis": "legacy_snapshot", "code_hash": None, "config_hash": None}
    return {"blocked": blocked, "run_id": None, "asof_session": None, "generated_at": None, "validation_status": VALIDATION_STATUS, "validation": {}, "actionable": None,
            "actionable_now": False, "actionable_at_generation": None, "current_target_status": {"status": "MISSING", "actionable_now": False, "reasons": ["no pipeline run"]},
            "not_actionable_reasons": ["no pipeline run"], "data_health": {}, "cash": None, "basis": "none", "wording": "target"}


def universe_table() -> list[dict]:
    """Universe rows: ticker, name, segment, sub_segment, description, cik, next_earnings, market_cap."""
    rows: dict[str, dict] = {}
    try:
        from ai_semi_intel.config.universe import UNIVERSE  # type: ignore
        for t, (seg, sub, desc) in UNIVERSE.items():
            rows[t] = {"ticker": t, "segment": seg, "sub_segment": sub, "description": desc,
                       "name": "", "cik": None, "next_earnings": None, "market_cap": None}
    except Exception:
        pass
    p = LATEST / "universe_scores.csv"
    if p.exists():
        import csv
        with open(p, newline="") as f:
            for r in csv.DictReader(f):
                t = r.get("ticker") or r.get("")
                if not t:
                    continue
                row = rows.setdefault(t, {"ticker": t, "segment": r.get("segment", ""), "sub_segment": r.get("sub_segment", ""),
                                          "description": r.get("description", ""), "name": "", "cik": None,
                                          "next_earnings": None, "market_cap": None})
                row["name"] = r.get("name") or row["name"]
                try:
                    row["cik"] = int(float(r["cik"])) if r.get("cik") else row["cik"]
                except ValueError:
                    pass
                row["next_earnings"] = r.get("next_earnings") or row["next_earnings"]
                try:
                    row["market_cap"] = float(r["market_cap"]) if r.get("market_cap") else None
                except ValueError:
                    pass
    for t, extra in extra_watchlist().items():
        row = rows.setdefault(t, {"ticker": t, "segment": extra.get("segment", "Watchlist"), "sub_segment": "",
                                  "description": extra.get("description", ""), "name": extra.get("name", t),
                                  "cik": extra.get("cik"), "next_earnings": None, "market_cap": None})
        row["name"] = row["name"] or extra.get("name", t)
    return list(rows.values())


def extra_watchlist() -> dict[str, dict]:
    """Optional analyst watchlist: market_intel/watchlist.yaml -> {tickers: {XYZ: {name, aliases, segment}}}."""
    p = PKG_DIR / "watchlist.yaml"
    if not p.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(p.read_text()) or {}
    except Exception:
        return {}
    out = {}
    for t, v in (data.get("tickers") or {}).items():
        out[str(t).upper()] = v or {}
    return out


def next_earnings() -> dict[str, str]:
    return {r["ticker"]: r["next_earnings"] for r in universe_table() if r.get("next_earnings")}
