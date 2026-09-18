import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from news_ai_collector import CollectedArticle, FeedDefinition, FeedFetchResult
from news_ai_database import Base, SocialAccount, SocialAccountStatus
from news_ai_e2e.runner import (
    BoundedLiveFeedCollector,
    DownloadedMedia,
    LiveE2EError,
    assert_safe_publication_baseline,
    build_live_source_documents,
    download_media,
    ensure_social_account,
    media_for_slides,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


class FakeCollector:
    async def collect(self, definition):
        return FeedFetchResult(
            source_feed_id=definition.source_feed_id,
            articles=[
                CollectedArticle(
                    source_id=definition.source_id,
                    source_feed_id=definition.source_feed_id,
                    url=f"https://example.org/{index}",
                    title=title,
                    published_at=datetime(2026, 9, 18, tzinfo=UTC),
                )
                for index, title in enumerate(
                    ["Other story", "Target story one", "Target story two"], start=1
                )
            ],
        )


def test_real_source_documents_are_bounded_config_not_mock_data():
    registry, feeds = build_live_source_documents(
        ("https://news.example.org/rss.xml", "https://second.example.net/feed")
    )
    assert len(registry["sources"]) == len(feeds["feeds"]) == 2
    assert feeds["feeds"][0]["url"] == "https://news.example.org/rss.xml"
    assert registry["sources"][0]["source_type"] == "NEWS"


def test_bounded_live_collector_filters_and_enforces_global_budget():
    collector = BoundedLiveFeedCollector(
        maximum_articles=1,
        title_contains="target",
        collector=FakeCollector(),
    )
    definition = FeedDefinition(
        source_id=uuid4(),
        source_feed_id=uuid4(),
        name="real",
        url="https://example.org/feed",
    )

    async def scenario():
        first = await collector.collect(definition)
        second = await collector.collect(definition)
        return first, second

    first, second = asyncio.run(scenario())
    assert [article.title for article in first.articles] == ["Target story one"]
    assert second.articles == []


def _jpeg() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"e2e-jpeg-payload" + b"\xff\xd9"


def test_media_download_validates_jpeg_and_hash():
    payload = _jpeg()

    async def scenario():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=payload,
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await download_media(
                ("https://cdn.example.org/test.jpg",),
                maximum_bytes=1024,
                client=client,
            )

    result = asyncio.run(scenario())
    assert result[0].file_hash == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    ("content_type", "payload", "code"),
    [
        ("image/png", _jpeg(), "MEDIA_CONTENT_TYPE"),
        ("image/jpeg", b"not-jpeg", "MEDIA_NOT_JPEG"),
    ],
)
def test_media_download_rejects_wrong_external_media(content_type, payload, code):
    async def scenario():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": content_type},
                content=payload,
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await download_media(
                ("https://cdn.example.org/test.jpg",),
                maximum_bytes=1024,
                client=client,
            )

    with pytest.raises(LiveE2EError) as exc:
        asyncio.run(scenario())
    assert exc.value.code == code


def test_one_media_url_can_fill_carousel_but_partial_lists_are_rejected():
    item = DownloadedMedia("https://cdn.example.org/test.jpg", "a" * 64)
    assert media_for_slides((item,), 3) == (item, item, item)
    with pytest.raises(LiveE2EError, match="exactly one JPEG"):
        media_for_slides((item, item), 3)


def _factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_social_account_is_created_once_and_reused():
    from news_ai_social import SocialSettings

    factory = _factory()
    settings = SocialSettings(
        environment="production",
        social_mode="LIVE",
        publishing_enabled=True,
        instagram_account_id="123456",
        instagram_access_token="secret",
    )
    first = ensure_social_account(factory, settings, account_name="e2e")
    second = ensure_social_account(factory, settings, account_name="e2e")
    assert first == second
    with factory() as session:
        rows = list(session.scalars(select(SocialAccount)))
        assert len(rows) == 1
        assert rows[0].credential_reference == "env:NEWS_AI_INSTAGRAM_ACCESS_TOKEN"


def test_existing_inactive_social_account_is_never_reactivated_by_harness():
    from news_ai_social import SocialSettings

    factory = _factory()
    with factory() as session, session.begin():
        session.add(
            SocialAccount(
                platform="INSTAGRAM",
                account_name="existing",
                account_identifier="123456",
                status=SocialAccountStatus.PAUSED,
                capabilities={"image": True, "carousel": True},
            )
        )
    settings = SocialSettings(
        environment="production",
        social_mode="LIVE",
        publishing_enabled=True,
        instagram_account_id="123456",
        instagram_access_token="secret",
    )
    with pytest.raises(LiveE2EError) as exc:
        ensure_social_account(factory, settings, account_name="e2e")
    assert exc.value.code == "INSTAGRAM_ACCOUNT_UNSAFE"


def test_empty_publication_baseline_is_safe():
    assert_safe_publication_baseline(_factory())
