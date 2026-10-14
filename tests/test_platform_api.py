from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import time

from fastapi.testclient import TestClient
import pytest

from market_intel.entities import Company, EntityIndex
from market_intel.platform.checkpoint import (
    FileLedgerCheckpointStore,
    HmacSha256Signer,
    LedgerCheckpointError,
)
from market_intel.platform.core import DatasetRequest, Finality, canonical_json
from market_intel.platform.paper import PaperError, Quote
from market_intel.platform.service import (
    EXECUTION_QUOTE_DATASET,
    EXECUTION_QUOTE_FIELDS,
    EXECUTION_QUOTE_SCHEMA_VERSION,
    ExecutionQuote,
    PlatformBlocked,
    PlatformService,
)
from market_intel.server import create_app
from market_intel.store import Store


class FakePlatformService:
    def __init__(self):
        self.calls = []
        self.committed_operations = {}

    def _durable_result(self, action, body, result):
        """Small receipt stand-in; the production service persists this in JSONL."""

        operation_id = body["_operation_id"]
        request_sha256 = body["_request_sha256"]
        existing = self.committed_operations.get(operation_id)
        if existing is not None:
            if (
                existing["action"] != action
                or existing["request_sha256"] != request_sha256
            ):
                raise PlatformBlocked(
                    "operation id was reused with a different action or payload"
                )
            return copy.deepcopy(existing["result"])
        self.committed_operations[operation_id] = {
            "action": action,
            "request_sha256": request_sha256,
            "result": copy.deepcopy(result),
        }
        return result

    def overview(self):
        return {"status": "NOT VALIDATED/PAPER", "live_trading_supported": False}

    def strategies(self):
        return {"status": "NOT VALIDATED/PAPER", "items": []}

    def runs(self, strategy_id=None, limit=100):
        self.calls.append(("runs", strategy_id, limit))
        return {"status": "NOT VALIDATED/PAPER", "items": []}

    def paper(self, session_id=None):
        self.calls.append(("paper", session_id))
        return {"status": "NOT VALIDATED/PAPER", "session_id": session_id, "live_trading_supported": False}

    def risk(self):
        return {"status": "NOT VALIDATED/PAPER", "risk": {"available": False}}

    def create_paper_session(self, body):
        self.calls.append(("create", body))
        return self._durable_result("paper.session.create", body, {
            "status": "NOT VALIDATED/PAPER",
            "live_trading_supported": False,
            "session": {"session_id": "paper_api"},
        })

    def step_paper(self, session_id, body):
        self.calls.append(("step", session_id, body))
        return self._durable_result("paper.step", body, {
            "status": "NOT VALIDATED/PAPER",
            "run_id": "run-001",
            "orders": [{"order_id": "o1"}],
            "fills": [{"fill_id": "f1"}],
        })

    def kill_switch(self, session_id, body):
        self.calls.append(("kill", session_id, body))
        return self._durable_result(
            "paper.kill_switch",
            body,
            {
                "status": "NOT VALIDATED/PAPER",
                "session": {"kill_switch_active": True},
            },
        )


def client(tmp_path, service, *, remote=False):
    idx = EntityIndex([
        Company(ticker="MU", name="Micron", segment="Memory", aliases=[], weight=0.1, cik=723125)
    ])
    result = TestClient(
        create_app(store=Store(tmp_path / "market.sqlite"), index=idx, platform_service=service),
        base_url="http://127.0.0.1",
        client=(("203.0.113.9", 50000) if remote else ("127.0.0.1", 50000)),
    )
    result.headers["Idempotency-Key"] = "test-operation-key"
    return result


