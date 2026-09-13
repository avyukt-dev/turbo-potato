"""Pure Stage-24 command construction from locked durable Stage-25 references."""

from __future__ import annotations

from uuid import UUID

from news_ai_content import ContentFormat, ContentPlatform
from news_ai_database import ContentVariant, MediaAsset
from news_ai_social import (
    InstagramCarouselArtifact,
    InstagramCarouselRenderer,
    InstagramMediaItem,
    InstagramPlatformConfig,
    SocialAdapterError,
    SocialErrorClass,
    SocialMediaFormat,
    SocialMediaMimeType,
    SocialMediaType,
    validate_instagram_request,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import PublicationError


def build_instagram_publication_request(
    session: Session,
    variant: ContentVariant,
    config: InstagramPlatformConfig,
    *,
    lock_media: bool = True,
):
    """Build and validate the exact Stage-24 request without transport or network I/O."""

    raw_ids = variant.media_asset_ids
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) != len(set(raw_ids)):
        raise PublicationError("MEDIA_UNAVAILABLE")
    try:
        ordered_ids = tuple(UUID(str(identifier)) for identifier in raw_ids)
    except (TypeError, ValueError) as exc:
        raise PublicationError("MEDIA_UNAVAILABLE") from exc

    statement = select(MediaAsset).where(MediaAsset.id.in_(ordered_ids)).order_by(MediaAsset.id)
    if lock_media:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    assets = {asset.id: asset for asset in session.scalars(statement)}
    if len(assets) != len(ordered_ids):
        raise PublicationError("MEDIA_UNAVAILABLE")

    payload = variant.structured_payload
    slides = payload.get("slides") if isinstance(payload, dict) else None
    hashtags = payload.get("hashtags", ()) if isinstance(payload, dict) else ()
    if not isinstance(slides, list) or len(slides) != len(ordered_ids):
        raise PublicationError("MEDIA_UNAVAILABLE")

    try:
        media_items = tuple(
            InstagramMediaItem(
                position=index,
                public_url=assets[asset_id].public_url,
                media_type=SocialMediaType(assets[asset_id].asset_type),
                media_format=SocialMediaFormat(
                    assets[asset_id].source_metadata.get("media_format")
                    if isinstance(assets[asset_id].source_metadata, dict)
                    else None
                ),
                mime_type=SocialMediaMimeType(assets[asset_id].mime_type),
            )
            for index, asset_id in enumerate(ordered_ids, start=1)
        )
        if any(assets[asset_id].visual_check_status != "VALIDATED" for asset_id in ordered_ids):
            raise PublicationError("MEDIA_UNAVAILABLE")
        artifact = InstagramCarouselArtifact(
            content_variant_id=variant.id,
            content_variant_version=variant.version,
            platform=ContentPlatform(variant.platform),
            format=ContentFormat(variant.format),
            caption=variant.caption,
            hashtags=tuple(hashtags),
            media_items=media_items,
        )
        request = InstagramCarouselRenderer().render(artifact)
        validate_instagram_request(request, config)
        return request
    except PublicationError:
        raise
    except SocialAdapterError as exc:
        code = (
            "MEDIA_UNAVAILABLE"
            if exc.classification is SocialErrorClass.MEDIA
            else "PLATFORM_CONSTRAINT"
        )
        raise PublicationError(code) from exc
    except (ValidationError, TypeError, ValueError, KeyError) as exc:
        raise PublicationError("PLATFORM_CONSTRAINT") from exc
