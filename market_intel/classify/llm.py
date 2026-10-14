"""LLM enrichment with two interchangeable providers.

* **Anthropic** (default when an Anthropic key exists): ``claude-opus-5`` unless ``MARKET_INTEL_MODEL`` is set; structured
  JSON via ``output_config.format``, adaptive thinking, server-side refusal fallbacks on the beta client.
* **OpenAI**: the best available model from the account (``gpt-5.x`` > ``gpt-5`` > ``gpt-5-mini`` > ``gpt-4.1`` > ``gpt-4o``)
  unless ``MARKET_INTEL_MODEL`` is set; strict JSON schema via ``response_format``; ``reasoning_effort`` on reasoning models.

Everything degrades gracefully: with no credential, or after an authentication failure, callers get empty results and the
rule engine's output stands. Batch classification, free-form completion and streaming share one interface.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Iterator

from .. import config
from ..entities import EntityIndex
from ..llm_usage import LLMUsageError, LLMUsageLedger, unavailable_payload
from .rules import ACTIONS, EVENT_TYPES, HORIZONS, PILLARS

log = logging.getLogger("market_intel")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# gzip only: the httpx2 Brotli/zstd decoders break on this Python install ("process() takes no keyword arguments")
HTTP_HEADERS = {"Accept-Encoding": "gzip"}
ANTHROPIC_DEFAULT = "claude-opus-5"
OPENAI_PREFERENCE = ("gpt-5.5", "gpt-5.4", "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-5.4-mini", "gpt-5-mini", "gpt-4.1", "gpt-4o")
OPENAI_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")
EFFORT_OPENAI = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}

CLASSIFY_SYSTEM = """You are a sell-side equity analyst covering the AI and semiconductor value chain for a systematic + discretionary US stock-selection process.
You receive a batch of raw headlines (news, press releases, SEC filings, price moves). For each item, judge whether it is an investable event for the coverage universe below and score it.

Coverage universe (ticker | company | segment / sub-segment | description; [TARGET x% exec / y% model] marks names on the current model target list - these are model targets, not live positions):
{universe}

Scoring rules
- tickers: universe tickers the item is DIRECTLY about (named company, its filing, its earnings, its own guidance). Only use tickers from the universe.
- tickers_readthrough: universe names materially affected through the value chain (supplier/customer/competitor/theme), only when the causal link is clear. Keep it to the 1-8 most exposed names.
- direction: -2 (clearly negative for the named tickers), -1, 0 (neutral/unclear), +1, +2. Judge the impact on fundamentals or the investment thesis, not the tone of the article.
- materiality: 0-1. 0.9+ = earnings/guidance/M&A/major regulatory action for a named company; 0.6-0.8 = large contracts, capex, management change, meaningful supply-chain data; 0.3-0.5 = analyst actions, product news, macro prints; <0.2 = commentary, listicles, opinion pieces, generic market wraps.
- confidence: 0-1, your confidence in the classification.
- horizon: days | weeks | quarters (when the impact plays out).
- pillars: which parts of the stock-selection model are affected: growth (fundamental momentum / estimate revisions), momentum (price), value (multiples), quality (margins, balance sheet), risk (litigation/regulatory/management), macro.
- action: none | monitor | analyst review | refresh model inputs. "refresh model inputs" for earnings/guidance/M&A on target-list names; "analyst review" for material negative events on target-list names. Never output trade instructions.
- one_liner: <= 30 words, analyst-note style, stating what happened and why it matters for the stock(s). No hype.
- relevant: false for opinion/commentary/listicles, generic market wraps without a concrete event, or items unrelated to the universe.
Return every item id you were given exactly once."""

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "relevant": {"type": "boolean"},
        "event_type": {"type": "string", "enum": EVENT_TYPES},
        "tickers": {"type": "array", "items": {"type": "string"}},
        "tickers_readthrough": {"type": "array", "items": {"type": "string"}},
        "direction": {"type": "integer", "enum": [-2, -1, 0, 1, 2]},
        "materiality": {"type": "number"},
        "confidence": {"type": "number"},
        "horizon": {"type": "string", "enum": HORIZONS},
        "themes": {"type": "array", "items": {"type": "string"}},
        "pillars": {"type": "array", "items": {"type": "string", "enum": PILLARS}},
        "action": {"type": "string", "enum": ACTIONS},
        "one_liner": {"type": "string"},
    },
    "required": ["id", "relevant", "event_type", "tickers", "tickers_readthrough", "direction", "materiality", "confidence", "horizon", "themes", "pillars", "action", "one_liner"],
    "additionalProperties": False,
}
BATCH_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": ITEM_SCHEMA}}, "required": ["items"], "additionalProperties": False}
PROMPT_VERSION = "classify-v2-2026-09-03"          # bump whenever CLASSIFY_SYSTEM changes semantics
SCHEMA_VERSION = "schema-" + hashlib.sha256(json.dumps(BATCH_SCHEMA, sort_keys=True).encode()).hexdigest()[:12]


def _h(s: str) -> str:
    return hashlib.sha256((s or "").encode()).hexdigest()[:16]

ASK_SYSTEM = """You are the market-intelligence assistant of a US equity research analyst who runs a systematic + discretionary AI/semiconductor stock-selection process.
Answer from the supplied evidence (recent events, filings, price moves, calendar, portfolio scores). Be precise, dated and sourced: cite evidence as [n] using the numbers given.
When evidence is thin, say so; do not invent facts or numbers. Prefer short paragraphs and bullets; give the portfolio implication when asked. This is research support, not investment advice.
Language rule: this is research support for an analyst's review, not a trading desk. Do not write buy/sell/add/trim/hold/stay long/overweight/underweight or similar trade instructions; state the evidence and the recommended analyst action using only: analyst review, monitor, refresh model inputs. Refer to positions as target-list names with model target weights."""