def test_platform_read_and_paper_mutation_routes_use_injected_service_and_audit(tmp_path):
    from market_intel import identity

    service = FakePlatformService()
    c = client(tmp_path, service)
    assert c.get("/api/v2/platform/overview").json()["live_trading_supported"] is False
    assert c.get("/api/v2/platform/strategies").status_code == 200
    assert c.get("/api/v2/platform/runs?strategy_id=stock_select&limit=7").status_code == 200
    assert c.get("/api/v2/platform/paper?session_id=paper_api").status_code == 200
    assert c.get("/api/v2/platform/risk").status_code == 200

    made = c.post("/api/v2/platform/paper/sessions", json={"starting_cash": "250000"})
    stepped = c.post("/api/v2/platform/paper/paper_api/step", json={"run_id": "run-001"})
    killed = c.post("/api/v2/platform/paper/paper_api/kill-switch", json={"reason": "drill"})
    assert made.status_code == stepped.status_code == killed.status_code == 200
    assert ("runs", "stock_select", 7) in service.calls
    assert ("paper", "paper_api") in service.calls
    create_body = next(call[1] for call in service.calls if call[0] == "create")
    step_body = next(call[2] for call in service.calls if call[0] == "step")
    kill_body = next(call[2] for call in service.calls if call[0] == "kill")
    assert create_body["starting_cash"] == "250000"
    assert step_body["run_id"] == "run-001"
    assert kill_body["reason"] == "drill"
    for body in (create_body, step_body, kill_body):
        assert body["_operation_id"].startswith("op_")
        assert len(body["_request_sha256"]) == 64
    actions = {event["action"] for event in identity.audit_log(object_type="paper_session")}
    assert actions == {
        "platform.paper.session.create.requested", "platform.paper.session.create",
        "platform.paper.step.requested", "platform.paper.step",
        "platform.paper.kill_switch.requested", "platform.paper.kill_switch",
    }
    assert identity.verify_chain()["ok"] is True


