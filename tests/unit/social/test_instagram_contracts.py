from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from news_ai_content import ContentFormat, ContentPlatform
from news_ai_social import (
    InstagramCarouselArtifact,
    InstagramCarouselRenderer,
    InstagramCarouselRequest,
    InstagramMediaItem,
    MockInstagramAdapter,
    SocialAdapterError,
    SocialErrorClass,
    SocialMediaType,
    SocialPlatformAdapter,
    load_instagram_config,
)
from pydantic import ValidationError


def _request(
    *,
    urls: tuple[str, ...] = ("https://media.example.com/1.jpg", "https://media.example.com/2.jpg"),
    caption: str = "Exact caption",
) -> InstagramCarouselRequest:
    return InstagramCarouselRequest(
        content_variant_id=uuid4(),
        content_variant_version=3,
        platform=ContentPlatform.INSTAGRAM,
        format=ContentFormat.CAROUSEL,
        caption=caption,
        hashtags=("#Exact", "#News"),
        media_items=tuple(
            InstagramMediaItem(
                position=position,
                public_url=url,
                media_type=SocialMediaType.IMAGE,
            )
            for position, url in enumerate(urls, start=1)
        ),
        correlation_id=uuid4(),
    )


def test_renderer_preserves_approved_content_exactly() -> None:
    request = _request()
    artifact = InstagramCarouselArtifact.model_validate(request.model_dump())
    rendered = InstagramCarouselRenderer().render(artifact)

    assert rendered == request
    assert rendered.caption == "Exact caption"
    assert rendered.hashtags == ("#Exact", "#News")
    assert rendered.graph_caption == "Exact caption\n\n#Exact #News"


def test_mock_satisfies_adapter_contract_and_is_deterministic() -> None:
    adapter = MockInstagramAdapter(load_instagram_config("config"))
    assert isinstance(adapter, SocialPlatformAdapter)
    request = _request()

    first = asyncio.run(adapter.publish(request))
    second = asyncio.run(adapter.publish(request))
    verified = asyncio.run(adapter.verify_publication(first.external_post_id))

    assert first == second
    assert first.mock is True
    assert first.external_post_id.startswith("mock_ig_")
    assert first.external_url is not None
    assert first.external_url.host.endswith(".invalid")
    assert verified.status.value == "PUBLISHED"
    assert adapter.capabilities.carousel is True
    assert adapter.capabilities.image is True
    assert adapter.capabilities.video is False
    assert adapter.capabilities.reel is False


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a.jpg",
        "https://localhost/a.jpg",
        "https://127.0.0.1/a.jpg",
        "https://10.0.0.1/a.jpg",
        "https://172.16.0.1/a.jpg",
        "https://192.168.1.1/a.jpg",
        "https://[::1]/a.jpg",
        "https://host.internal/a.jpg",
        "https://user:password@example.com/a.jpg",
        "https://media.example.com/a.jpg?access_token=secret",
    ],
)
def test_public_media_security_rejects_unsafe_urls(url: str) -> None:
    adapter = MockInstagramAdapter(load_instagram_config("config"))
    with pytest.raises(SocialAdapterError) as caught:
        adapter.validate_content(_request(urls=(url, "https://media.example.com/2.jpg")))
    assert caught.value.classification is SocialErrorClass.MEDIA


def test_structurally_public_https_media_is_accepted_without_fetching() -> None:
    MockInstagramAdapter(load_instagram_config("config")).validate_content(_request())


def test_malformed_media_url_is_rejected_structurally() -> None:
    with pytest.raises(ValidationError):
        _request(urls=("not a URL", "https://media.example.com/2.jpg"))


def test_wrong_platform_and_format_are_rejected() -> None:
    data = _request().model_dump()
    data["platform"] = "OTHER"
    with pytest.raises(ValidationError):
        InstagramCarouselRequest.model_validate(data)
    data = _request().model_dump()
    data["format"] = "OTHER"
    with pytest.raises(ValidationError):
        InstagramCarouselRequest.model_validate(data)


def test_invalid_media_count_and_unsupported_media_type_are_rejected() -> None:
    adapter = MockInstagramAdapter(load_instagram_config("config"))
    with pytest.raises(SocialAdapterError, match="media count"):
        adapter.validate_content(_request(urls=("https://media.example.com/1.jpg",)))
    video_data = _request().model_dump()
    video_data["media_items"][0]["media_type"] = "VIDEO"
    with pytest.raises(SocialAdapterError) as caught:
        adapter.validate_content(InstagramCarouselRequest.model_validate(video_data))
    assert caught.value.classification is SocialErrorClass.MEDIA


def test_caption_is_never_silently_truncated() -> None:
    adapter = MockInstagramAdapter(load_instagram_config("config"))
    with pytest.raises(SocialAdapterError, match="caption"):
        adapter.validate_content(_request(caption="x" * 2200))


def test_media_positions_must_be_consecutive() -> None:
    data = _request().model_dump()
    data["media_items"][1]["position"] = 3
    with pytest.raises(ValidationError, match="consecutive"):
        InstagramCarouselRequest.model_validate(data)
