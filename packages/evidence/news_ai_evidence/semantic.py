"""Stable semantic identities for durable high-value transitions."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def semantic_key(namespace: str, value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"{namespace}:{hashlib.sha256(encoded).hexdigest()}"