def test_viewer_platform_gets_do_not_change_repository_or_audit_heads(tmp_path):
    from market_intel import identity

    generated = "2026-09-03T14:00:00+00:00"
    snapshot = {
        "run_id": "run-read-only",
        "portfolio_id": "portfolio-read-only",
        "date": "2026-09-03",
        "asof_session": "2026-09-03",
        "generated_at": generated,
        "top": ["AAA"],
        "composite": {"AAA": 1.0},
        "validation_status": "NOT VALIDATED · fixture",
    }
    run = {
        "run_id": "run-read-only",
        "portfolio_id": "portfolio-read-only",
        "asof_session": "2026-09-03",
        "generated_at": generated,
        "status": "pending",
        "code_hash": "readonly",
        "validation_status": "NOT VALIDATED · fixture",
        "actionable": False,
        "not_actionable_reasons": ["read-only fixture"],
        "portfolio": {
            "construction": "risk_parity",
            "cash": 0.0,
            "invested": 1.0,
            "reconciles": True,
            "positions": [{"ticker": "AAA", "target_executable_weight": 1.0}],
        },
    }
    state_root = tmp_path / "platform-state"
    service = PlatformService(
        latest_dir=tmp_path / "unused",
        state_dir=state_root,
        snapshot_loader=lambda: snapshot,
        run_loader=lambda: run,
        clock=lambda: datetime(2026, 9, 3, 14, 1, tzinfo=timezone.utc),
        require_versioned_lineage=False,
    )
    service.sync_current_run()
    _, viewer_token = identity.create_account("readonly-viewer", "Viewer", "viewer")
    c = client(tmp_path, service, remote=True)
    headers = {"Authorization": f"Bearer {viewer_token}"}
    repository_before = service.repository.summary()
    run_before = service.repository.get_run("run-read-only")
    catalog_before = service.catalog.summary()
    audit_before = identity.verify_chain()
    physical_before = {
        str(path.relative_to(state_root)): (
            path.read_bytes(),
            path.stat().st_ino,
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in state_root.rglob("*")
        if path.is_file()
    }
    tree_before = sorted(str(path.relative_to(state_root)) for path in state_root.rglob("*"))
    sidecars_before = sorted(
        name for name in tree_before if name.endswith(("-wal", "-shm", "-journal"))
    )
    root_mtime_before = state_root.stat().st_mtime_ns

    for path in (
        "/api/v2/platform/overview",
        "/api/v2/platform/strategies",
        "/api/v2/platform/runs",
        "/api/v2/platform/risk",
        "/api/v2/platform/paper",
    ):
        assert c.get(path, headers=headers).status_code == 200

    run_after = service.repository.get_run("run-read-only")
    assert service.repository.summary() == repository_before
    assert service.catalog.summary() == catalog_before
    assert run_after == run_before
    assert run_after["event_count"] == run_before["event_count"]
    assert run_after["event_head_hash"] == run_before["event_head_hash"]
    assert identity.verify_chain() == audit_before
    physical_after = {
        str(path.relative_to(state_root)): (
            path.read_bytes(),
            path.stat().st_ino,
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in state_root.rglob("*")
        if path.is_file()
    }
    tree_after = sorted(str(path.relative_to(state_root)) for path in state_root.rglob("*"))
    sidecars_after = sorted(
        name for name in tree_after if name.endswith(("-wal", "-shm", "-journal"))
    )
    assert physical_after == physical_before
    assert tree_after == tree_before
    assert sidecars_after == sidecars_before == []
    assert state_root.stat().st_mtime_ns == root_mtime_before


def test_default_platform_service_wires_the_stock_select_risk_adapter(tmp_path, monkeypatch):
    import market_intel.config as config_module
    import market_intel.platform.service as service_module
    from market_intel import risk_workbench
    from market_intel.platform.risk_adapter import StockSelectRiskLoader

    seen = []

    class Constructed(FakePlatformService):
        def __init__(self, *, risk_loader=None, catalog=None, checkpoint_store=None, **_kwargs):
            super().__init__()
            seen.append((risk_loader, catalog, checkpoint_store))

    monkeypatch.setattr(config_module, "DATA_DIR", tmp_path / "market-data")
    monkeypatch.setattr(service_module, "PlatformService", Constructed)
    idx = EntityIndex([
        Company(ticker="MU", name="Micron", segment="Memory", aliases=[], weight=0.1, cik=723125)
    ])
    with TestClient(
        create_app(store=Store(tmp_path / "default.sqlite"), index=idx),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as c:
        assert c.get("/api/v2/platform/risk").status_code == 200
    assert len(seen) == 1
    loader, catalog, _checkpoint_store = seen[0]
    assert isinstance(loader, StockSelectRiskLoader)
    assert loader._frozen_builder is risk_workbench.build_frozen_construction
    assert loader._dataset_catalog is catalog


def test_platform_paper_mutations_require_operate_role(tmp_path):
    from market_intel import identity

    service = FakePlatformService()
    _, viewer_token = identity.create_account("platform-viewer", "Viewer", "viewer")
    c = client(tmp_path, service, remote=True)
    headers = {"Authorization": f"Bearer {viewer_token}"}
    assert c.get("/api/v2/platform/overview", headers=headers).status_code == 200
    for path in (
        "/api/v2/platform/paper/sessions",
        "/api/v2/platform/paper/paper_api/step",
        "/api/v2/platform/paper/paper_api/kill-switch",
    ):
        response = c.post(path, headers=headers, json={"reason": "x"})
        assert response.status_code == 403
    assert not any(call[0] in {"create", "step", "kill"} for call in service.calls)


def test_platform_blocked_step_records_intent_and_rejection_without_success_claim(tmp_path):
    from market_intel import identity

    class Blocked(FakePlatformService):
        def step_paper(self, session_id, body):
            raise PlatformBlocked("paper step blocked: no confirmed quote")

    c = client(tmp_path, Blocked())
    response = c.post("/api/v2/platform/paper/paper_api/step", json={})
    assert response.status_code == 409
    assert "no confirmed quote" in response.json()["detail"]
    actions = {
        event["action"] for event in identity.audit_log(object_type="paper_session")
    }
    assert actions == {
        "platform.paper.step.requested", "platform.paper.step.rejected"
    }
    assert "platform.paper.step" not in actions


@pytest.mark.parametrize(
    "checkpoint_error",
    (LedgerCheckpointError, PaperError, RuntimeError),
)
def test_http_reports_committed_checkpoint_pending_without_false_rejection(
    tmp_path, checkpoint_error
):
    """A real service commit is HTTP 202/pending, then the same key repairs it."""

    from market_intel import identity

    generated = "2026-09-03T14:00:00+00:00"
    snapshot = {
        "run_id": "run-http-pending",
        "portfolio_id": "portfolio-http-pending",
        "date": "2026-09-03",
        "asof_session": "2026-09-03",
        "generated_at": generated,
        "top": ["AAA"],
        "composite": {"AAA": 1.0},
        "validation_status": "NOT VALIDATED · fixture",
    }
    run = {
        "run_id": "run-http-pending",
        "portfolio_id": "portfolio-http-pending",
        "asof_session": "2026-09-03",
        "generated_at": generated,
        "status": "pending",
        "code_hash": "http-pending",
        "validation_status": "NOT VALIDATED · fixture",
        "actionable": True,
        "not_actionable_reasons": [],
        "portfolio": {
            "construction": "risk_parity",
            "cash": 0.0,
            "invested": 1.0,
            "reconciles": True,
            "positions": [{"ticker": "AAA", "target_executable_weight": 1.0}],
        },
    }

    witness = FileLedgerCheckpointStore(
        tmp_path / "http-witness",
        HmacSha256Signer("http-witness-v1", b"h" * 32),
    )

    class FailOnceAfterGenesis:
        def __init__(self, delegate):
            self.delegate = delegate
            self.failed = False

        def latest(self, session_id):
            return self.delegate.latest(session_id)

        def verify_head(self, session_id, sequence, event_hash):
            return self.delegate.verify_head(session_id, sequence, event_hash)

        def publish(self, **kwargs):
            if kwargs["sequence"] > 1 and not self.failed:
                self.failed = True
                raise checkpoint_error("injected HTTP witness outage")
            return self.delegate.publish(**kwargs)

    now = [datetime(2026, 9, 3, 14, 1, tzinfo=timezone.utc)]
    service = None

    def quotes(symbols, after, available_at):
        observed = available_at - timedelta(seconds=2)
        acquired = available_at - timedelta(seconds=1)
        values = {}
        for symbol in symbols:
            instrument_id = f"equity:US:{symbol}"
            quote = Quote(symbol, observed, Decimal("100"), Decimal("100"))
            artifact = {
                "ask": "100",
                "available_quantity": None,
                "bid": "100",
                "currency": "USD",
                "instrument_id": instrument_id,
                "observed_at": observed.isoformat(),
                "schema_version": EXECUTION_QUOTE_SCHEMA_VERSION,
                "source": "fixture.http",
                "symbol": symbol,
            }
            request = DatasetRequest(
                provider="fixture.http",
                entitlement="fixture-entitlement",
                dataset=EXECUTION_QUOTE_DATASET,
                instrument_ids=(instrument_id,),
                fields=EXECUTION_QUOTE_FIELDS,
                frequency="event",
                adjustment="raw",
                start=observed.date(),
                end=observed.date(),
                as_of=acquired,
                schema_version=EXECUTION_QUOTE_SCHEMA_VERSION,
                parameters={"currency": "USD", "symbol": symbol},
            )
            version = service.catalog.register(
                request,
                canonical_json(artifact).encode("utf-8"),
                effective_at=observed,
                observed_at=observed,
                acquired_at=acquired,
                finality=Finality.CONFIRMED.value,
            )
            values[symbol] = ExecutionQuote(
                quote=quote,
                observed_at=observed,
                acquired_at=acquired,
                finality=Finality.CONFIRMED,
                source="fixture.http",
                content_hash=version.content_sha256,
                instrument_id=instrument_id,
                dataset_version_id=version.version_id,
                request_hash=request.key,
            )
        return values

    service = PlatformService(
        latest_dir=tmp_path / "unused",
        state_dir=tmp_path / "http-platform-state",
        snapshot_loader=lambda: snapshot,
        run_loader=lambda: run,
        quote_loader=quotes,
        checkpoint_store=FailOnceAfterGenesis(witness),
        clock=lambda: now[0],
        require_versioned_lineage=False,
    )
    service.sync_current_run()
    c = client(tmp_path, service)
    created = c.post(
        "/api/v2/platform/paper/sessions", json={"starting_cash": "1000"}
    )
    assert created.status_code == 200
    sid = created.json()["session"]["session_id"]
    now[0] += timedelta(minutes=1)

    pending = c.post(f"/api/v2/platform/paper/{sid}/step", json={})
    assert pending.status_code == 202
    assert pending.json()["mutation_state"] == "committed_checkpoint_pending"
    assert pending.json()["external_checkpoint"]["error_type"] == checkpoint_error.__name__
    assert pending.json()["audit"]["status"] == "COMMITTED_CHECKPOINT_PENDING"
    assert len(service.paper(sid)["session"]["orders"]) == 1
    step_actions = [
        event["action"]
        for event in reversed(identity.audit_log(object_type="paper_session"))
        if event["action"].startswith("platform.paper.step")
    ]
    assert step_actions == [
        "platform.paper.step.requested",
        "platform.paper.step.committed_checkpoint_pending",
    ]
    assert "platform.paper.step.rejected" not in step_actions

    recovered = c.post(f"/api/v2/platform/paper/{sid}/step", json={})
    assert recovered.status_code == 200
    assert recovered.json()["mutation_state"] == "committed"
    assert len(service.paper(sid)["session"]["orders"]) == 1
    witness.verify_head(
        sid,
        service.paper(sid)["session"]["ledger_head"]["sequence"],
        service.paper(sid)["session"]["ledger_head"]["event_hash"],
    )

    # A completed HTTP response must not become a stale in-memory authority.
    # Losing a non-genesis witness is discovered on the same-key retry and can
    # never be rewritten as a new genesis checkpoint.
    checkpoint_path = tmp_path / "http-witness" / f"{sid}.checkpoints.jsonl"
    checkpoint_path.unlink()
    missing = c.post(f"/api/v2/platform/paper/{sid}/step", json={})
    assert missing.status_code == 202
    assert missing.json()["mutation_state"] == "committed_checkpoint_pending"
    assert "refusing to reinitialize a non-genesis head" in missing.json()[
        "external_checkpoint"
    ]["reason"]
    assert not checkpoint_path.exists()
    assert len(service.paper(sid)["session"]["orders"]) == 1


def test_malformed_json_cannot_fall_back_to_a_default_paper_mutation(tmp_path):
    service = FakePlatformService()
    c = client(tmp_path, service)
    response = c.post(
        "/api/v2/platform/paper/sessions",
        content=b"{bad-json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert not any(call[0] == "create" for call in service.calls)


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://attacker.example"},
        {"origin": "null"},
        {"origin": "http://127.0.0.1/path"},
        {"referer": "https://attacker.example/platform"},
        {"referer": "not-a-url"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},
        {"sec-fetch-site": "none"},
        {
            "origin": "http://127.0.0.1",
            "referer": "https://attacker.example/platform",
            "sec-fetch-site": "same-origin",
        },
    ],
)
def test_platform_browser_mutations_fail_closed_on_each_cross_origin_signal(
    tmp_path, headers
):
    service = FakePlatformService()
    c = client(tmp_path, service)
    response = c.post(
        "/api/v2/platform/paper/sessions",
        json={"starting_cash": "100"},
        headers=headers,
    )
    assert response.status_code == 403
    assert "cross-origin" in response.json()["detail"]
    assert not any(call[0] == "create" for call in service.calls)


def test_platform_browser_mutation_accepts_only_when_all_present_signals_are_same_origin(
    tmp_path,
):
    service = FakePlatformService()
    c = client(tmp_path, service)
    response = c.post(
        "/api/v2/platform/paper/sessions",
        content=b'{"starting_cash":"100"}',
        headers={
            "content-type": "application/json; charset=utf-8",
            "origin": "http://127.0.0.1",
            "referer": "http://127.0.0.1/platform?panel=paper",
            "sec-fetch-site": "same-origin",
        },
    )
    assert response.status_code == 200
    body = next(call[1] for call in service.calls if call[0] == "create")
    assert body["starting_cash"] == "100"


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", "application/x-www-form-urlencoded", "application/merge-patch+json", ""],
)
def test_body_bearing_platform_post_requires_application_json(
    tmp_path, content_type
):
    service = FakePlatformService()
    c = client(tmp_path, service)
    response = c.post(
        "/api/v2/platform/paper/sessions",
        content=b'{"starting_cash":"100"}',
        headers={"content-type": content_type},
    )
    assert response.status_code == 415
    assert "application/json" in response.json()["detail"]
    assert not any(call[0] == "create" for call in service.calls)


def test_originless_local_api_client_and_empty_post_body_remain_supported(tmp_path):
    service = FakePlatformService()
    c = client(tmp_path, service)
    response = c.post("/api/v2/platform/paper/sessions")
    assert response.status_code == 200
    body = next(call[1] for call in service.calls if call[0] == "create")
    assert set(body) == {"_operation_id", "_request_sha256"}


def test_platform_mutation_rejects_missing_idempotency_key_before_service_call(tmp_path):
    service = FakePlatformService()
    c = client(tmp_path, service)
    response = c.post(
        "/api/v2/platform/paper/sessions",
        json={"starting_cash": "100"},
        headers={"Idempotency-Key": ""},
    )
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]
    assert not any(call[0] == "create" for call in service.calls)


