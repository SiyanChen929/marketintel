from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from market_intel import config
from market_intel.ask import answer
from market_intel.classify.llm import LLM
from market_intel.entities import Company, EntityIndex
from market_intel.llm_usage import LLMBudgetExceeded, LLMUsageError, LLMUsageLedger
from market_intel.server import create_app
from market_intel.store import Store


def _anthropic_response(*, input_tokens=100, output_tokens=20, cache_read=30, cache_write=40, text="ok", request_id="req_1"):
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )
    return SimpleNamespace(
        id=request_id,
        usage=usage,
        stop_reason="end_turn",
        stop_details=None,
        content=[SimpleNamespace(type="text", text=text)],
    )


class _AnthropicMessages:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self.response


def _anthropic_llm(monkeypatch, ledger, response=None):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "UNIT_TEST_KEY_SENTINEL")
    monkeypatch.delenv("MARKET_INTEL_NO_LLM", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    messages = _AnthropicMessages(response or _anthropic_response())
    llm = LLM(config.Settings(model="claude-test"), provider="anthropic", usage_ledger=ledger)
    llm._client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return llm, messages


def _pricing():
    return {
        "anthropic": {
            "claude-test": {
                "input_per_million_usd": "1",
                "output_per_million_usd": "2",
                "cache_read_per_million_usd": "0.5",
                "cache_write_per_million_usd": "0.75",
            }
        }
    }


def test_fresh_sqlite_schema_initializes_with_one_calls_column_and_v1(tmp_path):
    path = tmp_path / "fresh.sqlite"
    assert not path.exists()
    ledger = LLMUsageLedger(path)
    assert ledger.payload("anthropic", "claude-test")["totals"]["calls"] == 0
    with sqlite3.connect(path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(llm_usage)")]
        assert columns.count("calls") == 1
        assert not {"prompt", "response", "api_key", "secret", "content", "message"}.intersection(columns)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert LLMUsageLedger(path).begin_call("anthropic", "claude-test", "ask") == 1


def test_schema_adopts_complete_v0_and_rejects_incomplete_or_future_layout(tmp_path):
    compatible = tmp_path / "compatible.sqlite"
    LLMUsageLedger(compatible).payload()
    with sqlite3.connect(compatible) as conn:
        conn.execute("PRAGMA user_version=0")
    LLMUsageLedger(compatible).payload()
    with sqlite3.connect(compatible) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    incomplete = tmp_path / "incomplete.sqlite"
    with sqlite3.connect(incomplete) as conn:
        conn.execute("CREATE TABLE llm_usage(id INTEGER PRIMARY KEY)")
    with pytest.raises(LLMUsageError, match="schema is incomplete"):
        LLMUsageLedger(incomplete)

    future = tmp_path / "future.sqlite"
    with sqlite3.connect(future) as conn:
        conn.execute("PRAGMA user_version=2")
    with pytest.raises(LLMUsageError, match="newer than supported"):
        LLMUsageLedger(future)


def test_concurrent_budget_reservation_is_atomic_across_ledger_instances(tmp_path):
    path = tmp_path / "usage.sqlite"
    first = LLMUsageLedger(path, token_budget=1_000)
    second = LLMUsageLedger(path, token_budget=1_000)
    first.payload("anthropic", "claude-test")  # initialize before the race
    barrier = threading.Barrier(2)

    def reserve(ledger):
        barrier.wait()
        try:
            return ledger.begin_call("anthropic", "claude-test", "ask")
        except LLMBudgetExceeded as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, (first, second)))
    assert sum(isinstance(result, int) for result in results) == 1
    assert sum("usage unavailable" in result for result in results if isinstance(result, str)) == 1
    assert first.payload("anthropic", "claude-test")["totals"]["calls"] == 1


