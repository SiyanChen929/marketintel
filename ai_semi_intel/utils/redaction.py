"""Remove credential-bearing values from operational diagnostics."""
from __future__ import annotations

import re
from urllib.parse import quote, quote_plus


_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|token)\b[\"']?\s*[:=]\s*[\"']?)([^\s&\"'<>;,}\]]+)"
)
_AUTH = re.compile(r"(?i)(\bauthorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)\s+)([^\s\"'<>;,}\]]+)")
_PROVIDER_KEY = re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}\b")


def redact_sensitive_text(value: str, *, secrets=()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            for token in {str(secret), quote(str(secret), safe=""), quote_plus(str(secret), safe="")}:
                text = text.replace(token, "[REDACTED]")
    text = _AUTH.sub(lambda m: m.group(0) if m.group(2).startswith("[REDACTED") else m.group(1) + "[REDACTED]", text)
    text = _ASSIGNMENT.sub(lambda m: m.group(0) if m.group(2).startswith("[REDACTED") else m.group(1) + "[REDACTED]", text)
    return _PROVIDER_KEY.sub("[REDACTED]", text)