def test_platform_retry_reenters_durable_service_and_overwrites_reserved_fields(tmp_path):
    from market_intel import identity

    service = FakePlatformService()
    c = client(tmp_path, service)
    headers = {"Idempotency-Key": "one-logical-operation"}
    request_body = {
        "starting_cash": "100",
        "_operation_id": "client-forged",
        "_request_sha256": "client-forged",
    }
    first = c.post("/api/v2/platform/paper/sessions", json=request_body, headers=headers)
    replay = c.post("/api/v2/platform/paper/sessions", json=request_body, headers=headers)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    create_calls = [call for call in service.calls if call[0] == "create"]
    assert len(create_calls) == 2
    assert len(service.committed_operations) == 1
    service_body = create_calls[0][1]
    assert service_body["_operation_id"].startswith("op_")
    assert service_body["_operation_id"] != "client-forged"
    assert len(service_body["_request_sha256"]) == 64
    assert service_body["_request_sha256"] != "client-forged"
    assert create_calls[1][1]["_operation_id"] == service_body["_operation_id"]
    assert create_calls[1][1]["_request_sha256"] == service_body["_request_sha256"]
    actions = [
        event["action"]
        for event in reversed(identity.audit_log(object_type="paper_session"))
    ]
    assert len(actions) == 4
    assert set(actions) == {
        "platform.paper.session.create.requested",
        "platform.paper.session.create",
    }


