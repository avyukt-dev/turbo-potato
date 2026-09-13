"""Bounded redaction for operator-supplied diagnostics; never serialize raw exceptions."""

import re


def safe_text(value: str, limit: int = 512) -> str:
    value = re.sub(
        r"\bsk-[A-Za-z0-9_-]{8,}\b|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
        "[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)\b(authorization|api[-_]?key|token|password|secret)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", value)
    value = re.sub(
        r"\b[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY)[A-Z0-9_]+\b", "[REDACTED]", value
    )
    value = re.sub(r"https?://\S+|postgres(?:ql)?[^\s]*://\S+|redis://\S+", "[REFERENCE]", value)
    return " ".join(value.split())[:limit]
