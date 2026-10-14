from pathlib import Path
import shutil
import subprocess
import pytest


def test_platform_performance_browser_state_contracts():
    node=shutil.which('node')
    if not node:
        pytest.skip('Node is required for the frontend behavior harness')
    p=subprocess.run([node,'--test',str(Path(__file__).with_name('platform_performance_frontend.test.cjs'))],capture_output=True,text=True)
    assert p.returncode==0,p.stdout+p.stderr