def test_complete_call_rejects_reservation_provider_or_model_tampering(tmp_path):
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite", pricing=_pricing())
    provider_id = ledger.begin_call("anthropic", "claude-test", "ask")
    with pytest.raises(LLMUsageError, match="identity mismatch"):
        ledger.complete_call(provider_id, provider="openai", model="claude-test", usage_reported=True, input_tokens=1, output_tokens=0,
                             cache_read_tokens=0, cache_write_tokens=0, total_tokens=1)
    model_id = ledger.begin_call("anthropic", "claude-test", "brief")
    with pytest.raises(LLMUsageError, match="identity mismatch"):
        ledger.complete_call(model_id, provider="anthropic", model="other-model", usage_reported=True, input_tokens=1, output_tokens=0,
                             cache_read_tokens=0, cache_write_tokens=0, total_tokens=1)
    assert [row["status"] for row in ledger.payload("anthropic", "claude-test")["recent"][:2]] == ["pending", "pending"]

    # The original reservation remains bound to its original identity and can
    # only be finalized using that provider/model pair.
    ledger.complete_call(provider_id, provider="anthropic", model="claude-test", usage_reported=True, input_tokens=1, output_tokens=0,
                         cache_read_tokens=0, cache_write_tokens=0, total_tokens=1)
    assert ledger.payload("anthropic", "claude-test")["recent"][1]["cost"]["status"] == "available"


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens"])
@pytest.mark.parametrize("bad", [True, "1", 1.5, -1])
def test_complete_call_rejects_non_integer_or_negative_token_counts(tmp_path, field, bad):
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite")
    call_id = ledger.begin_call("anthropic", "claude-test", "ask")
    values = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": 0}
    values[field] = bad
    with pytest.raises(LLMUsageError, match="non-negative integer"):
        ledger.complete_call(call_id, provider="anthropic", model="claude-test", usage_reported=True, **values)
    assert ledger.payload("anthropic", "claude-test")["recent"][0]["status"] == "pending"


