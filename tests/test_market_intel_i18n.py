"""Offline localization checks; no server, research service, or source data writes.

The optional browser suite fulfills all navigation with synthetic HTML and only
loads the project's static localization scripts into an isolated headless browser.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "market_intel" / "static"
RESOURCES = (
    "i18n.js",
    "i18n-common.js",
    "i18n-home.js",
    "i18n-portfolio.js",
    "i18n-platform.js",
)


def _node() -> str | None:
    """Honor normal installations, or an explicit $MARKET_INTEL_NODE."""
    found = shutil.which("node") or os.environ.get("MARKET_INTEL_NODE")
    return found if found and Path(found).is_file() else None


@pytest.mark.parametrize("page", sorted(STATIC.glob("*.html")), ids=lambda p: p.name)
def test_every_screen_loads_translation_before_ui_helpers(page: Path):
    from market_intel.server import _versioned

    # Canonical page routes load resources through this shared serving boundary.
    source = _versioned(page.read_text(encoding="utf-8"))
    scripts = [value.split("?", 1)[0] for value in re.findall(r'<script\b[^>]*\bsrc=[\"\']([^\"\']+)', source)]
    runtime = "/static/i18n.js"
    common = "/static/i18n-common.js"
    assert scripts.count(runtime) == 1, f"{page.name} needs exactly one localization runtime"
    assert scripts.count(common) == 1, f"{page.name} needs shared UI terminology"
    assert scripts.index(runtime) < scripts.index(common)
    if "/static/base.js" in scripts:
        assert scripts.index(common) < scripts.index("/static/base.js")
    for script in scripts:
        if script.startswith("/static/i18n-"):
            assert scripts.index(runtime) < scripts.index(script)
            assert (STATIC / Path(script).name).is_file(), script
    assert _versioned(source) == source, "Serving transformation must not duplicate localization scripts"


@pytest.mark.parametrize("name", RESOURCES)
def test_translation_scripts_have_valid_javascript(name: str):
    path = STATIC / name
    assert path.is_file(), path
    node = _node()
    if not node:
        pytest.skip("Node is required to check JavaScript syntax")
    result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_localization_uses_local_resources_and_visible_language_names():
    runtime = (STATIC / "i18n.js").read_text(encoding="utf-8")
    for value in ("English", "中文", "日本語", "mi-language"):
        assert value in runtime, value
    resources = "\n".join((STATIC / name).read_text(encoding="utf-8") for name in RESOURCES)
    assert not re.search(r"https?://", resources), "UI translation must not require a remote translation service"


def test_localization_frontend_behavior():
    node = _node()
    if not node:
        pytest.skip("Node is required for the frontend behavior suite")
    env = os.environ.copy()
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("market_intel_i18n_frontend.test.cjs"))],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
