"""Internet exposure through a Cloudflare quick tunnel (cloudflared): no account, no port forwarding, HTTPS.

The tunnel runs as its own launchd agent (``scripts/com.siyanchen.marketintel.tunnel.plist``). Its public hostname is read
from cloudflared's local metrics endpoint (``/quicktunnel``) with the log file as a fallback. Quick-tunnel hostnames
change whenever cloudflared restarts; a named tunnel (``cloudflared tunnel login`` + your own domain) gives a stable
hostname and is documented in docs/MARKET_INTEL.md.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import shutil
import subprocess
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from . import config

METRICS = "127.0.0.1:20242"  # dedicated to the quick tunnel; the managed WARP connector uses 20241
LAUNCH_AGENT = "com.siyanchen.marketintel.tunnel"
URL_FILE = config.DATA_DIR / "public_url"           # written by scripts/setup_named_tunnel.sh (stable hostname)
LAST_URL_FILE = config.LATEST / "public_url.txt"     # last observed quick-tunnel URL, for convenience
LOG = config.LOGS / "tunnel.out"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CONNECTION_RE = re.compile(r"^cloudflared_tunnel_ha_connections(?:\{[^}]*\})?\s+([0-9.eE+-]+)(?:\s|$)", re.MULTILINE)
QUICK_START_MARKER = "Requesting new quick Tunnel"
QUICK_CONNECTED_MARKER = "Registered tunnel connection"
QUICK_GONE_RE = re.compile(r"Unauthorized:\s+(?:Record for\s+)?Tunnel not found", re.IGNORECASE)
log = logging.getLogger(__name__)


def cloudflared_path() -> str | None:
    return shutil.which("cloudflared") or ("/opt/homebrew/bin/cloudflared" if shutil.os.path.exists("/opt/homebrew/bin/cloudflared") else None)


def _candidate_url() -> str | None:
    """Configured/current hostname before connection and DNS validation."""
    if URL_FILE.exists():
        u = URL_FILE.read_text().strip()
        if u:
            return u
    try:
        with urllib.request.urlopen(f"http://{METRICS}/quicktunnel", timeout=2) as r:
            data = json.loads(r.read().decode())
            host = data.get("hostname")
            if host:
                return f"https://{host}"
    except Exception:
        pass
    if LOG.exists():
        try:
            tail = LOG.read_text()[-20000:]
            found = URL_RE.findall(tail)
            if found:
                return found[-1]
        except Exception:
            pass
    return None


def tunnel_connected() -> bool:
    """Whether cloudflared reports at least one active edge connection."""
    try:
        with urllib.request.urlopen(f"http://{METRICS}/metrics", timeout=2) as r:
            metrics = r.read().decode(errors="replace")
        return any(float(value) > 0 for value in CONNECTION_RE.findall(metrics))
    except Exception:
        return False


def _hostname_resolves(url: str) -> bool:
    """Fail closed when a cached/logged hostname is no longer in public DNS."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        return bool(socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM))
    except (OSError, ValueError):
        return False


def public_url() -> str | None:
    """Return a phone-usable URL only while the tunnel is connected and its hostname resolves."""
    url = _candidate_url()
    if not url or not tunnel_connected() or not _hostname_resolves(url):
        return None
    return url


def _launch_agent_running() -> bool:
    """Whether the dedicated public-tunnel launchd job is loaded and running."""
    try:
        out = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LAUNCH_AGENT}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and re.search(r"^\s*state = running\s*$", out.stdout, re.MULTILINE):
            return True
    except Exception:
        pass
    return False


def tunnel_running() -> bool:
    """Whether this quick tunnel is running, without matching other cloudflared connectors."""
    if _launch_agent_running():
        return True

    # Also support a quick tunnel started manually. This endpoint is on the
    # quick tunnel's dedicated port, so a managed connector cannot satisfy it.
    try:
        with urllib.request.urlopen(f"http://{METRICS}/quicktunnel", timeout=2) as r:
            return bool(json.loads(r.read().decode()).get("hostname"))
    except Exception:
        return False


def _named_public_tunnel_configured() -> bool:
    """Fail safe: a non-empty marker means the shared launchd label now runs a named tunnel."""
    if not URL_FILE.exists():
        return False
    try:
        return bool(URL_FILE.read_text().strip())
    except OSError:
        return True


def _log_tail(max_bytes: int = 65536) -> str:
    """Read only the end of cloudflared's append-only log."""
    try:
        with LOG.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - max_bytes))
            return f.read().decode(errors="replace")
    except OSError:
        return ""


def quick_tunnel_stuck() -> bool:
    """Detect an expired Quick Tunnel without reacting to an ordinary network interruption."""
    if _named_public_tunnel_configured() or not _launch_agent_running() or tunnel_connected():
        return False
    tail = _log_tail()
    # A few Unauthorized responses can occur while a fresh tunnel propagates. A
    # later successful registration clears those; only errors since the latest
    # start/connection are evidence that the currently issued hostname is gone.
    boundary = max(tail.rfind(QUICK_START_MARKER), tail.rfind(QUICK_CONNECTED_MARKER))
    current_state = tail[boundary:] if boundary >= 0 else tail
    return len(QUICK_GONE_RE.findall(current_state)) >= 2


def restart_quick_tunnel() -> bool:
    """Restart only the dedicated Quick Tunnel launchd job, causing Cloudflare to issue a new URL."""
    if _named_public_tunnel_configured() or not _launch_agent_running():
        return False
    try:
        out = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCH_AGENT}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        log.warning("Quick Tunnel recovery failed: %s", exc)
        return False
    if out.returncode != 0:
        log.warning("Quick Tunnel recovery failed with launchctl exit %s", out.returncode)
        return False
    log.warning("expired Quick Tunnel restarted; Cloudflare will issue a new public URL")
    return True


def watchdog_loop(
    stop: threading.Event,
    *,
    interval_s: float = 30,
    bad_samples: int = 2,
    cooldown_s: float = 300,
) -> None:
    """Recover a poisoned Quick Tunnel after repeated confirmation, with restart throttling."""
    failures = 0
    last_restart: float | None = None
    threshold = max(1, int(bad_samples))
    while not stop.wait(interval_s):
        try:
            if not quick_tunnel_stuck():
                failures = 0
                continue
            failures = min(threshold, failures + 1)
            if failures < threshold:
                continue
            now = time.monotonic()
            if last_restart is not None and now - last_restart < cooldown_s:
                continue
            if restart_quick_tunnel():
                last_restart = now
            failures = 0
        except Exception:
            failures = 0
            log.exception("Quick Tunnel watchdog check failed")


def status() -> dict:
    process_running = tunnel_running()
    url = public_url()
    return {"installed": bool(cloudflared_path()), "running": process_running or bool(url), "process_running": process_running,
            "connected": bool(url), "public_url": url,
            "metrics": f"http://{METRICS}/metrics", "log": str(LOG)}
