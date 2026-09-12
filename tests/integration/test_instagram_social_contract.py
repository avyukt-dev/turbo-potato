"""Stage-24 adapter integration boundary; deliberately no publication orchestration."""

from __future__ import annotations

import asyncio
from collections import deque
from uuid import uuid4

from news_ai_content import ContentFormat, ContentPlatform
from news_ai_social import (
    GraphResponse,
    InstagramAdapter,
    InstagramCarouselArtifact,
    InstagramCarouselRenderer,
    InstagramMediaItem,
    SocialMediaFormat,
    SocialMediaMimeType,
    SocialMediaType,
    load_instagram_config,
)


class GraphFixture:
    def __init__(self) -> None:
        self.responses = deque(
            [
                GraphResponse(status_code=200, payload={"id": "101"}),
                GraphResponse(status_code=200, payload={"status_code": "FINISHED"}),
                GraphResponse(status_code=200, payload={"id": "102"}),
                GraphResponse(status_code=200, payload={"status_code": "FINISHED"}),
                GraphResponse(status_code=200, payload={"id": "201"}),
                GraphResponse(status_code=200, payload={"status_code": "FINISHED"}),
                GraphResponse(status_code=200, payload={"id": "301"}),
                GraphResponse(
                    status_code=200,
                    payload={
                        "id": "301",
                        "media_type": "CAROUSEL_ALBUM",
                        "permalink": "https://www.instagram.com/p/test-fixture/",
                    },
                ),
            ]
        )

    async def post(self, path: str, *, data: dict[str, str]) -> GraphResponse:
        return self.responses.popleft()

    async def get(self, path: str, *, params: dict[str, str]) -> GraphResponse:
        return self.responses.popleft()


async def _no_sleep(_: float) -> None:
    return None


def test_approved_shaped_artifact_renders_and_publishes_through_graph_contract() -> None:
    artifact = InstagramCarouselArtifact(
        content_variant_id=uuid4(),
        content_variant_version=1,
        platform=ContentPlatform.INSTAGRAM,
        format=ContentFormat.CAROUSEL,
        caption="Reviewed words remain exact.",
        hashtags=("#Reviewed",),
        media_items=(
            InstagramMediaItem(
                position=1,
                public_url="https://media.example.com/slide-1.jpg",
                media_type=SocialMediaType.IMAGE,
                media_format=SocialMediaFormat.JPEG,
                mime_type=SocialMediaMimeType.JPEG,
            ),
            InstagramMediaItem(
                position=2,
                public_url="https://media.example.com/slide-2.jpg",
                media_type=SocialMediaType.IMAGE,
                media_format=SocialMediaFormat.JPEG,
                mime_type=SocialMediaMimeType.JPEG,
            ),
        ),
    )
    command = InstagramCarouselRenderer().render(artifact)
    adapter = InstagramAdapter(
        load_instagram_config("config"),
        account_id="123",
        transport=GraphFixture(),
        sleeper=_no_sleep,
    )

    result = asyncio.run(adapter.publish(command))

    assert command.caption == artifact.caption
    assert command.hashtags == artifact.hashtags
    assert result.external_post_id == "301"
    assert str(result.external_url) == "https://www.instagram.com/p/test-fixture/"
    assert result.provider_metadata["verification_status"] == "PUBLISHED"