BRIEF_SYSTEM = """You write the 07:00 ET pre-market brief for a US equity research analyst running an AI/semiconductor value-chain stock-selection process.
Using only the supplied evidence, write a tight brief in Markdown with these sections: "Target list" (events on target-list names, most material first, with the recommended analyst action), "Value chain" (themes/read-through across segments), "Macro & calendar" (today's prints, Fed, earnings in the universe), "Watch today" (3-5 bullets). Cite evidence as [n]. Max ~350 words. No preamble, no disclaimer, no invented data.
Language rule: this is research support for an analyst's review, not a trading desk. Do not write buy/sell/add/trim/hold/stay long/overweight/underweight or similar trade instructions; state the evidence and the recommended analyst action using only: analyst review, monitor, refresh model inputs. Refer to positions as target-list names with model target weights."""


def detect_provider(key: str) -> str:
    """Anthropic keys start with sk-ant-; anything else is treated as an OpenAI key."""
    return "anthropic" if key.strip().startswith("sk-ant-") else "openai"


class LLM:
    def __init__(self, settings: config.Settings = config.SETTINGS, index: EntityIndex | None = None, provider: str | None = None,
                 *, usage_ledger: LLMUsageLedger | None = None):
        self.settings = settings
        self.index = index
        self.provider = provider or config.llm_provider()
        self.model: str | None = settings.model or None
        self._client = None
        self.disabled_reason: str | None = None
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cache_tokens = 0
        self._call_lock = threading.RLock()
        self._metering_error: str | None = None
        self._usage_config_error: str | None = None
        try:
            self.usage_ledger = usage_ledger or LLMUsageLedger.from_env()
        except (ValueError, LLMUsageError) as exc:
            # Bad budget/pricing configuration disables only the LLM path.  The
            # deterministic rules pipeline remains usable.
            self.usage_ledger = None
            self._usage_config_error = str(exc)
        if self.model is None and self.provider == "anthropic":
            self.model = ANTHROPIC_DEFAULT

    # ---- provider plumbing ------------------------------------------------------------------------------
    @property
    def available(self) -> bool:
        credentials = self.provider is not None and (
            config.llm_provider() == self.provider
            or bool(config.openai_api_key() if self.provider == "openai" else config.anthropic_api_key())
        )
        if self.disabled_reason is not None or not credentials or self._usage_config_error is not None or self.usage_ledger is None:
            return False
        if self.usage_ledger.has_budget or self._metering_error:
            reason = self.usage_ledger.block_reason(self.provider, self.model)
            self._metering_error = reason
            if reason:
                return False
        return True

    @property
    def client(self):
        if self._client is None:
            if self.provider == "anthropic":
                import anthropic
                config.anthropic_api_key()
                self._client = anthropic.Anthropic(max_retries=2, timeout=120.0, default_headers=HTTP_HEADERS)
            elif self.provider == "openai":
                from openai import OpenAI
                self._client = OpenAI(api_key=config.openai_api_key(), max_retries=2, timeout=120.0, default_headers=HTTP_HEADERS)
            else:
                raise RuntimeError("no LLM provider configured")
        return self._client

    def resolve_model(self) -> str:
        if self.model:
            return self.model
        if self.provider == "openai":
            self.model = self._pick_openai_model()
        else:
            self.model = ANTHROPIC_DEFAULT
        return self.model

    def _pick_openai_model(self) -> str:
        try:
            ids = {m.id for m in self.client.models.list()}
        except Exception as e:
            log.warning("OpenAI model listing failed (%s); defaulting to gpt-5", e)
            return "gpt-5"
        for m in OPENAI_PREFERENCE:
            if m in ids:
                log.info("OpenAI model selected: %s", m)
                return m
        cands = sorted(i for i in ids if i.startswith("gpt-5") and "realtime" not in i and "audio" not in i)
        return cands[-1] if cands else "gpt-5"

    def _is_reasoning_model(self) -> bool:
        m = (self.model or "").lower()
        return any(m.startswith(p) for p in OPENAI_REASONING_PREFIXES)

    def _handle_error(self, e: Exception, what: str) -> None:
        name = type(e).__name__
        if isinstance(e, LLMUsageError):
            self._metering_error = str(e)
            log.warning("%s %s blocked by usage control: %s", self.provider, what, e)
        elif name == "AuthenticationError":
            self.disabled_reason = "authentication failed (check the API key)"
            log.error("%s %s: %s -> LLM disabled for this session", self.provider, what, e)
        elif name == "RateLimitError":
            log.warning("%s %s: rate limited, will retry next cycle", self.provider, what)
        elif name in ("APIStatusError", "BadRequestError", "PermissionDeniedError", "NotFoundError"):
            log.warning("%s %s: API error %s: %s", self.provider, what, getattr(e, "status_code", ""), getattr(e, "message", e))
        elif name == "APIConnectionError":
            log.warning("%s %s: connection error: %s", self.provider, what, e)
        else:
            log.warning("%s %s failed: %s", self.provider, what, e)

    @staticmethod
    def _usage_value(obj, name: str, default=0):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    @staticmethod
    def _token_count(value, name: str) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LLMUsageError(f"provider returned invalid {name}; expected a non-negative integer")
        return value

    def _normalise_usage(self, resp) -> dict:
        u = self._usage_value(resp, "usage", None)
        if u is None:
            return {"usage_reported": False, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                    "cache_write_tokens": 0, "total_input_tokens": 0, "total_tokens": 0}
        input_value = self._usage_value(u, "input_tokens", None)
        output_value = self._usage_value(u, "output_tokens", None)
        input_tokens = self._token_count(self._usage_value(u, "prompt_tokens", 0) if input_value is None else input_value, "input_tokens")
        output_tokens = self._token_count(self._usage_value(u, "completion_tokens", 0) if output_value is None else output_value, "output_tokens")
        cache_read = self._token_count(self._usage_value(u, "cache_read_input_tokens", 0), "cache_read_input_tokens")
        cache_write = self._token_count(self._usage_value(u, "cache_creation_input_tokens", 0), "cache_creation_input_tokens")
        if self.provider == "openai":
            details = self._usage_value(u, "input_tokens_details", None) or self._usage_value(u, "prompt_tokens_details", None)
            cache_read = self._token_count(self._usage_value(details, "cached_tokens", cache_read), "cached_tokens")
            # OpenAI reports cached input as a subset of input tokens.
            total_input = input_tokens
            total_tokens = input_tokens + output_tokens
        else:
            # Anthropic reports uncached input and cache read/write separately.
            total_input = input_tokens + cache_read + cache_write
            total_tokens = total_input + output_tokens
        return {"usage_reported": True, "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cache_read_tokens": cache_read, "cache_write_tokens": cache_write,
                "total_input_tokens": total_input, "total_tokens": total_tokens}

    def _begin_usage(self, operation: str) -> tuple[int, str]:
        if self._usage_config_error:
            raise LLMUsageError(self._usage_config_error)
        if self.usage_ledger is None or self.provider is None:
            raise LLMUsageError("LLM usage ledger is not configured")
        model = self.resolve_model()
        call_id = self.usage_ledger.begin_call(self.provider, model, operation)
        self.calls += 1
        return call_id, model

    def _usage(self, resp, operation: str = "unspecified", *, call_id: int | None = None, model: str | None = None) -> None:
        if call_id is None:
            call_id, model = self._begin_usage(operation)
        values = self._normalise_usage(resp)
        assert self.usage_ledger is not None
        self.usage_ledger.complete_call(
            call_id,
            provider=str(self.provider),
            model=model or self.resolve_model(),
            usage_reported=values["usage_reported"],
            input_tokens=values["input_tokens"],
            output_tokens=values["output_tokens"],
            cache_read_tokens=values["cache_read_tokens"],
            cache_write_tokens=values["cache_write_tokens"],
            total_tokens=values["total_tokens"],
            provider_request_id=self._usage_value(resp, "id", None),
        )
        self.tokens_in += values["total_input_tokens"]
        self.tokens_out += values["output_tokens"]
        self.cache_tokens += values["cache_read_tokens"] + values["cache_write_tokens"]
        self._metering_error = None

    def _fail_usage(self, call_id: int | None, error: Exception) -> None:
        if call_id is None or self.usage_ledger is None:
            return
        try:
            self.usage_ledger.fail_call(call_id, error)
        except LLMUsageError as persist_error:
            self._metering_error = str(persist_error)
            log.error("could not finalize failed LLM usage reservation: %s", persist_error)

    def usage_payload(self) -> dict:
        if self._usage_config_error:
            return unavailable_payload(self._usage_config_error, blocked=True)
        if self.usage_ledger is None:
            return unavailable_payload("ledger_not_configured", blocked=True)
        try:
            return self.usage_ledger.payload(self.provider, self.model)
        except LLMUsageError as exc:
            self._metering_error = str(exc)
            totals = {"calls": self.calls, "input_tokens": self.tokens_in, "output_tokens": self.tokens_out,
                      "cache_tokens": self.cache_tokens, "total_tokens": self.tokens_in + self.tokens_out}
            return unavailable_payload(str(exc), blocked=True, totals=totals)

    # ---- Anthropic calls --------------------------------------------------------------------------------------
    def _anthropic_create(self, **kw):
        try:
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kw)
        except TypeError:
            return self.client.beta.messages.create(**kw)

    def _anthropic_text(self, resp) -> str:
        if resp.stop_reason == "refusal":
            return ""
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    # ---- OpenAI calls ------------------------------------------------------------------------------------------
    def _openai_kwargs(self, system: str, user: str, schema: dict | None, effort: str, max_tokens: int, stream: bool = False) -> dict:
        kw = {"model": self.resolve_model(), "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_completion_tokens": max_tokens}
        if schema is not None:
            kw["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}
        if self._is_reasoning_model():
            kw["reasoning_effort"] = EFFORT_OPENAI.get(effort, "medium")
        if stream:
            kw["stream"] = True
            kw["stream_options"] = {"include_usage": True}
        return kw

    def _openai_create(self, **kw):
        import openai
        try:
            return self.client.chat.completions.create(**kw)
        except openai.BadRequestError as e:
            msg = str(e)
            if "reasoning_effort" in msg and "reasoning_effort" in kw:        # model without reasoning controls
                kw.pop("reasoning_effort")
                return self.client.chat.completions.create(**kw)
            if "max_completion_tokens" in msg and "max_completion_tokens" in kw:
                kw["max_tokens"] = kw.pop("max_completion_tokens")
                return self.client.chat.completions.create(**kw)
            raise

    # ---- shared entry points ---------------------------------------------------------------------------------
    def _json_call(self, system: str, user: str, schema: dict, effort: str, max_tokens: int, operation: str = "classify") -> str:
        call_id = None
        with self._call_lock:
            try:
                call_id, model = self._begin_usage(operation)
                if self.provider == "anthropic":
                    resp = self._anthropic_create(model=model, max_tokens=max_tokens, system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                                                  messages=[{"role": "user", "content": user}], output_config={"format": {"type": "json_schema", "schema": schema}, "effort": effort})
                    self._usage(resp, operation, call_id=call_id, model=model)
                    if resp.stop_reason == "refusal":
                        log.warning("%s refused a classification batch (%s)", self.provider, getattr(getattr(resp, "stop_details", None), "category", None))
                    return self._anthropic_text(resp)
                resp = self._openai_create(**self._openai_kwargs(system, user, schema, effort, max_tokens))
                self._usage(resp, operation, call_id=call_id, model=model)
                choice = resp.choices[0]
                if choice.finish_reason == "content_filter":
                    log.warning("OpenAI content filter on a classification batch")
                return choice.message.content or ""
            except Exception as exc:
                self._fail_usage(call_id, exc)
                raise

    def ping(self) -> bool:
        """Cheap end-to-end check of the credential and model."""
        return bool(self.complete("Reply with the single word OK.", "ping", effort="low", max_tokens=50, operation="ping").strip())

    def classify(self, events: list[dict]) -> dict[str, dict]:
        """events: stored event dicts (id, title, summary, source_name, published, tickers, event_type). Returns {id: fields}."""
        if not events or not self.available or self.index is None:
            return {}
        payload = [{"id": e["id"], "source": e.get("source_name"), "type": e.get("source_type"), "published": str(e.get("published"))[:16],
                    "title": e["title"], "summary": (e.get("summary") or "")[:400],
                    "rule_hint": {"tickers": list(e.get("tickers") or []), "event_type": e.get("event_type")}} for e in events]
        system = CLASSIFY_SYSTEM.format(universe=self.index.describe_universe())
        user = "Classify these items:\n" + json.dumps(payload, ensure_ascii=False)
        try:
            text = self._json_call(system, user, BATCH_SCHEMA, self.settings.llm_effort, 16000)
        except Exception as e:
            self._handle_error(e, "classify")
            return {}
        if not text:
            return {}
        lineage = {"classifier": "llm", "provider": self.provider, "model": self.resolve_model(), "prompt_version": PROMPT_VERSION, "prompt_hash": _h(system),
                   "schema_version": SCHEMA_VERSION, "input_hash": _h(user), "output_hash": _h(text), "classified_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("%s classify: invalid JSON", self.provider)
            return {}
        out: dict[str, dict] = {}
        for it in data.get("items", []):
            try:
                out[it["id"]] = {**self._clean(it), "_lineage": lineage}
            except Exception as e:                    # pragma: no cover - defensive
                log.debug("skip malformed item: %s", e)
        return out

    def _clean(self, it: dict) -> dict:
        uni = self.index.companies if self.index else {}
        tick = [t.upper() for t in it.get("tickers", []) if t.upper() in uni]
        rt = [t.upper() for t in it.get("tickers_readthrough", []) if t.upper() in uni and t.upper() not in tick]
        return {"relevant": bool(it.get("relevant")), "event_type": it.get("event_type") if it.get("event_type") in EVENT_TYPES else "other",
                "tickers": tick, "tickers_rt": rt, "direction": int(max(-2, min(2, int(it.get("direction", 0))))),
                "materiality": float(max(0.0, min(1.0, float(it.get("materiality", 0))))), "confidence": float(max(0.0, min(1.0, float(it.get("confidence", 0.5))))),
                "horizon": it.get("horizon") if it.get("horizon") in HORIZONS else "days", "themes": [str(t)[:40] for t in it.get("themes", [])][:8],
                "pillars": [p for p in it.get("pillars", []) if p in PILLARS], "action": it.get("action") if it.get("action") in ACTIONS else "none",
                "one_liner": str(it.get("one_liner", ""))[:400]}

    def complete(self, system: str, user: str, effort: str = "medium", max_tokens: int = 4000, operation: str = "complete") -> str:
        if not self.available:
            return ""
        call_id = None
        try:
            with self._call_lock:
                call_id, model = self._begin_usage(operation)
                if self.provider == "anthropic":
                    resp = self._anthropic_create(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}], output_config={"effort": effort})
                    self._usage(resp, operation, call_id=call_id, model=model)
                    return self._anthropic_text(resp) or ("(The model declined to answer this request.)" if resp.stop_reason == "refusal" else "")
                resp = self._openai_create(**self._openai_kwargs(system, user, None, effort, max_tokens))
                self._usage(resp, operation, call_id=call_id, model=model)
                return resp.choices[0].message.content or ""
        except Exception as e:
            self._fail_usage(call_id, e)
            self._handle_error(e, "complete")
            return ""

    def stream(self, system: str, user: str, effort: str = "medium", max_tokens: int = 4000, operation: str = "stream") -> Iterator[str]:
        if not self.available:
            return iter(())

        def gen_anthropic():
            call_id = None
            with self._call_lock:
                try:
                    call_id, model = self._begin_usage(operation)
                    kw = dict(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}], output_config={"effort": effort})
                    try:
                        cm = self.client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **kw)
                    except TypeError:
                        cm = self.client.beta.messages.stream(**kw)
                    with cm as s:
                        for text in s.text_stream:
                            yield text
                        self._usage(s.get_final_message(), operation, call_id=call_id, model=model)
                except Exception as e:
                    self._fail_usage(call_id, e)
                    self._handle_error(e, "stream")
                    if not isinstance(e, LLMUsageError):
                        yield f"\n\n(LLM call failed: {e})"

        def gen_openai():
            call_id = None
            with self._call_lock:
                try:
                    call_id, model = self._begin_usage(operation)
                    resp = self._openai_create(**self._openai_kwargs(system, user, None, effort, max_tokens, stream=True))
                    usage_chunk = None
                    for chunk in resp:
                        if getattr(chunk, "usage", None):
                            usage_chunk = chunk
                        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content
                    self._usage(usage_chunk, operation, call_id=call_id, model=model)
                except Exception as e:
                    self._fail_usage(call_id, e)
                    self._handle_error(e, "stream")
                    if not isinstance(e, LLMUsageError):
                        yield f"\n\n(LLM call failed: {e})"

        return gen_anthropic() if self.provider == "anthropic" else gen_openai()
