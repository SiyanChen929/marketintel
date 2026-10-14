"""P0-3 acceptance: server-derived actor, RBAC, maker-checker, tamper-evident audit, retention safety.

These encode the review's security acceptance criteria: an analyst cannot self-sign, forged actor fields are
ignored, and every mutation writes exactly one immutable audit event.
"""
from __future__ import annotations

import sqlite3

import pytest

from market_intel import config


@pytest.fixture
def ident(tmp_path, monkeypatch):
    """Isolated accounts + audit database."""
    from market_intel import identity
    monkeypatch.setattr(identity, "ACCOUNTS_PATH", tmp_path / "accounts.sqlite")
    monkeypatch.setattr(identity, "AUDIT_PATH", tmp_path / "audit.sqlite")
    return identity


@pytest.fixture
def client(tmp_path, monkeypatch, ident):
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.entities import Company, EntityIndex
    from market_intel.server import create_app
    from market_intel.store import Store
    monkeypatch.setenv("MARKET_INTEL_NO_LLM", "1")
    monkeypatch.setenv("MARKET_INTEL_ALLOWED_HOSTS", "research.example")
    monkeypatch.delenv("MARKET_INTEL_TOKEN", raising=False)
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "token")
    idx = EntityIndex([Company(ticker="MU", name="Micron", segment="Memory & Storage", aliases=[], weight=0.1, cik=723125)])
    # the audit chain lives in the business database, so both must be the same file
    db = tmp_path / "t.sqlite"
    monkeypatch.setattr(ident, "AUDIT_PATH", db)
    # a non-loopback client so the local-operator shortcut does not apply
    return TestClient(
        create_app(store=Store(db), index=idx),
        client=("203.0.113.9", 51000),
        headers={"host": "research.example"},
    )


# ----------------------------------------------------------------------------- roles
def test_role_capabilities(ident):
    an, _ = ident.create_account("ana", "Analyst", "analyst")
    pm, _ = ident.create_account("pm", "PM", "pm")
    vw, _ = ident.create_account("vw", "Viewer", "viewer")
    ops, _ = ident.create_account("ops", "Ops", "quantops")
    assert an.can_write and not an.can_approve and not an.can_operate
    assert pm.can_write and pm.can_approve
    assert not vw.can_write and not vw.can_approve
    assert ops.can_operate and not ops.can_approve
    with pytest.raises(ValueError):
        ident.create_account("x", "X", "not-a-role")


def test_token_maps_to_named_actor_and_revocation_works(ident):
    a, tok = ident.create_account("ana", "Analyst", "analyst")
    assert ident.actor_for_token(tok).username == "ana"
    assert ident.actor_for_token("wrong-token") is None
    assert ident.actor_for_token(None) is None
    ident.deactivate_account("ana")
    assert ident.actor_for_token(tok) is None          # session/device revocation


# ----------------------------------------------------------------------------- maker-checker
def test_analyst_cannot_self_sign_and_pm_cannot_sign_own_work(ident):
    an, _ = ident.create_account("ana", "Analyst", "analyst")
    pm, _ = ident.create_account("pm", "PM", "pm")
    with pytest.raises(ident.ApprovalError, match="requires one of"):
        ident.check_maker_checker(an, maker="ana")     # wrong role
    with pytest.raises(ident.ApprovalError, match="requires one of"):
        ident.check_maker_checker(an, maker="someone_else")
    with pytest.raises(ident.ApprovalError, match="second approver"):
        ident.check_maker_checker(pm, maker="pm")      # right role, but own work
    ident.check_maker_checker(pm, maker="ana")         # the only legitimate path


