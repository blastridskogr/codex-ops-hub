"""Small safety scanner for Hermes outbox and wiki payloads."""

from __future__ import annotations

import re

_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    re.compile(r"authorization:\s*bearer\s+\S+", re.IGNORECASE),
    re.compile(r"\b(api[_-]?key|token|password|secret)\b\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"^\s*diff --git ", re.MULTILINE),
    re.compile(r"^\s*@@ ", re.MULTILINE),
]


class SecretScanError(RuntimeError):
    """Raised when content is not safe to persist."""


def ensure_safe_text(*texts: str) -> None:
    for text in texts:
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text or ""):
                raise SecretScanError("SECRET_OR_RAW_ARTIFACT_DETECTED")