@pytest.mark.parametrize(
    "provider,counts,wrong_total,expected",
    [
        ("openai", {"input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 30, "cache_write_tokens": 0}, 140, 110),
        ("anthropic", {"input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 3, "cache_write_tokens": 4}, 110, 117),
    ],
)
def test_complete_call_enforces_provider_total_token_identity(tmp_path, provider, counts, wrong_total, expected):
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite")
    call_id = ledger.begin_call(provider, "model", "ask")
    with pytest.raises(LLMUsageError, match=f"expected {expected}"):
        ledger.complete_call(call_id, provider=provider, model="model", usage_reported=True, total_tokens=wrong_total, **counts)
    ledger.complete_call(call_id, provider=provider, model="model", usage_reported=True, total_tokens=expected, **counts)
    assert ledger.payload(provider, "model")["totals"]["total_tokens"] == expected


def test_usage_is_persisted_with_dimensions_cache_and_explicit_cost(monkeypatch, tmp_path):
    path = tmp_path / "usage.sqlite"
    ledger = LLMUsageLedger(path, pricing=_pricing())
    llm, messages = _anthropic_llm(monkeypatch, ledger, _anthropic_response(text="MODEL_RESPONSE_SENTINEL"))

    assert llm.complete("SYSTEM_PROMPT_SENTINEL", "USER_PROMPT_SENTINEL", operation="ask") == "MODEL_RESPONSE_SENTINEL"
    assert messages.calls == 1
    assert (llm.calls, llm.tokens_in, llm.tokens_out, llm.cache_tokens) == (1, 170, 20, 70)

    # A fresh instance reads the same durable ledger. No in-memory counter is
    # needed to reconstruct usage after a restart.
    reopened = LLMUsageLedger(path, pricing=_pricing())
    payload = reopened.payload("anthropic", "claude-test")
    assert payload["totals"] == {"calls": 1, "input_tokens": 100, "output_tokens": 20, "cache_tokens": 70, "total_tokens": 190}
    assert payload["cost"]["status"] == "available" and payload["cost"]["usd"] == "0.000185"
    row = payload["recent"][0]
    assert (row["provider"], row["model"], row["operation"]) == ("anthropic", "claude-test", "ask")
    assert row["occurred_at"].endswith("+00:00") and row["cost"]["pricing_sha256"]
    with sqlite3.connect(path) as conn:
        dump = "\n".join(conn.iterdump())
    for sentinel in ("SYSTEM_PROMPT_SENTINEL", "USER_PROMPT_SENTINEL", "MODEL_RESPONSE_SENTINEL", "UNIT_TEST_KEY_SENTINEL"):
        assert sentinel not in dump


def test_missing_pricing_never_fabricates_cost(tmp_path):
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite")
    call_id = ledger.begin_call("anthropic", "claude-test", "brief")
    result = ledger.complete_call(
        call_id,
        provider="anthropic",
        model="claude-test",
        usage_reported=True,
        input_tokens=10,
        output_tokens=2,
        cache_read_tokens=0,
        cache_write_tokens=0,
        total_tokens=12,
    )
    payload = ledger.payload("anthropic", "claude-test")
    assert result == {"cost_status": "unavailable", "cost_reason": "pricing_not_configured", "cost_usd": None}
    assert payload["cost"]["status"] == "unavailable" and payload["cost"]["usd"] is None
    assert payload["cost"]["known_usd"] == "0"


def test_openai_cached_tokens_are_not_double_counted(tmp_path):
    pricing = {"openai": {"gpt-test": {"input_per_million_usd": "2", "output_per_million_usd": "4", "cache_read_per_million_usd": "1"}}}
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite", pricing=pricing)
    call_id = ledger.begin_call("openai", "gpt-test", "classification")
    ledger.complete_call(
        call_id,
        provider="openai",
        model="gpt-test",
        usage_reported=True,
        input_tokens=100,
        output_tokens=10,
        cache_read_tokens=30,
        cache_write_tokens=0,
        total_tokens=110,
    )
    payload = ledger.payload("openai", "gpt-test")
    assert payload["totals"]["cache_tokens"] == 30
    assert payload["totals"]["total_tokens"] == 110
    assert payload["cost"]["usd"] == "0.00021"  # 70*2 + 30*1 + 10*4, all rates explicitly supplied


def test_token_budget_blocks_the_next_llm_call_but_rules_fallback_still_runs(monkeypatch, tmp_path):
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite", token_budget=190)
    llm, messages = _anthropic_llm(monkeypatch, ledger)
    assert llm.complete("system", "user", operation="brief") == "ok"
    assert llm.available is False
    assert llm.complete("system", "user", operation="brief") == ""
    assert messages.calls == 1
    assert ledger.payload("anthropic", "claude-test")["budget"]["reason"] == "token budget exhausted"

    index = EntityIndex([Company("MU", "Micron", "Memory", aliases=["Micron"])])
    text = answer("What happened to MU?", Store(tmp_path / "events.sqlite"), index, llm, stream=False)
    assert "LLM usage is blocked" in text and "Evidence" not in text
    assert messages.calls == 1


def test_cost_budget_without_model_pricing_fails_closed_before_provider_call(monkeypatch, tmp_path):
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite", cost_budget_usd="1")
    llm, messages = _anthropic_llm(monkeypatch, ledger)
    assert llm.available is False
    assert llm.complete("system", "user", operation="ask") == ""
    assert messages.calls == 0
    assert ledger.payload("anthropic", "claude-test")["budget"]["reason"] == "pricing unavailable for configured cost budget"


def test_cost_budget_with_incomplete_rate_card_fails_closed_before_provider_call(monkeypatch, tmp_path):
    partial = {"anthropic": {"claude-test": {"input_per_million_usd": "1", "output_per_million_usd": "2"}}}
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite", pricing=partial, cost_budget_usd="1")
    llm, messages = _anthropic_llm(monkeypatch, ledger)
    assert llm.available is False
    assert llm.complete("system", "user", operation="ask") == ""
    assert messages.calls == 0
    assert ledger.payload("anthropic", "claude-test")["budget"]["reason"].startswith("pricing incomplete")


def test_cost_budget_blocks_after_limit_and_pending_usage_is_fail_closed(tmp_path):
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite", pricing=_pricing(), cost_budget_usd="0.000185")
    call_id = ledger.begin_call("anthropic", "claude-test", "ask")
    ledger.complete_call(call_id, provider="anthropic", model="claude-test", usage_reported=True, input_tokens=100, output_tokens=20,
                         cache_read_tokens=30, cache_write_tokens=40, total_tokens=190)
    try:
        ledger.begin_call("anthropic", "claude-test", "ask")
    except LLMBudgetExceeded as exc:
        assert "cost budget exhausted" in str(exc)
    else:  # pragma: no cover - must fail closed
        raise AssertionError("cost budget allowed a subsequent request")

    pending = LLMUsageLedger(tmp_path / "pending.sqlite", token_budget=1_000)
    pending.begin_call("anthropic", "claude-test", "ask")
    try:
        pending.begin_call("anthropic", "claude-test", "ask")
    except LLMBudgetExceeded as exc:
        assert "usage unavailable" in str(exc)
    else:  # pragma: no cover - must fail closed
        raise AssertionError("unmetered pending request did not block the budget gate")


def test_budget_window_uses_persisted_utc_request_times(tmp_path):
    now = [datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)]
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite", token_budget=10, window_seconds=60, clock=lambda: now[0])
    call_id = ledger.begin_call("anthropic", "claude-test", "ask")
    ledger.complete_call(call_id, provider="anthropic", model="claude-test", usage_reported=True, input_tokens=8, output_tokens=2,
                         cache_read_tokens=0, cache_write_tokens=0, total_tokens=10)
    assert ledger.payload("anthropic", "claude-test")["budget"]["blocked"] is True

    now[0] += timedelta(seconds=61)
    next_id = ledger.begin_call("anthropic", "claude-test", "ask")
    assert isinstance(next_id, int)
    assert ledger.payload("anthropic", "claude-test")["totals"]["calls"] == 1  # the new pending call, old call is outside the window


def test_failed_provider_call_remains_visible_and_blocks_metered_followup(monkeypatch, tmp_path):
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite", token_budget=1_000)
    llm, messages = _anthropic_llm(monkeypatch, ledger)

    def fail(**kwargs):
        messages.calls += 1
        raise RuntimeError("provider unavailable")

    messages.create = fail
    assert llm.complete("system", "user", operation="brief") == ""
    payload = ledger.payload("anthropic", "claude-test")
    assert payload["totals"]["calls"] == 1 and payload["recent"][0]["status"] == "failed"
    assert payload["budget"]["blocked"] is True and "usage unavailable" in payload["budget"]["reason"]
    assert llm.available is False and messages.calls == 1


def test_read_only_usage_api_and_status_keep_legacy_counters(monkeypatch, tmp_path):
    ledger = LLMUsageLedger(tmp_path / "usage.sqlite", pricing=_pricing())
    llm, _ = _anthropic_llm(monkeypatch, ledger)
    assert llm.complete("system", "user", operation="ask") == "ok"
    index = EntityIndex([Company("MU", "Micron", "Memory", aliases=["Micron"])])
    client = TestClient(
        create_app(store=Store(tmp_path / "events.sqlite"), index=index, llm=llm),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )

    with sqlite3.connect(ledger.path) as conn:
        rows_before = conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
        version_before = conn.execute("PRAGMA user_version").fetchone()[0]
        schema_before = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    bytes_before = ledger.path.read_bytes()
    stat_before = ledger.path.stat()
    usage = client.get("/api/llm/usage")
    assert usage.status_code == 200
    body = usage.json()
    assert body["schema_version"] == "llm_usage.v1" and body["totals"]["calls"] == 1
    status = client.get("/api/status").json()
    assert status["llm"]["calls"] == 1 and status["llm"]["tokens_in"] == 170 and status["llm"]["cache_tokens"] == 70
    assert status["llm"]["usage"]["by_provider_model_operation"][0]["operation"] == "ask"
    assert client.get("/api/llm/usage").json()["totals"]["calls"] == 1
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == rows_before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == version_before
        assert conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == schema_before
    stat_after = ledger.path.stat()
    assert ledger.path.read_bytes() == bytes_before
    assert (stat_after.st_ino, stat_after.st_mtime_ns, stat_after.st_size) == (stat_before.st_ino, stat_before.st_mtime_ns, stat_before.st_size)


def test_payload_does_not_recreate_missing_or_uninitialized_storage(tmp_path):
    path = tmp_path / "usage.sqlite"
    ledger = LLMUsageLedger(path)
    path.unlink()
    with pytest.raises(LLMUsageError, match="database missing"):
        ledger.payload()
    assert not path.exists()

    empty = tmp_path / "empty.sqlite"
    empty.touch()
    ledger.path = empty
    before = (empty.read_bytes(), empty.stat().st_mtime_ns)
    with pytest.raises(LLMUsageError, match="schema"):
        ledger.payload()
    assert (empty.read_bytes(), empty.stat().st_mtime_ns) == before
