from __future__ import annotations

from types import SimpleNamespace


class _Response:
    def __init__(self, body: str):
        self.body = body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def _quick_tunnel(monkeypatch, tmp_path, connections: str):
    from market_intel import tunnel

    monkeypatch.setattr(tunnel, "URL_FILE", tmp_path / "no-named-url")
    monkeypatch.setattr(tunnel, "LOG", tmp_path / "no-log")
    monkeypatch.setattr(tunnel, "tunnel_running", lambda: True)

    def fake_urlopen(url, timeout):
        assert timeout == 2
        if str(url).endswith("/quicktunnel"):
            return _Response('{"hostname":"current.trycloudflare.com"}')
        if str(url).endswith("/metrics"):
            return _Response(connections)
        raise AssertionError(url)

    monkeypatch.setattr(tunnel.urllib.request, "urlopen", fake_urlopen)
    return tunnel


def test_public_url_rejects_stale_tunnel_with_no_edge_connection(monkeypatch, tmp_path):
    tunnel = _quick_tunnel(monkeypatch, tmp_path, "cloudflared_tunnel_ha_connections 0\n")
    monkeypatch.setattr(tunnel, "_hostname_resolves", lambda _url: True)

    assert tunnel.public_url() is None
    assert tunnel.status()["connected"] is False
    assert tunnel.status()["public_url"] is None


def test_public_url_rejects_hostname_missing_from_dns(monkeypatch, tmp_path):
    tunnel = _quick_tunnel(monkeypatch, tmp_path, "cloudflared_tunnel_ha_connections 1\n")
    monkeypatch.setattr(tunnel, "_hostname_resolves", lambda _url: False)

    assert tunnel.public_url() is None


def test_public_url_accepts_connected_resolving_tunnel(monkeypatch, tmp_path):
    metrics = 'cloudflared_tunnel_ha_connections{connector_id="a"} 0\ncloudflared_tunnel_ha_connections{connector_id="b"} 1\n'
    tunnel = _quick_tunnel(monkeypatch, tmp_path, metrics)
    monkeypatch.setattr(tunnel, "_hostname_resolves", lambda url: url == "https://current.trycloudflare.com")

    assert tunnel.public_url() == "https://current.trycloudflare.com"
    status = tunnel.status()
    assert status["running"] is True
    assert status["connected"] is True
    assert status["public_url"] == "https://current.trycloudflare.com"


def test_hostname_validation_requires_https_and_public_dns(monkeypatch):
    from market_intel import tunnel

    monkeypatch.setattr(tunnel.socket, "getaddrinfo", lambda host, port, type: [(host, port, type)])
    assert tunnel._hostname_resolves("https://example.com") is True
    assert tunnel._hostname_resolves("http://example.com") is False
    assert tunnel._hostname_resolves("not-a-url") is False


def test_quick_tunnel_uses_dedicated_metrics_port():
    from market_intel import tunnel

    assert tunnel.METRICS == "127.0.0.1:20242"


def test_tunnel_running_uses_dedicated_launch_agent(monkeypatch):
    from market_intel import tunnel

    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="state = running\n")

    monkeypatch.setattr(tunnel.os, "getuid", lambda: 501)
    monkeypatch.setattr(tunnel.subprocess, "run", fake_run)

    assert tunnel.tunnel_running() is True
    assert seen["argv"] == ["launchctl", "print", "gui/501/com.siyanchen.marketintel.tunnel"]
    assert seen["kwargs"] == {"capture_output": True, "text": True, "timeout": 5}


def test_tunnel_running_rejects_managed_connector_without_quick_hostname(monkeypatch):
    from market_intel import tunnel

    monkeypatch.setattr(
        tunnel.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=113, stdout=""),
    )
    monkeypatch.setattr(
        tunnel.urllib.request,
        "urlopen",
        lambda url, timeout: _Response('{"hostname":""}'),
    )

    assert tunnel.tunnel_running() is False


def test_tunnel_running_detects_manually_started_quick_tunnel(monkeypatch):
    from market_intel import tunnel

    monkeypatch.setattr(
        tunnel.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=113, stdout=""),
    )
    monkeypatch.setattr(
        tunnel.urllib.request,
        "urlopen",
        lambda url, timeout: _Response('{"hostname":"manual.trycloudflare.com"}'),
    )

    assert tunnel.tunnel_running() is True


