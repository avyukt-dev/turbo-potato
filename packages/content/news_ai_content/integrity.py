"""Canonical hashes for publication-visible content and provenance."""

import hashlib
import json
from typing import Any


def content_artifact_hash(artifact: dict[str, Any]) -> str:
    encoded = json.dumps(
        artifact,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
