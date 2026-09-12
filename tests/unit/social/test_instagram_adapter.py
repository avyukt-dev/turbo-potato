from __future__ import annotations

import asyncio
from collections import deque
from uuid import uuid4

import pytest
from news_ai_content import ContentFormat, ContentPlatform
from news_ai_social import (
    GraphResponse,
    GraphTransportError,
    InstagramAdapter,
    InstagramCarouselRequest,
    InstagramMediaItem,
    InstagramPollingConfig,
    PublicationVerificationStatus,
    SocialAdapterError,
    SocialErrorClass,
    SocialMediaType,
    load_instagram_config,
)


class FakeTransport:
    def __init__(self, responses: list[GraphResponse | Exception]) -> None:
        self.responses = deque(responses)
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    async def post(self, path: str, *, data: dict[str, str]) -> GraphResponse:
        self.calls.append(("POST", path, data))
        return self._next()

    async def get(self, path: str, *, params: dict[str, str]) -> GraphResponse:
        self.calls.append(("GET", path, params))
        return self._next()

    def _next(self) -> GraphResponse:
        value = self.responses.popleft()
        if isinstance(value, Exception):
            raise value
        return value


def _ok(payload: dict[str, object], status: int = 200) -> GraphResponse:
    return GraphResponse(status_code=status, payload=payload)


def _request() -> InstagramCarouselRequest:
    return InstagramCarouselRequest(
        content_variant_id=uuid4(),
        content_variant_version=1,
        platform=ContentPlatform.INSTAGRAM,
        format=ContentFormat.CAROUSEL,
        caption="Approved caption",
        hashtags=("#One", "#Two"),
        media_items=(
            InstagramMediaItem(
                position=1,
                public_url="https://media.example.com/one.jpg",
                media_type=SocialMediaType.IMAGE,
            ),
            InstagramMediaItem(
                position=2,
                public_url="https://media.example.com/two.jpg",
                media_type=SocialMediaType.IMAGE,
            ),
        ),
    )


async def _no_sleep(_: float) -> None:
    return None


def test_official_carousel_sequence_order_caption_and_verification() -> None:
    transport = FakeTransport(
        [
            _ok({"id": "101"}),
            _ok({"status_code": "FINISHED"}),
            _ok({"id": "102"}),
            _ok({"status_code": "IN_PROGRESS"}),
            _ok({"status_code": "FINISHED"}),
            _ok({"id": "201"}),
            _ok({"status_code": "FINISHED"}),
            _ok({"id": "301"}),
            _ok(
                {
                    "id": "301",
                    "permalink": "https://www.instagram.com/p/example/",
                    "media_type": "CAROUSEL_ALBUM",
                    "timestamp": "2026-09-12T00:00:00+0000",
                }
            ),
        ]
    )
    adapter = InstagramAdapter(
        load_instagram_config("config"),
        account_id="123456",
        transport=transport,
        sleeper=_no_sleep,
    )
    result = asyncio.run(adapter.publish(_request()))

    assert result.external_post_id == "301"
    assert str(result.external_url) == "https://www.instagram.com/p/example/"
    assert [call[:2] for call in transport.calls] == [
        ("POST", "123456/media"),
        ("GET", "101"),
        ("POST", "123456/media"),
        ("GET", "102"),
        ("GET", "102"),
        ("POST", "123456/media"),
        ("GET", "201"),
        ("POST", "123456/media_publish"),
        ("GET", "301"),
    ]
    assert transport.calls[0][2] == {
        "image_url": "https://media.example.com/one.jpg",
        "is_carousel_item": "true",
    }
    assert transport.calls[5][2] == {
        "media_type": "CAROUSEL",
        "children": "101,102",
        "caption": "Approved caption\n\n#One #Two",
    }
    assert transport.calls[7][2] == {"creation_id": "201"}


def test_processing_failure_and_timeout_are_typed() -> None:
    failed = InstagramAdapter(
        load_instagram_config("config"),
        account_id="123",
        transport=FakeTransport([_ok({"id": "101"}), _ok({"status_code": "ERROR"})]),
        sleeper=_no_sleep,
    )
    with pytest.raises(SocialAdapterError) as caught:
        asyncio.run(failed.publish(_request()))
    assert caught.value.classification is SocialErrorClass.MEDIA

    config = load_instagram_config("config").model_copy(
        update={"polling": InstagramPollingConfig(interval_seconds=0.01, max_attempts=1)}
    )
    timed_out = InstagramAdapter(
        config,
        account_id="123",
        transport=FakeTransport([_ok({"id": "101"}), _ok({"status_code": "IN_PROGRESS"})]),
        sleeper=_no_sleep,
    )
    with pytest.raises(SocialAdapterError) as caught:
        asyncio.run(timed_out.publish(_request()))
    assert caught.value.classification is SocialErrorClass.TRANSIENT


