"""Executable frontend regressions, including mixed snapshots and selection scope."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_risk_review_browser_independent_behaviour():
    node = shutil.which("node") or os.environ.get("MARKET_INTEL_NODE")
    if node is None:
        pytest.skip("Node.js is required for executable frontend regressions")
    test = Path(__file__).with_name("risk_review_frontend.test.cjs")
    result = subprocess.run([node, "--test", str(test)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
