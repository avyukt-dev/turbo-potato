"""One-shot real-source, real-Groq, real-Instagram end-to-end orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
import yaml
from news_ai_collector import (
    FeedFetchResult,
    RSSCollector,
    SourceRegistryLoader,
    SourceRegistryService,
)
from news_ai_common.config import AppSettings, ConfigLoader
from news_ai_content import ContentMediaAttachmentService, MediaAttachmentRequest
from news_ai_database import (
    AIModel,
    AIRun,
    ArticleDiscovery,
    ArticleVersion,
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    EventDeadLetter,
    MediaAsset,
    Publication,
    ReviewDecisionRecord,
    SocialAccount,
    SocialAccountStatus,
    StorySource,
)
from news_ai_domain import PublicationStatus, ReviewState
from news_ai_pipeline import PipelineRunner, build_production_pipeline_stack
from news_ai_publisher import build_production_publisher_stack
from news_ai_publisher.runner import run as run_publisher
from news_ai_runtime import DatabasePublishingControl
from news_ai_scheduler import build_production_scheduler_stack
from news_ai_social import SocialSettings, canonical_public_media_url
from sqlalchemy import select

from .configuration import LiveE2EConfigurationError, LiveE2ESettings

logger = logging.getLogger(__name__)

_TERMINAL_PUBLICATION_STATES = frozenset(
    {
        PublicationStatus.PUBLISHED,
        PublicationStatus.BLOCKED,
        PublicationStatus.FAILED,
        PublicationStatus.CANCELLED,
    }
)
_ACTIVE_PUBLICATION_STATES = frozenset(
    {
        PublicationStatus.SCHEDULED,
        PublicationStatus.PUBLISHING,
        PublicationStatus.RETRYING,
    }
)
_REQUIRED_GROQ_TASKS = frozenset(
    {
        "CLAIM_EXTRACTION",
        "CONTENT_GENERATION",
        "EVIDENCE_ASSESSMENT",
        "QUALITY_CHECKING",
    }
)


class LiveE2EError(RuntimeError):
    """Safe operator-facing E2E failure. Never include secrets or raw provider responses."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    public_url: str
    file_hash: str


@dataclass(frozen=True, slots=True)
class LiveE2EResult:
    outcome: str
    content_variant_id: UUID
    publication_id: UUID | None = None
    external_post_id: str | None = None
    external_url: str | None = None


class BoundedLiveFeedCollector:
    """Real RSS/Atom transport with a global article budget for safe live E2E runs."""

    def __init__(
        self,
        *,
        maximum_articles: int,
        title_contains: str | None = None,
        collector: RSSCollector | None = None,
    ) -> None:
        self.maximum_articles = maximum_articles
        self.title_contains = title_contains.casefold() if title_contains else None
        self.collector = collector or RSSCollector()
        self._selected = 0
        self._lock = asyncio.Lock()

    async def collect(self, definition) -> FeedFetchResult:
        result = await self.collector.collect(definition)
        candidates = result.articles
        if self.title_contains is not None:
            candidates = [
                article
                for article in candidates
                if self.title_contains in article.title.casefold()
            ]
        async with self._lock:
            remaining = max(0, self.maximum_articles - self._selected)
            selected = candidates[:remaining]
            self._selected += len(selected)
        return result.model_copy(update={"articles": selected})


def _stable_key(prefix: str, material: str, *, slug: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "-" for character in slug.casefold()
    )
    normalized = "-".join(part for part in normalized.split("-") if part)[:80] or "source"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{normalized}-{digest}"[:128]