def test_platform_idempotency_key_reuse_with_different_body_is_a_conflict(tmp_path):
    service = FakePlatformService()
    c = client(tmp_path, service)
    headers = {"Idempotency-Key": "body-conflict-operation"}
    first = c.post(
        "/api/v2/platform/paper/sessions",
        json={"starting_cash": "100"},
        headers=headers,
    )
    conflict = c.post(
        "/api/v2/platform/paper/sessions",
        json={"starting_cash": "200"},
        headers=headers,
    )
    assert first.status_code == 200
    assert conflict.status_code == 409
    assert "different action or payload" in conflict.json()["detail"]
    assert len([call for call in service.calls if call[0] == "create"]) == 2
    assert len(service.committed_operations) == 1


def test_concurrent_platform_retries_use_one_durable_operation(tmp_path):
    class SlowCreate(FakePlatformService):
        def create_paper_session(self, body):
            time.sleep(0.03)
            return super().create_paper_session(body)

    service = SlowCreate()
    c = client(tmp_path, service)
    headers = {"Idempotency-Key": "concurrent-logical-operation"}

    def post(_):
        return c.post(
            "/api/v2/platform/paper/sessions",
            json={"starting_cash": "100"},
            headers=headers,
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(post, range(12)))
    assert {response.status_code for response in responses} == {200}
    assert len({response.text for response in responses}) == 1
    assert len([call for call in service.calls if call[0] == "create"]) == 12
    assert len(service.committed_operations) == 1


