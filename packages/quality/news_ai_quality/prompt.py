"""Versioned quality prompt loading."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class QualityPrompt:
    prompt_id: str
    version: str
    checksum: str
    system_prompt: str

    @classmethod
    def load(cls, path: Path, *, version: str = "v1") -> QualityPrompt:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("quality prompt must not be blank")
        return cls("content-quality", version, hashlib.sha256(text.encode()).hexdigest(), text)