def build_live_source_documents(
    feed_urls: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    feeds: list[dict[str, Any]] = []
    source_keys: dict[str, str] = {}
    for index, feed_url in enumerate(feed_urls, start=1):
        parsed = urlparse(feed_url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if not host:
            raise LiveE2EConfigurationError("feed URL has no hostname")
        source_key = source_keys.get(host)
        if source_key is None:
            source_key = _stable_key("e2e-source", host, slug=host)
            source_keys[host] = source_key
            sources.append(
                {
                    "key": source_key,
                    "name": f"Live E2E {host}",
                    "source_type": "NEWS",
                    "domain": host,
                    "base_url": f"https://{host}",
                    "enabled": True,
                    "metadata": {"live_e2e": True},
                }
            )
        feeds.append(
            {
                "key": _stable_key("e2e-feed", feed_url, slug=f"{host}-{index}"),
                "source_key": source_key,
                "name": f"Live E2E feed {index}",
                "url": feed_url,
                "feed_type": "RSS",
                "poll_interval_seconds": 60,
                "enabled": True,
            }
        )
    return {"schema_version": 1, "sources": sources}, {"schema_version": 1, "feeds": feeds}


def build_isolated_config(base: Path, destination: Path, feed_urls: tuple[str, ...]) -> Path:
    if not base.is_dir():
        raise LiveE2EConfigurationError("NEWS_AI_CONFIG_DIR does not exist")
    shutil.copytree(base, destination)
    registry, feeds = build_live_source_documents(feed_urls)
    sources_dir = destination / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / "registry.yaml").write_text(
        yaml.safe_dump(registry, sort_keys=False), encoding="utf-8"
    )
    (sources_dir / "feeds.yaml").write_text(
        yaml.safe_dump(feeds, sort_keys=False), encoding="utf-8"
    )
    return destination


async def download_media(
    urls: tuple[str, ...],
    *,
    maximum_bytes: int,
    client: httpx.AsyncClient | None = None,
) -> tuple[DownloadedMedia, ...]:
    owned = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, timeout=30)
    results: list[DownloadedMedia] = []
    try:
        for raw_url in urls:
            canonical_public_media_url(raw_url)
            async with client.stream("GET", raw_url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                if content_type != "image/jpeg":
                    raise LiveE2EError("MEDIA_CONTENT_TYPE", "E2E media must return image/jpeg")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > maximum_bytes:
                        raise LiveE2EError("MEDIA_TOO_LARGE", "E2E media exceeded the bounded size")
                final_url = canonical_public_media_url(str(response.url))
            ordinary_jpeg = (
                len(data) >= 4
                and data.startswith(b"\xff\xd8\xff")
                and data.endswith(b"\xff\xd9")
            )
            if not ordinary_jpeg:
                raise LiveE2EError("MEDIA_NOT_JPEG", "E2E media bytes are not an ordinary JPEG")
            results.append(
                DownloadedMedia(
                    public_url=final_url,
                    file_hash=hashlib.sha256(data).hexdigest(),
                )
            )
    finally:
        if owned:
            await client.aclose()
    return tuple(results)


def media_for_slides(
    media: tuple[DownloadedMedia, ...],
    slide_count: int,
) -> tuple[DownloadedMedia, ...]:
    if not 2 <= slide_count <= 10:
        raise LiveE2EError("INVALID_SLIDE_COUNT", "Instagram carousel must contain 2 to 10 slides")
    if len(media) == 1:
        return media * slide_count
    if len(media) != slide_count:
        raise LiveE2EError(
            "MEDIA_COUNT_MISMATCH",
            "Provide one reusable JPEG or exactly one JPEG URL per generated slide",
        )
    return media


def persist_and_attach_media(factory, variant_id: UUID, media: tuple[DownloadedMedia, ...]) -> None:
    with factory() as session:
        variant = session.get(ContentVariant, variant_id)
        if variant is None:
            raise LiveE2EError("VARIANT_MISSING", "Selected content variant disappeared")
        version = variant.version
        payload = variant.structured_payload if isinstance(variant.structured_payload, dict) else {}
        slides = payload.get("slides")
        slide_count = len(slides) if isinstance(slides, list) else 0
    selected = media_for_slides(media, slide_count)
    ids = []
    with factory() as session, session.begin():
        for position, item in enumerate(selected, start=1):
            asset = MediaAsset(
                asset_type="IMAGE",
                storage_provider="live-e2e-public-url",
                storage_key=f"live-e2e/{item.file_hash}/{position}.jpg",
                public_url=item.public_url,
                mime_type="image/jpeg",
                file_hash=item.file_hash,
                visual_check_status="VALIDATED",
                source_metadata={
                    "media_format": "JPEG",
                    "live_e2e": True,
                    "position": position,
                },
            )
            session.add(asset)
            session.flush()
            ids.append(asset.id)
    ContentMediaAttachmentService(factory).attach_media(
        MediaAttachmentRequest(
            content_variant_id=variant_id,
            expected_content_variant_version=version,
            ordered_media_asset_ids=tuple(ids),
        )
    )


def ensure_social_account(factory, social: SocialSettings, *, account_name: str) -> UUID:
    identifier = social.instagram_account_id
    if identifier is None:
        raise LiveE2EError("INSTAGRAM_ACCOUNT_MISSING", "Instagram account ID is unavailable")
    with factory() as session, session.begin():
        account = session.scalar(
            select(SocialAccount)
            .where(
                SocialAccount.platform == "INSTAGRAM",
                SocialAccount.account_identifier == identifier,
            )
            .with_for_update()
        )
        if account is None:
            account = SocialAccount(
                platform="INSTAGRAM",
                account_name=account_name,
                account_identifier=identifier,
                status=SocialAccountStatus.ACTIVE,
                credential_reference="env:NEWS_AI_INSTAGRAM_ACCESS_TOKEN",
                capabilities={"image": True, "carousel": True},
                rate_limit_state={},
                account_metadata={"managed_by": "live-e2e-runner"},
            )
            session.add(account)
            session.flush()
        elif (
            account.status is not SocialAccountStatus.ACTIVE
            or not isinstance(account.capabilities, dict)
            or account.capabilities.get("image") is not True
            or account.capabilities.get("carousel") is not True
        ):
            raise LiveE2EError(
                "INSTAGRAM_ACCOUNT_UNSAFE",
                "Existing Instagram destination is not ACTIVE with image/carousel capability",
            )
        return account.id


def _active_publication_ids(factory) -> set[UUID]:
    with factory() as session:
        return set(
            session.scalars(
                select(Publication.id).where(
                    Publication.status.in_(tuple(_ACTIVE_PUBLICATION_STATES))
                )
            )
        )


def assert_safe_publication_baseline(factory) -> None:
    if _active_publication_ids(factory):
        raise LiveE2EError(
            "ACTIVE_PUBLICATION_EXISTS",
            "Refusing to unpause publishing while a pre-existing executable publication exists",
        )


def assert_only_expected_publication(factory, publication_id: UUID) -> None:
    if _active_publication_ids(factory) != {publication_id}:
        raise LiveE2EError(
            "PUBLICATION_SET_CHANGED",
            "Executable publication set changed during the bounded live window",
        )


def assert_groq_provenance(factory, baseline_ai_runs: set[UUID]) -> None:
    with factory() as session:
        statement = (
            select(AIRun.task_type, AIModel.provider)
            .join(AIModel, AIModel.id == AIRun.ai_model_id)
            .where(AIRun.task_type.in_(tuple(_REQUIRED_GROQ_TASKS)))
        )
        if baseline_ai_runs:
            statement = statement.where(AIRun.id.not_in(baseline_ai_runs))
        rows = tuple(session.execute(statement))

    observed = {task_type for task_type, _ in rows}
    missing = sorted(_REQUIRED_GROQ_TASKS - observed)
    if missing:
        raise LiveE2EError(
            "GROQ_PROVENANCE_MISSING",
            "Live E2E did not persist every required Groq-backed AI stage: "
            + ",".join(missing),
        )

    fallback = sorted(
        {task_type for task_type, provider in rows if provider.casefold() != "groq"}
    )
    if fallback:
        raise LiveE2EError(
            "GROQ_PROVENANCE_FALLBACK",
            "Live E2E used a non-Groq provider for: " + ",".join(fallback),
        )


def _new_dead_letter(factory, baseline: set[UUID]) -> EventDeadLetter | None:
    with factory() as session:
        statement = select(EventDeadLetter).order_by(
            EventDeadLetter.failed_at, EventDeadLetter.id
        )
        if baseline:
            statement = statement.where(EventDeadLetter.id.not_in(baseline))
        return session.scalar(statement.limit(1))


def restore_source_registry(factory, config_root: Path) -> None:
    snapshot = SourceRegistryLoader(ConfigLoader(config_root)).load()
    with factory() as session, session.begin():
        SourceRegistryService(session).sync(snapshot)


def _review_command(base_url: str, variant: ContentVariant) -> str:
    key = f"live-e2e-review-{variant.id}-{variant.version}"
    return (
        "curl -X POST "
        f"'{base_url}/api/v1/review/content_variant/{variant.id}/approve' "
        '-H "Authorization: Bearer $NEWS_AI_REVIEW_API_TOKEN" '
        f'-H "Idempotency-Key: {key}" '
        '-H "Content-Type: application/json" '
        f"-d '{{\"artifact_version\":{variant.version},\"reason\":null}}'"
    )


class LiveE2ERunner:
    def __init__(
        self,
        app_settings: AppSettings,
        e2e_settings: LiveE2ESettings,
        social_settings: SocialSettings,
    ) -> None:
        self.app_settings = app_settings
        self.e2e = e2e_settings
        self.social = social_settings
        self.run_id = uuid4().hex[:12]

    def _api_client(self) -> httpx.AsyncClient:
        token = self.app_settings.review_api_token
        if token is None:
            raise LiveE2EError("REVIEW_AUTH_MISSING", "Review API token is unavailable")
        return httpx.AsyncClient(
            base_url=self.e2e.api_base_url,
            timeout=10,
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        )

    async def _preflight_api(self) -> None:
        try:
            async with self._api_client() as client:
                health = await client.get("/health")
                if health.status_code != 200:
                    raise LiveE2EError("API_UNHEALTHY", "Local API health check failed")
                ready = await client.get("/ready")
                if ready.status_code != 200:
                    raise LiveE2EError(
                        "API_NOT_READY",
                        "Local API dependencies or AI routing are not ready",
                    )
                queue = await client.get("/api/v1/review/queue", params={"limit": 1})
                if queue.status_code != 200:
                    raise LiveE2EError(
                        "REVIEW_API_UNAVAILABLE",
                        "Local review API/authentication preflight failed",
                    )
        except httpx.RequestError:
            raise LiveE2EError("API_UNAVAILABLE", "Local API is unavailable") from None

    async def _wait_pipeline_ready(self, pipeline, pipeline_task: asyncio.Task) -> None:
        ready_task = asyncio.create_task(pipeline.ready_event.wait())
        try:
            done, _ = await asyncio.wait(
                (ready_task, pipeline_task),
                timeout=30,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ready_task in done and ready_task.result():
                return
            if pipeline_task in done:
                raise LiveE2EError(
                    "PIPELINE_STARTUP_FAILED",
                    "Production pipeline exited before becoming ready",
                )
            raise LiveE2EError(
                "PIPELINE_STARTUP_TIMEOUT",
                "Production pipeline did not become ready within 30 seconds",
            )
        finally:
            if not ready_task.done():
                ready_task.cancel()
                await asyncio.gather(ready_task, return_exceptions=True)

    async def _wait_until(self, predicate, *, timeout: float | None = None):
        deadline = timeout or self.e2e.timeout_seconds
        try:
            async with asyncio.timeout(deadline):
                while True:
                    value = predicate()
                    if value:
                        return value
                    await asyncio.sleep(self.e2e.poll_interval_seconds)
        except TimeoutError:
            raise LiveE2EError(
                "TIMEOUT",
                "Timed out waiting for the current live E2E stage",
            ) from None

    async def _wait_target(
        self,
        factory,
        baseline_discoveries: set[UUID],
        baseline_variants: set[UUID],
        baseline_dead_letters: set[UUID],
        pipeline_task: asyncio.Task,
    ) -> ContentVariant:
        def probe():
            if pipeline_task.done():
                raise LiveE2EError(
                    "PIPELINE_STOPPED",
                    "Production pipeline exited before producing target content",
                )
            dead = _new_dead_letter(factory, baseline_dead_letters)
            if dead is not None:
                raise LiveE2EError(
                    "DEAD_LETTER",
                    f"Pipeline dead-lettered work in consumer {dead.consumer_group}",
                )
            with factory() as session:
                statement = (
                    select(ContentVariant, ArticleDiscovery)
                    .join(
                        ContentDraft,
                        ContentDraft.id == ContentVariant.content_draft_id,
                    )
                    .join(StorySource, StorySource.story_id == ContentDraft.story_id)
                    .join(
                        ArticleDiscovery,
                        ArticleDiscovery.article_id == StorySource.article_id,
                    )
                    .order_by(
                        ContentVariant.created_at,
                        ContentVariant.id,
                        ArticleDiscovery.created_at,
                        ArticleDiscovery.id,
                    )
                )
                if baseline_variants:
                    statement = statement.where(
                        ContentVariant.id.not_in(baseline_variants)
                    )
                if baseline_discoveries:
                    statement = statement.where(
                        ArticleDiscovery.id.not_in(baseline_discoveries)
                    )
                row = session.execute(statement.limit(1)).first()
                if row is None:
                    return None
                variant, discovery = row
                version = (
                    session.get(ArticleVersion, discovery.normalized_article_version_id)
                    if discovery.normalized_article_version_id is not None
                    else None
                )
                if version is None or not version.body or not version.body.strip():
                    raise LiveE2EError(
                        "ARTICLE_BODY_MISSING",
                        "Target content was generated without a persisted article body",
                    )
                return variant

        return await self._wait_until(probe)

    async def _wait_review_ready(
        self,
        factory,
        variant_id: UUID,
        baseline_dead_letters: set[UUID],
        pipeline_task: asyncio.Task,
    ) -> ContentVariant:
        def probe():
            if pipeline_task.done():
                raise LiveE2EError(
                    "PIPELINE_STOPPED", "Production pipeline exited before quality completion"
                )
            dead = _new_dead_letter(factory, baseline_dead_letters)
            if dead is not None:
                raise LiveE2EError(
                    "DEAD_LETTER",
                    f"Pipeline dead-lettered work in consumer {dead.consumer_group}",
                )
            with factory() as session:
                variant = session.get(ContentVariant, variant_id)
                if variant is None:
                    raise LiveE2EError("VARIANT_MISSING", "Selected content variant disappeared")
                check = session.scalar(
                    select(ContentQualityCheck)
                    .where(
                        ContentQualityCheck.content_variant_id == variant.id,
                        ContentQualityCheck.content_variant_version == variant.version,
                    )
                    .order_by(ContentQualityCheck.created_at.desc())
                    .limit(1)
                )
                if check is not None and not check.passed:
                    raise LiveE2EError(
                        "QUALITY_REJECTED",
                        "Quality Gate rejected the target variant",
                    )
                if variant.review_state is ReviewState.READY_FOR_REVIEW:
                    return variant
                return None

        return await self._wait_until(probe)

    async def _wait_human_decision(self, factory, variant: ContentVariant) -> ReviewDecisionRecord:
        print("\nHuman approval required.")
        print(f"Artifact: content_variant/{variant.id} version {variant.version}")
        print("Review it through the API/UI. To approve from this shell:")
        print(_review_command(self.e2e.api_base_url, variant))

        def probe():
            with factory() as session:
                return session.scalar(
                    select(ReviewDecisionRecord)
                    .where(
                        ReviewDecisionRecord.artifact_id == variant.id,
                        ReviewDecisionRecord.artifact_version == variant.version,
                    )
                    .limit(1)
                )

        return await self._wait_until(probe)

    async def _create_and_publish(
        self,
        factory,
        run_settings: AppSettings,
        variant: ContentVariant,
    ) -> LiveE2EResult:
        account_id = ensure_social_account(
            factory,
            self.social,
            account_name=self.e2e.account_name,
        )
        control = DatabasePublishingControl(factory)
        before = control.snapshot()
        if not before.available or before.database_pause is not True:
            raise LiveE2EError(
                "PUBLISHING_CONTROL_UNSAFE",
                "Live E2E requires the durable publishing control to start paused",
            )
        assert_safe_publication_baseline(factory)

        scheduler_stack = None
        publisher_stack = None
        publisher_stop = asyncio.Event()
        publisher_task: asyncio.Task | None = None
        unpaused = False
        publication_id: UUID | None = None
        try:
            scheduler_stack = build_production_scheduler_stack(run_settings)
            publisher_stack = build_production_publisher_stack(
                run_settings,
                social_settings=self.social,
            )
            async with self._api_client() as client:
                create_key = f"live-e2e-create-{variant.id}-{variant.version}"
                response = await client.post(
                    "/api/v1/publications",
                    json={
                        "content_variant_id": str(variant.id),
                        "social_account_id": str(account_id),
                        "platform": "INSTAGRAM",
                        "scheduled_at": None,
                        "idempotency_key": create_key,
                    },
                )
                publication = self._require_api_response(response, expected={201})
                publication_id = UUID(publication["id"])
                publish_key = f"live-e2e-publish-{publication_id}"
                response = await client.post(
                    f"/api/v1/publications/{publication_id}/publish-now",
                    headers={"Idempotency-Key": publish_key},
                )
                self._require_api_response(response, expected={200})

            assert_only_expected_publication(factory, publication_id)
            control.set_paused(False, reason=f"Live E2E {self.run_id} publication window")
            unpaused = True

            dispatched = await asyncio.to_thread(scheduler_stack.scheduler.scan)
            if dispatched != 1:
                raise LiveE2EError(
                    "SCHEDULER_DISPATCH",
                    f"Expected exactly one publication dispatch, got {dispatched}",
                )
            assert_only_expected_publication(factory, publication_id)
            with factory() as session:
                scheduled = session.get(Publication, publication_id)
                if scheduled is None or scheduled.scheduled_event_id is None:
                    raise LiveE2EError(
                        "SCHEDULER_WRONG_PUBLICATION",
                        "Scheduler did not dispatch the selected E2E publication",
                    )

            publisher_task = asyncio.create_task(
                run_publisher(publisher_stack, should_stop=publisher_stop.is_set),
                name="live-e2e-publisher",
            )

            def terminal():
                with factory() as session:
                    return session.get(Publication, publication_id)

            row = await self._wait_until(
                lambda: (
                    item
                    if (item := terminal()) is not None
                    and item.status in _TERMINAL_PUBLICATION_STATES
                    else None
                ),
                timeout=self.e2e.timeout_seconds,
            )
            if row.status is PublicationStatus.PUBLISHED:
                return LiveE2EResult(
                    outcome="PUBLISHED",
                    content_variant_id=variant.id,
                    publication_id=row.id,
                    external_post_id=row.external_post_id,
                    external_url=row.external_url,
                )
            reason = row.failure_reason or row.blocking_reason or "UNKNOWN"
            raise LiveE2EError(
                f"PUBLICATION_{row.status.value}",
                f"Publication ended in {row.status.value} ({reason})",
            )
        finally:
            publisher_stop.set()
            if publisher_task is not None:
                with suppress(Exception):
                    await asyncio.wait_for(publisher_task, timeout=30)
            if unpaused:
                with suppress(Exception):
                    control.set_paused(True, reason=f"Live E2E {self.run_id} safety pause")
            if publisher_stack is not None and publisher_task is None:
                from news_ai_publisher.runner import close_stack

                with suppress(Exception):
                    await close_stack(publisher_stack)
            if scheduler_stack is not None:
                engine = scheduler_stack.service.session_factory.kw.get("bind")
                if engine is not None:
                    engine.dispose()

    @staticmethod
    def _require_api_response(response: httpx.Response, *, expected: set[int]) -> dict[str, Any]:
        if response.status_code not in expected:
            code = "HTTP_ERROR"
            with suppress(ValueError, TypeError, AttributeError):
                code = response.json().get("error", {}).get("code") or code
            raise LiveE2EError("API_REQUEST_FAILED", f"Local API rejected the request: {code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise LiveE2EError("API_RESPONSE_INVALID", "Local API returned an invalid response")
        return payload

    async def run(self) -> LiveE2EResult:
        self.e2e.validate_application(self.app_settings, self.social)
        await self._preflight_api()
        downloaded_media = await download_media(
            self.e2e.media_urls,
            maximum_bytes=self.e2e.media_max_bytes,
        )

        with tempfile.TemporaryDirectory(prefix="news-ai-live-e2e-") as temporary:
            config_root = build_isolated_config(
                Path(self.app_settings.config_dir),
                Path(temporary) / "config",
                self.e2e.feed_urls,
            )
            run_settings = self.app_settings.model_copy(update={"config_dir": config_root})
            collector = BoundedLiveFeedCollector(
                maximum_articles=self.e2e.max_articles_total,
                title_contains=self.e2e.article_title_contains,
            )
            stack = await build_production_pipeline_stack(
                run_settings,
                feed_collector=collector,
            )
            factory = stack.session_factory
            with factory() as session:
                baseline_discoveries = set(session.scalars(select(ArticleDiscovery.id)))
                baseline_variants = set(session.scalars(select(ContentVariant.id)))
                baseline_dead_letters = set(session.scalars(select(EventDeadLetter.id)))
                baseline_ai_runs = set(session.scalars(select(AIRun.id)))
            control = DatabasePublishingControl(factory)
            snapshot = control.snapshot()
            if not snapshot.available or snapshot.database_pause is not True:
                await stack.close()
                raise LiveE2EError(
                    "PUBLISHING_CONTROL_UNSAFE",
                    "Live E2E requires the durable publishing control to start paused",
                )
            assert_safe_publication_baseline(factory)

            pipeline = PipelineRunner(stack)
            pipeline_task = asyncio.create_task(pipeline.run(), name="live-e2e-pipeline")
            try:
                await self._wait_pipeline_ready(pipeline, pipeline_task)
                target = await self._wait_target(
                    factory,
                    baseline_discoveries,
                    baseline_variants,
                    baseline_dead_letters,
                    pipeline_task,
                )
                persist_and_attach_media(factory, target.id, downloaded_media)
                target = await self._wait_review_ready(
                    factory,
                    target.id,
                    baseline_dead_letters,
                    pipeline_task,
                )
                assert_groq_provenance(factory, baseline_ai_runs)
                decision = await self._wait_human_decision(factory, target)
                if decision.decision is not ReviewState.APPROVED:
                    return LiveE2EResult(
                        outcome=decision.decision.value,
                        content_variant_id=target.id,
                    )
                return await self._create_and_publish(factory, run_settings, target)
            finally:
                pipeline.stop_event.set()
                try:
                    await asyncio.wait_for(pipeline_task, timeout=30)
                except TimeoutError:
                    pipeline_task.cancel()
                    await asyncio.gather(pipeline_task, return_exceptions=True)
                except Exception:
                    pass
                try:
                    restore_source_registry(
                        factory,
                        Path(self.app_settings.config_dir),
                    )
                except Exception:
                    logger.error("live E2E source-registry restoration did not complete")
                finally:
                    stack.engine.dispose()


async def run_live_e2e() -> LiveE2EResult:
    app = AppSettings()
    e2e = LiveE2ESettings.from_env()
    social = SocialSettings()
    return await LiveE2ERunner(app, e2e, social).run()