def test_successful_retry_refreshes_dynamic_checkpoint_state(tmp_path):
    class DynamicCheckpoint(FakePlatformService):
        synchronized = True

        def step_paper(self, session_id, body):
            result = super().step_paper(session_id, body)
            result["mutation_state"] = (
                "committed"
                if self.synchronized
                else "committed_checkpoint_pending"
            )
            result["external_checkpoint"] = {
                "configured": True,
                "synchronized": self.synchronized,
                "reason": None if self.synchronized else "external checkpoint missing",
            }
            return result

    service = DynamicCheckpoint()
    c = client(tmp_path, service)
    headers = {"Idempotency-Key": "refresh-checkpoint-state"}

    first = c.post(
        "/api/v2/platform/paper/paper_api/step", json={}, headers=headers
    )
    service.synchronized = False
    replay = c.post(
        "/api/v2/platform/paper/paper_api/step", json={}, headers=headers
    )

    assert first.status_code == 200
    assert first.json()["external_checkpoint"]["synchronized"] is True
    assert replay.status_code == 202
    assert replay.json()["mutation_state"] == "committed_checkpoint_pending"
    assert replay.json()["external_checkpoint"]["synchronized"] is False
    assert len(service.committed_operations) == 1
    assert len([call for call in service.calls if call[0] == "step"]) == 2