# ----------------------------------------------------------------------------- audit chain
def test_audit_is_append_only_and_tamper_evident(ident):
    a, _ = ident.create_account("ana", "Analyst", "analyst")
    for i in range(4):
        ident.audit(a, "case.update", "case", "7", before={"n": i}, after={"n": i + 1})
    assert ident.verify_chain()["ok"]
    assert ident.verify_chain()["n_events"] == 5          # 4 case events + the audited account creation
    log = ident.audit_log("case", "7")
    assert len(log) == 4 and log[0]["actor"] == "ana" and log[0]["after"] == {"n": 4}
    c = sqlite3.connect(str(ident.AUDIT_PATH))
    c.execute("UPDATE audit_events SET after='{\"n\":99}' WHERE seq=3")
    c.commit(); c.close()
    broken = ident.verify_chain()
    assert broken["ok"] is False and broken["broken_at_seq"] == 3      # the exact edited row is identified


def test_deleting_an_audit_row_breaks_the_chain(ident):
    a, _ = ident.create_account("ana", "Analyst", "analyst")
    for i in range(3):
        ident.audit(a, "case.create", "case", str(i))
    c = sqlite3.connect(str(ident.AUDIT_PATH))
    c.execute("DELETE FROM audit_events WHERE seq=2")
    c.commit(); c.close()
    assert ident.verify_chain()["ok"] is False


# ----------------------------------------------------------------------------- HTTP: forged identity is ignored
def test_forged_actor_fields_are_ignored_and_each_mutation_audits_once(client, ident):
    _, tok = ident.create_account("ana", "Analyst", "analyst")
    h = {"Authorization": f"Bearer {tok}"}
    who = client.get("/api/whoami", headers=h).json()
    assert who["actor"]["username"] == "ana" and who["actor"]["role"] == "analyst"
    before = len(ident.audit_log(limit=500))
    r = client.post("/api/cases", headers=h, json={"ticker": "MU", "title": "HBM contract print", "by": "ceo", "owner": "ceo"})
    assert r.status_code == 200
    case = r.json()["case"]
    # the creator is the session actor; the forged "by"/"owner" did not become the author
    assert r.json()["actor"]["username"] == "ana" and case["owner"] == "ana"
    log = ident.audit_log("case", str(case["id"]))
    assert len(log) == 1 and log[0]["actor"] == "ana" and log[0]["action"] == "case.create"
    r2 = client.post(f"/api/cases/{case['id']}", headers=h, json={"note": "checking", "by": "ceo", "signoff_by": "ceo"})
    assert r2.status_code == 403 and "second approver" in r2.json()["detail"] or r2.status_code == 403
    assert len(ident.audit_log(limit=500)) == before + 1        # the refused call wrote no audit event
    assert ident.verify_chain()["ok"]


def test_analyst_is_refused_signoff_over_http_but_pm_succeeds(client, ident):
    _, atok = ident.create_account("ana", "Analyst", "analyst")
    _, ptok = ident.create_account("pm", "PM", "pm")
    ah, ph = {"Authorization": f"Bearer {atok}"}, {"Authorization": f"Bearer {ptok}"}
    cid = client.post("/api/cases", headers=ah, json={"ticker": "MU", "title": "case"}).json()["case"]["id"]
    refused = client.post(f"/api/cases/{cid}", headers=ah, json={"status": "decided", "decision": "monitor"})
    assert refused.status_code == 403                                    # analyst may not sign off at all
    ok = client.post(f"/api/cases/{cid}", headers=ph, json={"status": "decided", "decision": "monitor"})
    assert ok.status_code == 200 and ok.json()["case"]["signoff_by"] == "pm"   # signer is the session actor
    assert ident.verify_chain()["ok"]


def test_viewer_cannot_mutate(client, ident):
    _, vtok = ident.create_account("vw", "Viewer", "viewer")
    r = client.post("/api/cases", headers={"Authorization": f"Bearer {vtok}"}, json={"ticker": "MU", "title": "x"})
    assert r.status_code == 403 and "not permitted" in r.json()["detail"]


