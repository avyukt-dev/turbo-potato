"""Synthetic caller attestations, attached through the production service."""

import hashlib
from uuid import uuid4

from news_ai_content import ContentMediaAttachmentService, MediaAttachmentRequest
from news_ai_database import ContentVariant, MediaAsset
from sqlalchemy import select


def persist_caller_assets(session, count=2):
    assets = [
        MediaAsset(
            asset_type="IMAGE",
            storage_provider="test-caller",
            storage_key=f"caller-{uuid4()}.jpg",
            public_url=f"https://media.example.org/caller-{uuid4()}.jpg",
            mime_type="image/jpeg",
            file_hash=hashlib.sha256(f"synthetic caller image {uuid4()}".encode()).hexdigest(),
            visual_check_status="VALIDATED",
            source_metadata={"media_format": "JPEG"},
        )
        for _ in range(count)
    ]
    session.add_all(assets)
    session.flush()
    return tuple(asset.id for asset in assets)


def attach_caller_media(factory, variant_id=None):
    with factory() as session, session.begin():
        variant = (
            session.get(ContentVariant, variant_id)
            if variant_id
            else session.scalar(select(ContentVariant))
        )
        ids = persist_caller_assets(session, len(variant.structured_payload["slides"]))
        request = MediaAttachmentRequest(
            content_variant_id=variant.id,
            expected_content_variant_version=variant.version,
            ordered_media_asset_ids=ids,
        )
    ContentMediaAttachmentService(factory).attach_media(request)
    return ids