def test_platform_routes_reject_unlisted_host_even_when_origin_matches_it(tmp_path):
    service = FakePlatformService()
    c = client(tmp_path, service)
    headers = {
        "host": "rebound.attacker.example",
        "origin": "http://rebound.attacker.example",
        "sec-fetch-site": "same-origin",
        "Idempotency-Key": "dns-rebinding-operation",
    }
    read = c.get("/api/v2/platform/overview", headers={"host": headers["host"]})
    mutation = c.post(
        "/api/v2/platform/paper/sessions",
        json={"starting_cash": "100"},
        headers=headers,
    )
    assert read.status_code == mutation.status_code == 400
    assert mutation.json()["error"] == "invalid Host header"
    assert not any(call[0] == "create" for call in service.calls)


def test_explicitly_allowlisted_platform_host_accepts_same_origin_mutation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MARKET_INTEL_ALLOWED_HOSTS", "research.example")
    service = FakePlatformService()
    c = client(tmp_path, service)
    response = c.post(
        "/api/v2/platform/paper/sessions",
        json={"starting_cash": "100"},
        headers={
            "host": "research.example",
            "origin": "http://research.example",
            "sec-fetch-site": "same-origin",
            "Idempotency-Key": "allowlisted-host-operation",
        },
    )
    assert response.status_code == 200
    assert len([call for call in service.calls if call[0] == "create"]) == 1