# ----------------------------------------------------------------------------- retention safety
def test_prune_keeps_evidence_referenced_by_a_case(tmp_path):
    from datetime import datetime, timedelta, timezone
    from market_intel.store import Store
    st = Store(tmp_path / "p.sqlite")
    old = datetime.now(timezone.utc) - timedelta(days=200)
    ids = st.upsert_events([
        {"uid": "keep", "source": "s", "source_name": "SEC", "source_type": "sec", "title": "cited by a case", "published": old, "relevant": 1},
        {"uid": "drop", "source": "s", "source_name": "SEC", "source_type": "sec", "title": "not cited", "published": old, "relevant": 1}])
    keep, drop = ids[0], ids[1]
    st.insert_case({"story_id": "s1", "event_id": None, "ticker": "MU", "title": "t", "level": "warning",
                    "owner": "ana", "due": None, "sla_hours": 24, "status": "open", "evidence": [keep], "notes": []})
    n = st.prune(days=90)
    remaining = {e["id"] for e in st.events(limit=50)}
    assert keep in remaining, "retention deleted evidence a case still points at"
    assert drop not in remaining and n == 1


# ----------------------------------------------------------------------------- P0-3 second pass
def test_every_state_changing_endpoint_requires_a_capability(client, ident):
    """A viewer-level session must not be able to reach any mutation."""
    _, vtok = ident.create_account("vw2", "Viewer", "viewer")
    h = {"Authorization": f"Bearer {vtok}"}
    mutations = [("post", "/api/cases", {"ticker": "MU", "title": "x"}),
                 ("post", "/api/brief", {}),
                 ("post", "/api/poll", {}),
                 ("post", "/api/research/optimize", {"tickers": ["MU"]}),
                 ("post", "/api/research/bl", {"tickers": ["MU"]}),
                 ("post", "/api/research/bl/run", {}),
                 ("post", "/api/research/constructions/run", {}),
                 ("post", "/api/pipeline/run", {})]
    for method, path, body in mutations:
        r = getattr(client, method)(path, headers=h, json=body)
        assert r.status_code == 403, f"{path} allowed a viewer: {r.status_code}"
        assert "not permitted" in r.text


def test_shared_legacy_token_is_authenticated_but_read_only(client, ident, monkeypatch, tmp_path):
    """An unattributable session must never produce a mutation the audit log cannot name."""
    from market_intel import auth
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "shared_token")
    shared = auth.get_token()
    proxied = {"cf-connecting-ip": "203.0.113.9", "x-forwarded-proto": "https"}
    login = client.post("/login", headers=proxied, data={"password": shared, "next": "/"}, follow_redirects=False)
    assert login.status_code == 302 and auth.COOKIE in login.cookies
    session_headers = {**proxied, "cookie": f"{auth.COOKIE}={login.cookies[auth.COOKIE]}"}
    h = {**proxied, "Authorization": f"Bearer {shared}"}
    who = client.get("/api/whoami", headers=h).json()["actor"]
    assert who["username"] == "shared-token" and who["role"] == "viewer" and who["can_write"] is False
    assert who["source"] == "legacy_shared"
    assert client.get("/api/status", headers=session_headers).status_code == 200
    assert client.get("/api/events", headers=session_headers).status_code == 200
    blocked = [
        client.post("/api/cases", headers=session_headers, json={"ticker": "MU", "title": "x"}),
        client.post("/api/events/read", headers=session_headers, json={"ids": ["missing"]}),
        client.post("/api/events/missing/read", headers=session_headers),
        client.post("/api/ask", headers=session_headers, json={"question": "MU"}),
    ]
    assert all(r.status_code == 403 and "not permitted" in r.text for r in blocked)


def test_maker_checker_uses_the_immutable_author_not_the_mutable_owner(client, ident):
    _, atok = ident.create_account("maker", "Maker", "analyst")
    _, ptok = ident.create_account("pm2", "PM", "pm")
    ah, ph = {"Authorization": f"Bearer {atok}"}, {"Authorization": f"Bearer {ptok}"}
    cid = client.post("/api/cases", headers=ah, json={"ticker": "MU", "title": "c"}).json()["case"]["id"]
    # reassigning owner must NOT let the original author sign their own work off
    client.post(f"/api/cases/{cid}", headers=ph, json={"owner": "someone_else"})
    refused = client.post(f"/api/cases/{cid}", headers=ah, json={"status": "decided", "decision": "monitor"})
    assert refused.status_code == 403
    # and a PM who did not create it may sign off even though owner was reassigned
    ok = client.post(f"/api/cases/{cid}", headers=ph, json={"status": "decided", "decision": "monitor"})
    assert ok.status_code == 200 and ok.json()["case"]["signoff_by"] == "pm2"


