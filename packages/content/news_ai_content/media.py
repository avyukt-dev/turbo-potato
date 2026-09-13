"""Ordered caller-owned media validation. No download, mutation, or AI inspection."""

import re
from uuid import UUID

from news_ai_database import ContentVariant, MediaAsset
from sqlalchemy import select
from sqlalchemy.orm import Session


class MediaValidationError(ValueError):
    def __init__(self) -> None:
        super().__init__("caller media provenance is invalid")


class MediaNotAttachedError(MediaValidationError):
    def __init__(self) -> None:
        ValueError.__init__(self, "required caller media is not attached")


def load_media_provenance(
    session: Session, variant: ContentVariant, *, lock: bool = False
) -> list[dict[str, str]]:
    """Lock in UUID order, return validated provenance in carousel/caller order."""
    # Lazy import keeps the platform package's content contracts dependency acyclic.
    from news_ai_social import SocialAdapterError, canonical_public_media_url

    if variant.platform != "INSTAGRAM" or variant.format != "CAROUSEL":
        raise MediaValidationError()
    raw_ids = variant.media_asset_ids
    if not isinstance(raw_ids, list):
        raise MediaValidationError()
    if not raw_ids:
        raise MediaNotAttachedError()
    try:
        ids = tuple(UUID(str(item)) for item in raw_ids)
    except (ValueError, TypeError, AttributeError):
        raise MediaValidationError() from None
    if len(ids) != len(set(ids)):
        raise MediaValidationError()
    slides = (
        variant.structured_payload.get("slides")
        if isinstance(variant.structured_payload, dict)
        else None
    )
    if not isinstance(slides, list) or not 2 <= len(slides) <= 10 or len(slides) != len(ids):
        raise MediaValidationError()
    statement = (
        select(MediaAsset)
        .where(MediaAsset.id.in_(ids))
        .order_by(MediaAsset.id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    assets = {asset.id: asset for asset in session.scalars(statement)}
    if len(assets) != len(ids):
        raise MediaValidationError()
    snapshot = []
    for identifier in ids:
        asset = assets[identifier]
        media_format = (
            asset.source_metadata.get("media_format")
            if isinstance(asset.source_metadata, dict)
            else None
        )
        if (
            asset.asset_type != "IMAGE"
            or asset.mime_type != "image/jpeg"
            or media_format != "JPEG"
            or asset.visual_check_status != "VALIDATED"
            or not isinstance(asset.file_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", asset.file_hash) is None
        ):
            raise MediaValidationError()
        try:
            url = canonical_public_media_url(asset.public_url)
        except SocialAdapterError:
            raise MediaValidationError() from None
        snapshot.append(
            {
                "id": str(asset.id),
                "file_hash": asset.file_hash,
                "asset_type": asset.asset_type,
                "mime_type": asset.mime_type,
                "media_format": media_format,
                "public_url": url,
                "visual_check_status": asset.visual_check_status,
            }
        )
    return snapshot