def test_llm_usage_and_status_reject_unlisted_host_on_loopback(tmp_path):
    c = client(tmp_path, FakePlatformService())

    for path in ("/api/llm/usage", "/api/status"):
        response = c.get(path, headers={"host": "rebound.attacker.example"})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid Host header"


def test_llm_usage_and_status_accept_explicitly_allowlisted_host(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_INTEL_ALLOWED_HOSTS", "research.example")
    c = client(tmp_path, FakePlatformService())

    for path in ("/api/llm/usage", "/api/status"):
        response = c.get(path, headers={"host": "research.example"})
        assert response.status_code == 200


def test_audit_intent_failure_prevents_platform_mutation(tmp_path, monkeypatch):
    from market_intel import identity

    service = FakePlatformService()
    c = client(tmp_path, service)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(identity, "audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        c.post("/api/v2/platform/paper/sessions", json={"starting_cash": "100"})
    assert not any(call[0] == "create" for call in service.calls)


def test_completion_audit_failure_is_repaired_by_same_operation_retry(
    tmp_path, monkeypatch
):
    from market_intel import identity

    service = FakePlatformService()
    c = client(tmp_path, service)
    real_audit = identity.audit
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("completion audit unavailable")
        return real_audit(*args, **kwargs)

    monkeypatch.setattr(identity, "audit", fail_second)
    response = c.post("/api/v2/platform/paper/sessions", json={"starting_cash": "100"})
    assert response.status_code == 200
    assert response.json()["audit"]["status"] == "INTENT_ONLY"
    retry = c.post("/api/v2/platform/paper/sessions", json={"starting_cash": "100"})
    assert retry.status_code == 200
    assert retry.json()["audit"]["status"] == "COMPLETE"
    body = next(call[1] for call in service.calls if call[0] == "create")
    assert body["starting_cash"] == "100"
    assert len(service.committed_operations) == 1
    actions = [
        event["action"]
        for event in reversed(identity.audit_log(object_type="paper_session"))
    ]
    assert actions == [
        "platform.paper.session.create.requested",
        "platform.paper.session.create.requested",
        "platform.paper.session.create",
    ]


def test_rebalance_preview_is_a_pure_calculation_using_the_server_clock(tmp_path, monkeypatch):
    from market_intel.platform import rebalance_preview
    service = FakePlatformService()
    seen = {}
    def preview(**kwargs):
        seen.update(kwargs)
        return {"status": "UNAVAILABLE", "execution_status": "PREVIEW_ONLY", "orders_created": False}
    monkeypatch.setattr(rebalance_preview, "build_preview", preview)
    app = client(tmp_path, service)
    before = datetime.now(timezone.utc)
    response = app.post("/api/v2/platform/rebalance/preview", json={"target": {"run_id": "declared-example"}})
    after = datetime.now(timezone.utc)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert before <= seen["now"] <= after
    assert seen["target"] == {"run_id": "declared-example"}
    assert seen["account"] is None and seen["quotes"] is None and seen["policy"] is None
    assert service.calls == [] and service.committed_operations == {}
    assert response.json()["orders_created"] is False


def test_rebalance_preview_rejects_client_clock_cross_origin_and_oversized_inputs(tmp_path):
    app = client(tmp_path, FakePlatformService())
    endpoint = "/api/v2/platform/rebalance/preview"
    assert app.post(endpoint, json={"now": "2026-09-22T13:31:00Z"}).status_code == 422
    assert app.post(endpoint, json={}, headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert app.post(endpoint, content=b'"' + b'x' * 262144 + b'"', headers={"Content-Type": "application/json"}).status_code == 413
    result = app.post(endpoint, json={})
    assert result.status_code == 200 and result.json()["status"] == "UNAVAILABLE"
    assert result.json()["actionability"] == "NOT ACTIONABLE"
    assert result.json()["sample_type"] == "unknown"