def test_created_by_is_immutable(client, ident):
    _, atok = ident.create_account("author", "Author", "analyst")
    h = {"Authorization": f"Bearer {atok}"}
    c = client.post("/api/cases", headers=h, json={"ticker": "MU", "title": "c"}).json()["case"]
    assert c["created_by"] == "author"
    r = client.post(f"/api/cases/{c['id']}", headers=h, json={"created_by": "someone_else", "note": "n"})
    assert r.status_code == 200 and "created_by" in r.json()["ignored_fields"]
    assert client.get(f"/api/cases/{c['id']}", headers=h).json()["case"]["created_by"] == "author"


def test_a_decided_case_cannot_be_silently_edited_by_an_analyst(client, ident):
    _, atok = ident.create_account("an3", "A", "analyst")
    _, ptok = ident.create_account("pm3", "P", "pm")
    ah, ph = {"Authorization": f"Bearer {atok}"}, {"Authorization": f"Bearer {ptok}"}
    cid = client.post("/api/cases", headers=ah, json={"ticker": "MU", "title": "c"}).json()["case"]["id"]
    client.post(f"/api/cases/{cid}", headers=ph, json={"status": "decided", "decision": "monitor"})
    blocked = client.post(f"/api/cases/{cid}", headers=ah, json={"decision": "rewritten", "note": "sneaky"})
    assert blocked.status_code == 403 and "reopen" in blocked.text
    assert client.get(f"/api/cases/{cid}", headers=ph).json()["case"]["decision"] == "monitor"


def test_audit_and_business_state_commit_or_roll_back_together(tmp_path, ident):
    """An audit failure must not leave a committed but unaudited state change."""
    from market_intel.store import Store
    db = tmp_path / "atomic.sqlite"
    ident.AUDIT_PATH = db
    st = Store(db)
    a, _ = ident.create_account("atomic", "A", "analyst")
    row = {"story_id": "s1", "event_id": None, "ticker": "MU", "title": "t", "level": "warning",
           "owner": "a", "due": None, "sla_hours": 24, "status": "open", "evidence": [], "notes": []}
    with st.transaction() as cx:
        st.insert_case(row)
        ident.audit(a, "case.create", "case", "1", conn=cx)
    n_cases = st.conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    n_audit = st.conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    with pytest.raises(RuntimeError):
        with st.transaction() as cx:
            st.insert_case({**row, "story_id": "s2", "title": "t2"})
            ident.audit(a, "case.create", "case", "2", conn=cx)
            raise RuntimeError("audit sink failed")
    assert st.conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == n_cases
    assert st.conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == n_audit


def test_tail_truncation_is_detected_by_the_checkpoint(ident):
    a, _ = ident.create_account("tail", "T", "analyst")
    for i in range(4):
        ident.audit(a, "case.update", "case", "9", after={"n": i})
    assert ident.verify_chain()["ok"]
    c = sqlite3.connect(str(ident.AUDIT_PATH))
    c.execute("DELETE FROM audit_events WHERE seq=(SELECT MAX(seq) FROM audit_events)")
    c.commit(); c.close()
    v = ident.verify_chain()
    assert v["ok"] is False and v["reason"] == "tail truncation or replacement"
    assert v["checkpoint"]["n_events"] > v["n_events"]


def test_threat_model_is_stated_and_does_not_claim_immutability(ident):
    a, _ = ident.create_account("tm", "T", "analyst")
    ident.audit(a, "x", "y")
    tm = ident.verify_chain()["threat_model"]
    assert "does NOT make the table immutable" in tm and "Off-box replication" in tm


def test_account_lifecycle_is_audited(ident):
    ident.create_account("aud1", "A", "analyst")
    ident.deactivate_account("aud1")
    actions = [e["action"] for e in ident.audit_log("account", "aud1")]
    assert "account.create" in actions and "account.disable" in actions