def _stuck_tunnel(monkeypatch, tmp_path, log_text: str):
    from market_intel import tunnel

    monkeypatch.setattr(tunnel, "URL_FILE", tmp_path / "no-named-url")
    monkeypatch.setattr(tunnel, "LOG", tmp_path / "tunnel.log")
    tunnel.LOG.write_text(log_text)
    monkeypatch.setattr(tunnel, "_launch_agent_running", lambda: True)
    monkeypatch.setattr(tunnel, "tunnel_connected", lambda: False)
    return tunnel


def test_stuck_quick_tunnel_requires_repeated_error_in_current_run(monkeypatch, tmp_path):
    tunnel = _stuck_tunnel(
        monkeypatch,
        tmp_path,
        "Unauthorized: Tunnel not found\n"
        "Unauthorized: Tunnel not found\n"
        "Requesting new quick Tunnel on trycloudflare.com...\n",
    )
    assert tunnel.quick_tunnel_stuck() is False

    tunnel.LOG.write_text(
        "Requesting new quick Tunnel on trycloudflare.com...\n"
        "Unauthorized: Tunnel not found\n"
        "Unauthorized: Record for tunnel not found\n"
        "Registered tunnel connection connIndex=0\n"
    )
    assert tunnel.quick_tunnel_stuck() is False

    with tunnel.LOG.open("a") as f:
        f.write("Unauthorized: Tunnel not found\nUnauthorized: Record for tunnel not found\n")
    assert tunnel.quick_tunnel_stuck() is True
    monkeypatch.setattr(tunnel, "tunnel_connected", lambda: True)
    assert tunnel.quick_tunnel_stuck() is False


def test_stuck_quick_tunnel_ignores_named_and_manual_tunnels(monkeypatch, tmp_path):
    tunnel = _stuck_tunnel(
        monkeypatch,
        tmp_path,
        "Requesting new quick Tunnel\nUnauthorized: Tunnel not found\nUnauthorized: Tunnel not found\n",
    )
    tunnel.URL_FILE.write_text("https://intel.example.com\n")
    assert tunnel.quick_tunnel_stuck() is False

    tunnel.URL_FILE.unlink()
    monkeypatch.setattr(tunnel, "_launch_agent_running", lambda: False)
    assert tunnel.quick_tunnel_stuck() is False


def test_restart_targets_only_dedicated_quick_tunnel_agent(monkeypatch, tmp_path):
    from market_intel import tunnel

    seen = {}
    monkeypatch.setattr(tunnel, "URL_FILE", tmp_path / "no-named-url")
    monkeypatch.setattr(tunnel, "_launch_agent_running", lambda: True)
    monkeypatch.setattr(tunnel.os, "getuid", lambda: 501)

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tunnel.subprocess, "run", fake_run)
    assert tunnel.restart_quick_tunnel() is True
    assert seen["argv"] == ["launchctl", "kickstart", "-k", "gui/501/com.siyanchen.marketintel.tunnel"]
    assert seen["kwargs"] == {"capture_output": True, "text": True, "timeout": 15}
    assert tunnel.METRICS == "127.0.0.1:20242"


class _FiniteStop:
    def __init__(self, checks: int):
        self.remaining = checks

    def wait(self, _interval: float) -> bool:
        if self.remaining <= 0:
            return True
        self.remaining -= 1
        return False


def test_watchdog_requires_consecutive_failures(monkeypatch):
    from market_intel import tunnel

    states = iter((True, False, True, True))
    restarts = []
    monkeypatch.setattr(tunnel, "quick_tunnel_stuck", lambda: next(states))
    monkeypatch.setattr(tunnel, "restart_quick_tunnel", lambda: restarts.append(True) or True)
    monkeypatch.setattr(tunnel.time, "monotonic", lambda: 100.0)

    tunnel.watchdog_loop(_FiniteStop(4), interval_s=0, bad_samples=2, cooldown_s=300)
    assert restarts == [True]


def test_watchdog_throttles_restarts(monkeypatch):
    from market_intel import tunnel

    times = iter((100.0, 200.0, 401.0))
    restarts = []
    monkeypatch.setattr(tunnel, "quick_tunnel_stuck", lambda: True)
    monkeypatch.setattr(tunnel, "restart_quick_tunnel", lambda: restarts.append(True) or True)
    monkeypatch.setattr(tunnel.time, "monotonic", lambda: next(times))

    tunnel.watchdog_loop(_FiniteStop(6), interval_s=0, bad_samples=2, cooldown_s=300)
    assert restarts == [True, True]
