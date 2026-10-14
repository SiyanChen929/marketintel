"""Test isolation for stateful singletons.

The identity module writes named accounts and the tamper-evident audit chain to a single database under
``data_cache``. Without isolation a test run appends to the *production* audit log, which both pollutes it
and destroys its meaning: the chain is supposed to be an authoritative record of operator actions, not a
mixture of those and fixture noise. Every test therefore gets its own accounts/audit database.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_identity_store(tmp_path, monkeypatch):
    from market_intel import identity
    monkeypatch.setattr(identity, "ACCOUNTS_PATH", tmp_path / "accounts.sqlite", raising=False)
    monkeypatch.setattr(identity, "AUDIT_PATH", tmp_path / "audit.sqlite", raising=False)
    yield


@pytest.fixture(scope="session")
def wf():
    """One real walk-forward on the live panel, shared across the ledger tests (it is expensive)."""
    import warnings
    import pandas as pd
    warnings.filterwarnings("ignore")
    from ai_semi_intel.portfolio import construction_eval as CE
    from market_intel import research
    from pathlib import Path
    panel = Path("data_cache/prices/long_adj_close.parquet")
    if not panel.exists():
        pytest.skip("needs the locally fetched price panel (python scripts/fetch_long_history.py)")
    px = pd.read_parquet(panel)
    uni = research.universe()
    if uni is None or uni.empty:
        pytest.skip("no universe artifact")
    return CE.walk_forward(px, uni, methods=["equal_weight", "risk_parity", "min_variance"],
                           start="2022-01-01", vol_target=0.25)
