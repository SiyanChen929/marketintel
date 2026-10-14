"""Credential-bearing HTTP failures must not enter the research status UI."""
import json

import pytest

from ai_semi_intel.utils.redaction import redact_sensitive_text


@pytest.mark.parametrize("message", [
    "GET https://example.test/data?series=DGS10&api_key=fake-private-token&limit=2",
    '{"Authorization": "Bearer fake-private-token"}',
    "access_token=fake-private-token; status=401",
    "password: fake-private-token",
])
def test_redact_credential_values_without_discarding_error_context(message):
    clean = redact_sensitive_text(message)
    assert "fake-private-token" not in clean and "[REDACTED]" in clean
    assert redact_sensitive_text(clean) == clean
    assert redact_sensitive_text("HTTP 503 for DGS10; fetched_at=2026-09-22") == "HTTP 503 for DGS10; fetched_at=2026-09-22"


def test_fred_http_exception_is_redacted_before_cache_status_and_logging(tmp_path, monkeypatch, caplog):
    from ai_semi_intel.data import fred
    monkeypatch.setattr(fred, "FRED_DIR", tmp_path)
    monkeypatch.setattr(fred, "STATUS_PATH", tmp_path / "_status.json")
    monkeypatch.setattr(fred, "load_fred_key", lambda: "fake-private-token")
    def unavailable(*a, **kw):
        raise RuntimeError("503 at https://example.test?api_key=fake-private-token&series_id=DGS10")
    monkeypatch.setattr(fred.requests, "get", unavailable)
    result = fred.fetch_series("DGS10")
    assert result.empty and result.attrs["status"] == "unavailable"
    outputs = caplog.text + json.dumps(result.attrs) + (tmp_path / "_status.json").read_text()
    assert "fake-private-token" not in outputs and "503" in outputs


def test_log_is_redacted_before_tail_cuts_off_the_credential_label(tmp_path, monkeypatch):
    from market_intel import config, research
    runner = research.PipelineRunner()
    runner.LOG = tmp_path / "pipeline.log"
    runner.LOG.write_text("api_key=" + "x" * 4100 + "&error=503")
    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    monkeypatch.setattr(research, "summary", lambda: {})
    tail = runner.status()["log_tail"]
    assert "x" * 20 not in tail and "[REDACTED]" in tail and "503" in tail