@pytest.mark.parametrize(
    ("status", "payload", "classification"),
    [
        (400, {"error": {"code": 100}}, SocialErrorClass.VALIDATION),
        (401, {"error": {"code": 190}}, SocialErrorClass.AUTHENTICATION),
        (403, {"error": {"code": 10}}, SocialErrorClass.PERMISSION),
        (404, {"error": {"code": 803}}, SocialErrorClass.NOT_FOUND),
        (429, {"error": {"code": 4}}, SocialErrorClass.RATE_LIMIT),
        (503, {"error": {"code": 2}}, SocialErrorClass.TRANSIENT),
    ],
)
def test_graph_failures_are_classified(
    status: int, payload: dict[str, object], classification: SocialErrorClass
) -> None:
    adapter = InstagramAdapter(
        load_instagram_config("config"),
        account_id="123",
        transport=FakeTransport([_ok(payload, status)]),
        sleeper=_no_sleep,
    )
    with pytest.raises(SocialAdapterError) as caught:
        asyncio.run(adapter.publish(_request()))
    assert caught.value.classification is classification


def test_post_send_unknown_outcome_is_ambiguous_and_not_retried() -> None:
    transport = FakeTransport([GraphTransportError("lost response", outcome_may_be_ambiguous=True)])
    adapter = InstagramAdapter(
        load_instagram_config("config"),
        account_id="123",
        transport=transport,
        sleeper=_no_sleep,
    )
    with pytest.raises(SocialAdapterError) as caught:
        asyncio.run(adapter.publish(_request()))
    assert caught.value.classification is SocialErrorClass.AMBIGUOUS
    assert caught.value.outcome_may_be_ambiguous is True
    assert len(transport.calls) == 1


def test_partial_carousel_failure_stops_before_parent_or_publish() -> None:
    transport = FakeTransport(
        [
            _ok({"id": "101"}),
            _ok({"status_code": "FINISHED"}),
            _ok({"error": {"code": 2}}, 503),
        ]
    )
    adapter = InstagramAdapter(
        load_instagram_config("config"),
        account_id="123",
        transport=transport,
        sleeper=_no_sleep,
    )
    with pytest.raises(SocialAdapterError) as caught:
        asyncio.run(adapter.publish(_request()))
    assert caught.value.classification is SocialErrorClass.TRANSIENT
    assert [path for _, path, _ in transport.calls] == [
        "123/media",
        "101",
        "123/media",
    ]


def test_pre_request_connect_failure_is_transient() -> None:
    transport = FakeTransport([GraphTransportError("offline", outcome_may_be_ambiguous=False)])
    adapter = InstagramAdapter(
        load_instagram_config("config"),
        account_id="123",
        transport=transport,
        sleeper=_no_sleep,
    )
    with pytest.raises(SocialAdapterError) as caught:
        asyncio.run(adapter.publish(_request()))
    assert caught.value.classification is SocialErrorClass.TRANSIENT


def test_untrusted_provider_identifier_and_code_cannot_leak_as_result_or_error() -> None:
    sentinel = "SUPER_SECRET_INSTAGRAM_TOKEN_123"
    transport = FakeTransport([_ok({"id": sentinel, "error": {"code": sentinel}})])
    adapter = InstagramAdapter(
        load_instagram_config("config"),
        account_id="123",
        transport=transport,
        sleeper=_no_sleep,
    )
    with pytest.raises(SocialAdapterError) as caught:
        asyncio.run(adapter.publish(_request()))
    assert sentinel not in str(caught.value)
    assert sentinel not in repr(caught.value)
    assert sentinel not in str(caught.value.safe_metadata)


def test_verification_distinguishes_not_found_processing_failed_and_unknown() -> None:
    transport = FakeTransport(
        [
            _ok({"error": {"code": 803}}, 404),
            _ok({"id": "401", "status_code": "IN_PROGRESS"}),
            _ok({"id": "401", "status_code": "ERROR"}),
            _ok({"unexpected": True}),
        ]
    )
    adapter = InstagramAdapter(
        load_instagram_config("config"), account_id="123", transport=transport
    )
    results = [
        asyncio.run(adapter.verify_publication("999")),
        asyncio.run(adapter.verify_publication("401")),
        asyncio.run(adapter.verify_publication("401")),
        asyncio.run(adapter.verify_publication("401")),
    ]
    assert [result.status for result in results] == [
        PublicationVerificationStatus.NOT_FOUND,
        PublicationVerificationStatus.PROCESSING,
        PublicationVerificationStatus.FAILED,
        PublicationVerificationStatus.UNKNOWN,
    ]
