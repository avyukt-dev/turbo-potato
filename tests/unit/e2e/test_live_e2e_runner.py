import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from news_ai_collector import CollectedArticle, FeedDefinition, FeedFetchResult
from news_ai_common.config import AppSettings
from news_ai_database import AIModel, AIRun, Base, SocialAccount, SocialAccountStatus
from news_ai_domain import PublicationStatus
from news_ai_e2e.configuration import LiveE2ESettings
from news_ai_e2e.runner import (
    BoundedLiveFeedCollector,
    DownloadedMedia,
    LiveE2EError,
    LiveE2ERunner,
    assert_groq_provenance,
    assert_only_expected_publication,
    assert_safe_publication_baseline,
    build_live_source_documents,
    download_media,
    ensure_social_account,
    media_for_slides,
)
from news_ai_social import SocialSettings
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


def test_real_source_documents_preserve_publisher_identity_across_same_host_feeds():
    registry, feeds = build_live_source_documents(
        (
            "https://news.example.org/rss.xml",
            "https://news.example.org/world.xml",
            "https://second.example.net/feed",
        )
    )
    assert len(registry["sources"]) == 2
    assert len(feeds["feeds"]) == 3
    assert feeds["feeds"][0]["source_key"] == feeds["feeds"][1]["source_key"]
    assert feeds["feeds"][0]["source_key"] != feeds["feeds"][2]["source_key"]
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


def test_media_download_enforces_byte_bound():
    payload = _jpeg() + b"x" * 64

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
            await download_media(
                ("https://cdn.example.org/test.jpg",),
                maximum_bytes=8,
                client=client,
            )

    with pytest.raises(LiveE2EError) as exc:
        asyncio.run(scenario())
    assert exc.value.code == "MEDIA_TOO_LARGE"


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


@pytest.mark.parametrize(
    ("status", "capabilities"),
    [
        (SocialAccountStatus.PAUSED, {"image": True, "carousel": True}),
        (SocialAccountStatus.ACTIVE, {"image": True, "carousel": False}),
    ],
)
def test_existing_unsafe_social_account_is_never_mutated_by_harness(status, capabilities):
    factory = _factory()
    with factory() as session, session.begin():
        session.add(
            SocialAccount(
                platform="INSTAGRAM",
                account_name="existing",
                account_identifier="123456",
                status=status,
                capabilities=capabilities,
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
    with factory() as session:
        account = session.scalar(select(SocialAccount))
        assert account.status is status
        assert account.capabilities == capabilities


def test_empty_publication_baseline_is_safe():
    assert_safe_publication_baseline(_factory())


def test_active_publication_baseline_is_rejected():
    from unit.publishing.test_scheduler import Clock, request, seed_candidate, stack

    factory = _factory()
    clock = Clock()
    variant, account, actor = seed_candidate(factory)
    service, _ = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    assert row.status is PublicationStatus.SCHEDULED
    with pytest.raises(LiveE2EError) as exc:
        assert_safe_publication_baseline(factory)
    assert exc.value.code == "ACTIVE_PUBLICATION_EXISTS"
    assert_only_expected_publication(factory, row.id)
    with pytest.raises(LiveE2EError) as changed:
        assert_only_expected_publication(factory, uuid4())
    assert changed.value.code == "PUBLICATION_SET_CHANGED"


def _add_ai_run(session, provider: str, task_type: str):
    model = session.scalar(
        select(AIModel).where(
            AIModel.provider == provider,
            AIModel.model_name == f"{provider}-model",
        )
    )
    if model is None:
        model = AIModel(
            provider=provider,
            model_name=f"{provider}-model",
            locality="CLOUD" if provider == "groq" else "LOCAL",
            capabilities={},
        )
        session.add(model)
        session.flush()
    run = AIRun(
        ai_model_id=model.id,
        task_type=task_type,
        status="SUCCEEDED",
        validation_status="VALIDATED",
    )
    session.add(run)
    session.flush()
    return run.id


def test_groq_provenance_requires_every_target_stage_and_ignores_baseline():
    factory = _factory()
    with factory() as session, session.begin():
        baseline = _add_ai_run(session, "local-llama", "CLAIM_EXTRACTION")
        for task_type in (
            "CLAIM_EXTRACTION",
            "CONTENT_GENERATION",
            "EVIDENCE_ASSESSMENT",
            "QUALITY_CHECKING",
        ):
            _add_ai_run(session, "groq", task_type)

    assert_groq_provenance(factory, {baseline})


def test_groq_provenance_rejects_missing_stage_and_provider_fallback():
    missing_factory = _factory()
    with missing_factory() as session, session.begin():
        for task_type in (
            "CLAIM_EXTRACTION",
            "CONTENT_GENERATION",
            "QUALITY_CHECKING",
        ):
            _add_ai_run(session, "groq", task_type)
    with pytest.raises(LiveE2EError) as missing:
        assert_groq_provenance(missing_factory, set())
    assert missing.value.code == "GROQ_PROVENANCE_MISSING"

    fallback_factory = _factory()
    with fallback_factory() as session, session.begin():
        for task_type in (
            "CLAIM_EXTRACTION",
            "CONTENT_GENERATION",
            "EVIDENCE_ASSESSMENT",
        ):
            _add_ai_run(session, "groq", task_type)
        _add_ai_run(session, "local-llama", "QUALITY_CHECKING")
    with pytest.raises(LiveE2EError) as fallback:
        assert_groq_provenance(fallback_factory, set())
    assert fallback.value.code == "GROQ_PROVENANCE_FALLBACK"


def test_api_error_diagnostic_never_copies_provider_or_server_message():
    response = httpx.Response(
        409,
        json={
            "error": {
                "code": "ACCOUNT_INACTIVE",
                "message": "SECRET_SENTINEL",
            }
        },
    )
    with pytest.raises(LiveE2EError) as exc:
        LiveE2ERunner._require_api_response(response, expected={201})
    assert exc.value.code == "API_REQUEST_FAILED"
    assert "ACCOUNT_INACTIVE" in str(exc.value)
    assert "SECRET_SENTINEL" not in str(exc.value)


def test_wait_timeout_is_normalized():
    runner = LiveE2ERunner(
        AppSettings(),
        LiveE2ESettings(
            feed_urls=("https://example.org/feed",),
            media_urls=("https://cdn.example.org/test.jpg",),
            timeout_seconds=0.01,
            poll_interval_seconds=0.001,
        ),
        SocialSettings(),
    )

    async def scenario():
        await runner._wait_until(lambda: None, timeout=0.01)

    with pytest.raises(LiveE2EError) as exc:
        asyncio.run(scenario())
    assert exc.value.code == "TIMEOUT"
