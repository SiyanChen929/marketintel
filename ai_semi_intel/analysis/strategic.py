"""Strategic-role pillar: LLM deep research with an evidence log.

The one place where judgment enters the score deliberately. Each company carries a versioned
assessment (data_cache/strategic/assessments/<TICKER>.json) scored on the rubric in
docs/STRATEGIC_RUBRIC.md, with cited evidence, kill criteria and watch items.

Refresh policy (event-driven, so the pillar moves with the market but not with narrative drift):
  * triggers: new 10-K/10-Q/8-K since the last assessment, |5-day return| > 10%, 30-day EPS revision
    > 10%, earnings within 7 days, assessment older than 90 days, or a manual request;
  * change control: a new assessment replaces the old one only if it cites evidence dated after
    the previous assessment (or it is the scheduled quarterly refresh); otherwise the prior stands;
  * decay: `news_direction` fades linearly to zero over 90 days from `news_asof`.

Assessor: two Claude API calls - a research turn with server-side web search / web fetch that
writes an evidence dossier, then a structured parse of the dossier into the rubric schema.
Runs in dry-run (no API calls, stored assessments only) when no credentials are configured.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from ..config.settings import DATA_CACHE, HISTORY, PROJECT_ROOT
from ..utils.io import log, read_json, write_json

STRAT_DIR = DATA_CACHE / "strategic"
ASSESS_DIR = STRAT_DIR / "assessments"
CONTEXT_DIR = STRAT_DIR / "context"
QUEUE_PATH = STRAT_DIR / "refresh_queue.json"
for _p in (ASSESS_DIR, CONTEXT_DIR):
    _p.mkdir(parents=True, exist_ok=True)

DIMENSION_WEIGHTS = {"scarcity": 0.25, "lock_in": 0.15, "roadmap": 0.15, "ai_exposure": 0.15, "pricing_power": 0.10,
                     "competitive_threat": 0.10, "geo_regulatory": 0.05, "execution_governance": 0.05}
SCARCITY_WEIGHTS = {"scarcity": 0.40, "lock_in": 0.20, "roadmap": 0.15, "ai_exposure": 0.15, "pricing_power": 0.10}
RISK_WEIGHTS = {"competitive_threat": 0.35, "geo_regulatory": 0.25, "execution_governance": 0.20, "customer_concentration": 0.20}
RISK_GATE, RISK_GATE_SCARCITY_OVERRIDE, HIGH_RISK_WEIGHT_CAP = 0.75, 0.80, 0.05
PILLAR_SCARCITY_SHARE = 0.75
NEWS_DECAY_DAYS = 90
STALE_DAYS = 90
MODEL = "claude-opus-5"
REFERENCE_PRIORS_PATH = PROJECT_ROOT / "data" / "strategic_reference_priors.csv"


# ------------------------------------------------------------------------- schema
class Dimension(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    rationale: str


class Evidence(BaseModel):
    date: str
    source: str
    url_or_ref: str
    claim: str
    dimension: str
    direction: Literal["+", "-", "0"]


class Assessment(BaseModel):
    ticker: str
    asof: str
    version: int = 1
    assessor: str
    dimensions: dict[str, Dimension]
    strategic_score: float = Field(ge=0.0, le=1.0)
    news_direction: float = Field(ge=-1.0, le=1.0)
    news_asof: str
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str
    kill_criteria: list[str]
    watch_items: list[str]
    evidence: list[Evidence]
    triggers_since_last: list[str] = []
    previous_version_score: float | None = None


def _d(dims: dict, k: str, default: float = np.nan) -> float:
    v = dims.get(k)
    if v is None:
        return default
    return float(v["score"] if isinstance(v, dict) else v.score)


def weighted_score(dims: dict) -> float:
    return float(sum(DIMENSION_WEIGHTS[k] * _d(dims, k, 0.5) for k in DIMENSION_WEIGHTS))


def scarcity_strength(dims: dict) -> float:
    return float(sum(w * _d(dims, k, 0.5) for k, w in SCARCITY_WEIGHTS.items()))


def llm_risk(dims: dict) -> float:
    """1 - weighted mean of the inverted risk dimensions (customer_concentration optional -> renormalised)."""
    tot, wsum = 0.0, 0.0
    for k, w in RISK_WEIGHTS.items():
        v = _d(dims, k)
        if not np.isnan(v):
            tot += w * v; wsum += w
    return float(1 - tot / wsum) if wsum else 0.5


def financial_risk(row: pd.Series | dict) -> float:
    """Data-derived risk 0-1 from filed and price data (no LLM input)."""
    g = (lambda k: row.get(k) if isinstance(row, dict) else row.get(k, np.nan))
    parts = []
    nd = g("net_debt_to_ebitda")
    if nd is not None and not pd.isna(nd):
        parts.append(float(np.clip((nd - 1.0) / 3.0, 0, 1)))          # 1x -> 0, 4x -> 1
    fm = g("fcf_margin")
    if fm is not None and not pd.isna(fm):
        parts.append(float(np.clip(-fm / 0.3, 0, 1)))                 # -30% FCF margin -> 1
    dd = g("max_dd_1y")
    if dd is not None and not pd.isna(dd):
        parts.append(float(np.clip((-dd - 0.3) / 0.4, 0, 1)))         # -30% -> 0, -70% -> 1
    vol = g("vol_1y")
    if vol is not None and not pd.isna(vol):
        parts.append(float(np.clip((vol - 0.4) / 0.8, 0, 1)))         # 40% -> 0, 120% -> 1
    return float(np.mean(parts)) if parts else 0.5


def risk_score(dims: dict, row: pd.Series | dict) -> float:
    return float(0.7 * llm_risk(dims) + 0.3 * financial_risk(row))


def pillar_score(dims: dict, row: pd.Series | dict) -> float:
    return float(PILLAR_SCARCITY_SHARE * scarcity_strength(dims) + (1 - PILLAR_SCARCITY_SHARE) * (1 - risk_score(dims, row)))


def risk_gate(scar: float, risk: float) -> tuple[bool, bool]:
    """(eligible, weight_capped): high-risk names are excluded unless they are genuine bottlenecks, which are capped."""
    if risk is None or np.isnan(risk) or risk < RISK_GATE:
        return True, False
    override = scar is not None and not np.isnan(scar) and scar >= RISK_GATE_SCARCITY_OVERRIDE
    return override, override                     # stays only as a capped bottleneck; otherwise excluded


# ------------------------------------------------------------------------- storage
def load_assessment(ticker: str) -> dict | None:
    try:
        a = read_json(ASSESS_DIR / f"{ticker}.json")
        return a if a and "dimensions" in a else None
    except Exception:                            # partially written file (research agent mid-write)
        return None


def load_all() -> dict[str, dict]:
    return {p.stem: read_json(p) for p in ASSESS_DIR.glob("*.json")}


def load_reference_priors() -> pd.DataFrame:
    """Secondary-source priors supplied by the reference paper; never treated as verified assessments."""
    if not REFERENCE_PRIORS_PATH.exists():
        return pd.DataFrame()
    p = pd.read_csv(REFERENCE_PRIORS_PATH).set_index("ticker")
    return p


def save_assessment(a: dict) -> None:
    """Archive every version without overwriting; latest remains a replaceable pointer."""
    from ..research.forward_registry import canonical_bytes, write_once
    ticker = a["ticker"]
    if not isinstance(ticker, str) or not ticker or any(x in ticker for x in ("/", "\\", "..")):
        raise ValueError("unsafe assessment ticker")
    prev = load_assessment(a["ticker"])
    hist = ASSESS_DIR / "history"
    hist.mkdir(exist_ok=True)
    def archive(value):
        path = hist / f"{ticker}_v{value.get('version', 1)}.json"
        # Existing legacy versions may use a different JSON indentation.
        if path.exists():
            if canonical_bytes(read_json(path)) != canonical_bytes(value):
                raise ValueError(f"immutable assessment history conflict: {path.name}")
        else:
            write_once(path, value)
    if prev:
        archive(prev)
        a["version"] = int(prev.get("version", 1)) + 1
        a["previous_version_score"] = prev.get("strategic_score")
    a.setdefault("stored_at", datetime.now(timezone.utc).isoformat())
    archive(a)
    with tempfile.NamedTemporaryFile(dir=ASSESS_DIR, prefix=".assessment-", delete=False) as handle:
        pending = Path(handle.name)
        handle.write(canonical_bytes(a)); handle.flush(); os.fsync(handle.fileno())
    try:
        os.replace(pending, ASSESS_DIR / f"{ticker}.json")
    finally:
        pending.unlink(missing_ok=True)


def validate(a: dict) -> dict:
    """Normalise and validate an assessment dict; recompute the weighted score from dimensions."""
    a = dict(a)
    a["strategic_score"] = round(weighted_score(a["dimensions"]), 4)
    a.setdefault("triggers_since_last", []); a.setdefault("version", 1); a.setdefault("previous_version_score", None)
    a.setdefault("news_asof", a["asof"])
    Assessment.model_validate(a)
    return a


# ------------------------------------------------------------------------- change control & decay
def accept_change(new: dict, prev: dict | None, scheduled: bool = False) -> tuple[bool, str]:
    """A new assessment is accepted if it is the first one, a scheduled refresh, or cites evidence
    dated after the previous assessment date."""
    if prev is None or scheduled:
        return True, "first assessment" if prev is None else "scheduled refresh"
    prev_date = pd.Timestamp(prev["asof"])
    new_ev = [e for e in new.get("evidence", []) if _safe_ts(e.get("date")) is not None and _safe_ts(e["date"]) > prev_date]
    if new_ev:
        return True, f"{len(new_ev)} new evidence item(s) after {prev['asof']}"
    return False, "no new evidence; previous assessment retained"


def _safe_ts(s):
    try:
        stamp = pd.Timestamp(s)
        return None if pd.isna(stamp) else stamp
    except Exception:
        return None


def decayed_news(a: dict, asof: datetime) -> float:
    d = _safe_ts(a.get("news_asof") or a.get("asof"))
    if d is None:
        return 0.0
    age = _age_days(d, asof)
    if age < 0:
        return 0.0
    return float(np.clip(a.get("news_direction", 0.0), -1, 1)) * max(0.0, 1 - age / NEWS_DECAY_DAYS)


def _age_days(source, asof) -> int:
    from ..trading_policy import ET
    def local(value):
        stamp = pd.Timestamp(value)
        return stamp.tz_convert(ET).tz_localize(None) if stamp.tzinfo is not None else stamp
    return (local(asof) - local(source)).days


def is_stale(a: dict, asof: datetime) -> bool:
    d = _safe_ts(a.get("asof"))
    return d is None or _age_days(d, asof) < 0 or _age_days(d, asof) > STALE_DAYS


def _availability_issue(a: dict, knowledge_at) -> str | None:
    """Reject explicit future claims; absent legacy provenance stays unverified."""
    from ..trading_policy import ET, aware
    clock = aware(knowledge_at)
    asof = _safe_ts(a.get("asof"))
    if asof is None:
        return "invalid_asof"
    if (asof.tz_convert("UTC") > clock if asof.tzinfo is not None else asof.date() > clock.tz_convert(ET).date()):
        return "future_asof"
    sources = [a, a.get("provenance") or {}, *(a.get("evidence") or [])]
    for source in sources:
        if not isinstance(source, dict):
            return "invalid_evidence"
        for key in ("known_at", "published_at", "available_at"):
            if not source.get(key):
                continue
            try:
                if aware(source[key]) > clock:
                    return "future_" + key
            except (ValueError, TypeError, RuntimeError):
                return "invalid_" + key
        if source.get("date"):
            stamp = _safe_ts(source["date"])
            if stamp is None:
                return "invalid_evidence_date"
            if (stamp.tz_convert("UTC") > clock if stamp.tzinfo is not None else stamp.date() > clock.tz_convert(ET).date()):
                return "future_evidence_date"
    return None


# ------------------------------------------------------------------------- triggers
_OVERLAY_CACHE: dict = {}


def _overlay_row(ticker: str):
    """Row of the market_intel event overlay (outputs/latest/event_overlay.csv) if fresh (< 36h)."""
    if "df" not in _OVERLAY_CACHE:
        try:
            from market_intel.scoring import load_overlay
            df = load_overlay(36.0)
            _OVERLAY_CACHE["df"] = df.set_index("ticker") if df is not None and "ticker" in df.columns else None
        except Exception:
            _OVERLAY_CACHE["df"] = None
    df = _OVERLAY_CACHE["df"]
    return df.loc[ticker].to_dict() if df is not None and ticker in df.index else None


def detect_triggers(feat: pd.DataFrame, px: pd.DataFrame, asof: datetime) -> dict[str, list[str]]:
    """Per ticker, list of refresh triggers fired since the last assessment."""
    out: dict[str, list[str]] = {}
    for t, r in feat.iterrows():
        a = load_assessment(t)
        trig = []
        last = _safe_ts(a["asof"]) if a else None
        if a is None:
            trig.append("no assessment")
        else:
            if is_stale(a, asof):
                trig.append(f"assessment {(pd.Timestamp(asof) - last).days} days old")
            for col, lab in (("latest_10q_date", "10-Q"), ("latest_10k_date", "10-K")):
                d = _safe_ts(r.get(col))
                if d is not None and last is not None and d > last:
                    trig.append(f"new {lab} filed {d.date()}")
            if int(r.get("filings_30d") or 0) > 0 and last is not None and (pd.Timestamp(asof) - last).days <= 30:
                trig.append("material filing in last 30 days")
        if t in px.columns:
            s = px[t].dropna()
            if len(s) > 6:
                r5 = s.iloc[-1] / s.iloc[-6] - 1
                if abs(r5) >= 0.10:
                    trig.append(f"5-day move {r5:+.0%}")
        rev = r.get("eps_rev_30d")
        if rev is not None and not np.isnan(rev) and abs(rev) >= 0.10:
            trig.append(f"30-day EPS revision {rev:+.0%}")
        ev = _overlay_row(t)
        if ev is not None and (abs(float(ev.get("event_score_7d", 0) or 0)) >= 3.0 or str(ev.get("alert_level", "")) in ("critical", "warning")):
            trig.append(f"market_intel {ev.get('alert_level')}: {str(ev.get('last_event_title', ''))[:80]}")
        ne = _safe_ts(r.get("next_earnings"))
        if ne is not None and 0 <= (ne.tz_localize(None) - pd.Timestamp(asof)).days <= 7:
            trig.append("earnings within 7 days")
        if trig:
            out[t] = trig
    write_json({"asof": str(pd.Timestamp(asof).date()), "queue": out}, QUEUE_PATH)
    return out


# ------------------------------------------------------------------------- feature output
def strategic_features(feat, asof: datetime, *, knowledge_at=None, assessments: dict | None = None) -> pd.DataFrame:
    """Per-ticker strategic pillar for the decision score.

    Sources, in order of precedence: (1) evidence-logged LLM assessment (data_cache/strategic/assessments),
    (2) reference prior from the research paper's Table 6 (confidence-capped), (3) missing -> neutral.
    Outputs both the scarcity side (`scarcity_raw` = scarcity strength, used by the scarcity pillar) and the
    risk side (`llm_resilience` for the risk-resilience pillar, plus the combined `risk_score` and the gate).
    `feat` may be the feature DataFrame (enables data-derived financial risk) or a list of tickers."""
    if not isinstance(feat, pd.DataFrame):
        feat = pd.DataFrame(index=pd.Index(list(feat), name="ticker"))
    priors = load_reference_priors()
    knowledge_at = knowledge_at or datetime.now(timezone.utc)
    rows = []
    for t, r in feat.iterrows():
        fin = financial_risk(r)
        a = assessments.get(t) if assessments is not None else load_assessment(t)
        rejection = _availability_issue(a, knowledge_at) if a else None
        if rejection:
            a = None
        if not a:
            base = {"ticker": t, "strategic_score": np.nan, "scarcity_raw": np.nan, "scarcity_strength": np.nan, "llm_risk": np.nan,
                    "llm_resilience": np.nan, "financial_risk": fin, "risk_score": np.nan, "strategic_pillar": np.nan, "news_direction": np.nan,
                    "strategic_confidence": np.nan, "strategic_asof": None, "strategic_stale": True, "strategic_thesis": None,
                    "strategic_version": 0, "strategic_source": "missing", "strategic_source_ref": None, "risk_eligible": True, "risk_capped": False,
                    "kill_criteria": None, "watch_items": None,
                    "strategic_rejection_reason": rejection, "strategic_availability": "rejected" if rejection else "missing"}
            if t in priors.index:
                pr = priors.loc[t]; p_asof = str(pr["asof"])
                prior_rejection = _availability_issue(pr.to_dict(), knowledge_at)
                if prior_rejection:
                    base.update(strategic_rejection_reason="reference_prior:" + prior_rejection, strategic_availability="rejected")
                    rows.append(base)
                    continue
                base.update(strategic_score=float(pr["strategic_score"]), scarcity_raw=float(pr["strategic_score"]), scarcity_strength=float(pr["strategic_score"]),
                            news_direction=0.0, strategic_confidence=float(pr["confidence"]), strategic_asof=p_asof,
                            strategic_stale=is_stale({"asof": p_asof}, asof), strategic_source="reference_prior",
                            strategic_source_ref=pr["source_ref"], strategic_availability="legacy_unverified")
            rows.append(base); continue
        dims = a.get("dimensions") or {}
        scar, rk = scarcity_strength(dims), risk_score(dims, r)
        elig, capped = risk_gate(scar, rk)
        rows.append({"ticker": t, "strategic_score": a.get("strategic_score"), "scarcity_raw": scar, "scarcity_strength": scar,
                     "llm_risk": llm_risk(dims), "llm_resilience": 1 - llm_risk(dims), "financial_risk": fin, "risk_score": rk,
                     "strategic_pillar": pillar_score(dims, r), "news_direction": decayed_news(a, asof),
                     "strategic_confidence": a.get("confidence"), "strategic_asof": a.get("asof"), "strategic_stale": is_stale(a, asof),
                     "strategic_thesis": a.get("thesis"), "strategic_version": a.get("version", 1),
                     "strategic_source": "evidence_assessment", "strategic_source_ref": f"assessment:{t}:v{a.get('version', 1)}",
                     "strategic_availability": "timestamped_source_unverified" if (a.get("provenance") or {}).get("known_at") else "legacy_unverified",
                     "strategic_rejection_reason": None,
                     "risk_eligible": elig, "risk_capped": capped,
                     "kill_criteria": " | ".join(a.get("kill_criteria", [])), "watch_items": " | ".join(a.get("watch_items", [])),
                     **{f"strat_{k}": _d(dims, k) for k in list(DIMENSION_WEIGHTS) + ["customer_concentration"]}})
    return pd.DataFrame(rows).set_index("ticker")


# ------------------------------------------------------------------------- LLM assessor
def _client():
    """Anthropic client, or None (dry-run) when no credentials are configured.
    Credentials: ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN env vars, or an `ant auth login` profile."""
    has_env = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    has_profile = any((Path.home() / ".config" / "anthropic").glob("*")) if (Path.home() / ".config" / "anthropic").exists() else False
    if not (has_env or has_profile):
        log.info("strategic assessor in dry-run mode (no ANTHROPIC_API_KEY / auth profile): stored assessments only")
        return None
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception as e:                        # SDK missing or misconfigured
        log.warning("strategic assessor in dry-run mode: %s", str(e)[:120])
        return None


def runtime_status() -> dict:
    """Strategic assessor readiness only; configuration is not a successful request."""
    import importlib.util
    configured = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    auth_dir = Path.home() / ".config" / "anthropic"
    profile_present = auth_dir.exists() and any(auth_dir.glob("*"))
    sdk_available = importlib.util.find_spec("anthropic") is not None
    # Never expose credential values or treat another LLM provider as usable here.
    return {"provider": "anthropic", "model": MODEL, "credentials_configured": configured,
            "profile_present": profile_present, "sdk_available": sdk_available,
            "client_available": bool((configured or profile_present) and sdk_available),
            "availability_basis": "configuration_only; no network authentication test",
            "request_verified": False}
RESEARCH_SYSTEM = (
    "You are a senior semiconductor and AI-infrastructure equity analyst. Research the company below for the "
    "strategic-role rubric of an equity research process. Use web search and, where a URL is given, web fetch. "
    "Focus on: control of scarce capabilities in the AI build-out, switching costs and design-in cycles, technology "
    "roadmap versus peers, share of revenue tied to AI data-centre demand, pricing power, competitive threats "
    "(custom silicon, China, new entrants), export-control and geopolitical exposure, and management execution. "
    "Prioritise the last 90 days for new information. Write an evidence dossier: for every claim give the date, "
    "source, URL or filing reference, and whether it strengthens (+) or weakens (-) the strategic role. "
    "Be specific and sceptical; distinguish company statements from third-party evidence; note anything you could not verify."
)

PARSE_INSTRUCTIONS = (
    "Convert the dossier into the rubric assessment. Score each dimension 0.0-1.0 using the anchors: "
    "scarcity (0 commodity / 0.5 one of 3-5 credible suppliers / 1 sole or dominant source of a bottleneck), "
    "lock_in (0 easily swapped / 0.5 multi-quarter qualification / 1 multi-year design-ins or ecosystem control), "
    "roadmap (0 trailing / 0.5 parity / 1 clear generation lead), ai_exposure (0 <10% of revenue / 0.5 25-50% / 1 >50% with visibility), "
    "pricing_power (0 price-taker / 0.5 stable / 1 contract price increases or LTAs), competitive_threat inverted (1 = no credible substitute), "
    "geo_regulatory inverted (1 = minimal exposure), execution_governance (0 restatements or repeated misses / 0.5 normal / 1 conservative and clean), "
    "customer_concentration inverted (1 = diversified, 0 = one customer above 50% of revenue). "
    "news_direction is -1..+1 for the direction of information in the last 90 days only. Evidence items must carry dates and sources from the dossier. "
    "Keep thesis under 60 words; 2-4 kill criteria; 2-4 watch items; 3-8 evidence items. Score relative to the AI/semiconductor value chain."
)


def _context_text(ticker: str) -> str:
    ctx = read_json(CONTEXT_DIR / f"{ticker}.json", {})
    keep = {k: v for k, v in ctx.items() if k != "business_summary"}
    return f"Company context (as of {ctx.get('asof')}):\n{json.dumps(keep, indent=1, default=str)}\n\nBusiness summary:\n{ctx.get('business_summary', '')}"


def assess_with_api(ticker: str, triggers: list[str] | None = None, client=None, max_searches: int = 6) -> dict | None:
    """Two-step research -> structured assessment. Returns a validated assessment dict or None."""
    client = client or _client()
    if client is None:
        return None
    ctx_text = _context_text(ticker)
    url = (read_json(CONTEXT_DIR / f"{ticker}.json", {}) or {}).get("latest_10k_url")
    prompt = (f"{ctx_text}\n\nLatest annual report (10-K/20-F) URL: {url or 'n/a'}\n"
              f"Refresh triggers: {', '.join(triggers or ['scheduled'])}\n\nProduce the evidence dossier for {ticker}.")
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
    if url:
        tools.append({"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 2, "max_content_tokens": 40000})
    with client.messages.stream(model=MODEL, max_tokens=16000, system=RESEARCH_SYSTEM, tools=tools,
                                thinking={"type": "adaptive"}, output_config={"effort": "high"},
                                messages=[{"role": "user", "content": prompt}]) as stream:
        research = stream.get_final_message()
    if research.stop_reason == "refusal":
        log.warning("%s: research refused (%s)", ticker, getattr(research, "stop_details", None)); return None
    dossier = "\n".join(b.text for b in research.content if b.type == "text")
    parse_user = f"Ticker: {ticker}. Today: {datetime.today().date()}.\n\nDOSSIER:\n{dossier}"
    parsed = client.messages.parse(model=MODEL, max_tokens=16000,
                                   system=PARSE_INSTRUCTIONS,
                                   messages=[{"role": "user", "content": parse_user}],
                                   output_format=Assessment)
    a = parsed.parsed_output.model_dump()
    a.update(ticker=ticker, asof=str(datetime.today().date()), assessor=f"{MODEL}-api", triggers_since_last=triggers or [])
    a["usage"] = {"research_in": research.usage.input_tokens, "research_out": research.usage.output_tokens,
                  "parse_in": parsed.usage.input_tokens, "parse_out": parsed.usage.output_tokens}
    from ..research.forward_registry import digest
    prompts = {"model": MODEL, "research_system": RESEARCH_SYSTEM, "research_user": prompt,
               "parse_system": PARSE_INSTRUCTIONS, "parse_user": parse_user, "tools": tools}
    a["provenance"] = {"schema": "strategic_assessment_provenance.v1", "provider": "anthropic", "model": MODEL,
                       "known_at": datetime.now(timezone.utc).isoformat(), "prompt_hash": digest(prompts),
                       "prompt_template_hash": digest({"research_system": RESEARCH_SYSTEM, "parse_system": PARSE_INSTRUCTIONS, "model": MODEL}),
                       "evidence_hash": digest(a.get("evidence", [])), "context_hash": digest(ctx_text),
                       "dossier_hash": digest(dossier), "prompts": prompts,
                       "source_availability_status": "retrieved_citations_not_independently_verified"}
    return validate(a)


def refresh(feat: pd.DataFrame, px: pd.DataFrame, asof: datetime, max_per_run: int = 12, scheduled: bool = False,
            allow_llm_refresh: bool = True) -> dict:
    """Process the trigger queue: re-assess up to `max_per_run` names (API), apply change control."""
    queue = detect_triggers(feat, px, asof)
    enabled = allow_llm_refresh and max_per_run > 0
    client = _client() if enabled else None
    report = {"queued": len(queue), "assessed": [], "retained": [], "dry_run": client is None, "errors": [],
              "allow_llm_refresh": bool(allow_llm_refresh), "client_available": client is not None if enabled else None,
              "provider": "anthropic", "model": MODEL,
              "status": "enabled" if client is not None else "unavailable" if enabled else "disabled_by_policy"}
    if client is None:
        return report
    # most triggers first
    for t, trig in sorted(queue.items(), key=lambda kv: -len(kv[1]))[:max_per_run]:
        try:
            new = assess_with_api(t, trig, client)
            if new is None:
                continue
            prev = load_assessment(t)
            ok, why = accept_change(new, prev, scheduled=scheduled or (prev is not None and is_stale(prev, asof)))
            if ok:
                save_assessment(new); report["assessed"].append({"ticker": t, "why": why, "score": new["strategic_score"], "prev": (prev or {}).get("strategic_score")})
            else:
                report["retained"].append({"ticker": t, "why": why})
        except Exception as e:                    # pragma: no cover - network
            report["errors"].append({"ticker": t, "error": str(e)[:200]}); log.warning("strategic %s failed: %s", t, e)
    return report


def snapshot(asof: datetime, *, assessments: dict | None = None) -> Path:
    """Archive all current assessments (for the daily history and live validation)."""
    hdir = HISTORY / str(pd.Timestamp(asof).date()); hdir.mkdir(parents=True, exist_ok=True)
    p = hdir / "strategic_snapshot.json"
    write_json(load_all() if assessments is None else assessments, p)
    return p
