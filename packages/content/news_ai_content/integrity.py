"""Canonical hashes for publication-visible content and provenance."""

import hashlib
import json
from typing import Any
from uuid import UUID

from news_ai_database import ContentVariant
from sqlalchemy.orm import Session

from .media import load_media_provenance


def quality_artifact(
    session: Session, variant: ContentVariant, *, lock_media: bool = False
) -> dict[str, Any]:
    """Current v3 integrity boundary, shared by quality and human review."""
    media = load_media_provenance(session, variant, lock=lock_media)
    return {
        "content_variant_id": str(variant.id),
        "content_variant_version": variant.version,
        "platform": variant.platform,
        "format": variant.format,
        "language": variant.language.strip().replace("_", "-").lower(),
        "title": variant.title,
        "body": variant.body,
        "caption": variant.caption,
        "structured_payload": variant.structured_payload,
        "claim_ids_used": [str(UUID(str(item))) for item in variant.claim_ids_used],
        "source_ids_used": [str(UUID(str(item))) for item in variant.source_ids_used],
        "media_asset_ids": [item["id"] for item in media],
        "media_provenance": media,
    }


def content_artifact_hash(artifact: dict[str, Any]) -> str:
    encoded = json.dumps(
        artifact,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
