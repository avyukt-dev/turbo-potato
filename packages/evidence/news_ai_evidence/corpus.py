"""Production search adapter over versioned articles owned by PostgreSQL."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from news_ai_database import Article, ArticleVersion, Source
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .contracts import (
    CandidateSourceType,
    SearchCapability,
    SearchProviderCapabilities,
    SearchQueryFamily,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from .provider import SearchCapabilityError

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STREAM_BATCH_SIZE = 256


@dataclass(frozen=True, slots=True)
class _CorpusCandidate:
    score: int
    url: str
    version_id: UUID
    article_id: UUID
    source_id: UUID
    title: str | None
    snippet: str
    source_name: str
    published_at: datetime | None
    retrieved_at: datetime
    language: str | None
    version_number: int
    content_hash: str

    @property
    def order_key(self) -> tuple[int, str, str]:
        return (-self.score, self.url, str(self.version_id))


class _BoundedCandidates:
    """Exact top-K unique URLs without retaining the scanned corpus."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.by_url: dict[str, _CorpusCandidate] = {}
        self.maximum_size = 0

    def consider(self, candidate: _CorpusCandidate) -> None:
        existing = self.by_url.get(candidate.url)
        if existing is not None:
            if candidate.order_key < existing.order_key:
                self.by_url[candidate.url] = candidate
            return
        if len(self.by_url) < self.limit:
            self.by_url[candidate.url] = candidate
        else:
            worst = max(self.by_url.values(), key=lambda item: item.order_key)
            if candidate.order_key < worst.order_key:
                del self.by_url[worst.url]
                self.by_url[candidate.url] = candidate
        self.maximum_size = max(self.maximum_size, len(self.by_url))

    def ordered(self) -> list[_CorpusCandidate]:
        return sorted(self.by_url.values(), key=lambda item: item.order_key)


class PostgresArticleSearchProvider:
    """Search the latest immutable version of each collected article deterministically."""

    provider_id = "postgres-article-corpus"

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))
        self._capabilities = SearchProviderCapabilities(
            provider_id=self.provider_id,
            capabilities=frozenset({SearchCapability.NEWS}),
            query_families=frozenset(SearchQueryFamily),
            max_results=100,
            supports_domain_filter=True,
            supports_date_filter=True,
        )

    @property
    def capabilities(self) -> SearchProviderCapabilities:
        return self._capabilities

    async def search(self, request: SearchRequest) -> SearchResponse:
        if not self.capabilities.supports(request):
            raise SearchCapabilityError("collected corpus cannot satisfy requested capability")
        query_tokens = _tokens(request.query)
        if not query_tokens:
            return SearchResponse(
                request_id=request.request_id,
                provider_id=self.provider_id,
                retrieved_at=self.clock(),
                results=(),
            )

        results = await asyncio.to_thread(self._search_sync, request, query_tokens)
        return SearchResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            retrieved_at=self.clock(),
            results=results,
        )

    def _search_sync(
        self,
        request: SearchRequest,
        query_tokens: tuple[str, ...],
    ) -> tuple[SearchResult, ...]:
        # The Session is created, consumed and closed inside this worker thread.
        latest = (
            select(
                ArticleVersion.article_id,
                func.max(ArticleVersion.version_number).label("version_number"),
            )
            .group_by(ArticleVersion.article_id)
            .subquery()
        )
        statement = (
            select(
                Article.id,
                Article.source_id,
                Article.canonical_url,
                Article.title,
                Article.language,
                Article.published_at,
                ArticleVersion.id,
                ArticleVersion.version_number,
                ArticleVersion.content_hash,
                ArticleVersion.body,
                ArticleVersion.retrieved_at,
                Source.name,
                Source.domain,
            )
            .join(Source, Source.id == Article.source_id)
            .join(latest, latest.c.article_id == Article.id)
            .join(
                ArticleVersion,
                (ArticleVersion.article_id == latest.c.article_id)
                & (ArticleVersion.version_number == latest.c.version_number),
            )
            .where(Source.is_active.is_(True))
        )
        if request.published_after is not None:
            statement = statement.where(Article.published_at >= request.published_after)
        if request.published_before is not None:
            statement = statement.where(Article.published_at <= request.published_before)
        if request.language:
            base = request.language.casefold().split("-", 1)[0]
            language = func.lower(Article.language)
            statement = statement.where(or_(language == base, language.like(base + "-%")))
        if request.include_domains:
            statement = statement.where(
                or_(
                    Source.domain.is_(None),
                    Source.domain == "",
                    func.lower(Source.domain).in_(request.include_domains),
                )
            )
        if request.exclude_domains:
            statement = statement.where(
                or_(
                    Source.domain.is_(None),
                    func.lower(Source.domain).not_in(request.exclude_domains),
                )
            )

        retained = _BoundedCandidates(request.max_results)
        with self.session_factory() as session:
            rows = session.execute(
                statement.execution_options(
                    stream_results=True,
                    yield_per=_STREAM_BATCH_SIZE,
                )
            )
            for row in rows:
                (
                    article_id,
                    source_id,
                    url,
                    title,
                    language,
                    published_at,
                    version_id,
                    version_number,
                    content_hash,
                    body,
                    retrieved_at,
                    source_name,
                    source_domain,
                ) = row
                if not _matches_filter_values(
                    request,
                    language=language,
                    published_at=published_at,
                    source_domain=source_domain,
                    canonical_url=url,
                ):
                    continue
                material = " ".join(item for item in (title, body) if item)
                score = _lexical_score(query_tokens, material)
                if score == 0:
                    continue
                retained.consider(
                    _CorpusCandidate(
                        score=score,
                        url=url,
                        version_id=version_id,
                        article_id=article_id,
                        source_id=source_id,
                        title=title,
                        snippet=_bounded_snippet(body or title or "", query_tokens),
                        source_name=source_name,
                        published_at=_aware_utc(published_at),
                        retrieved_at=_aware_utc(retrieved_at),
                        language=language,
                        version_number=version_number,
                        content_hash=content_hash,
                    )
                )

        results = []
        for rank, candidate in enumerate(retained.ordered(), start=1):
            results.append(
                SearchResult(
                    url=candidate.url,
                    title=candidate.title,
                    snippet=candidate.snippet,
                    source_name=candidate.source_name,
                    candidate_type=CandidateSourceType.NEWS_ARTICLE,
                    rank=rank,
                    published_at=candidate.published_at,
                    updated_at=candidate.retrieved_at,
                    language=candidate.language,
                    metadata={
                        "source_id": str(candidate.source_id),
                        "article_id": str(candidate.article_id),
                        "article_version_id": str(candidate.version_id),
                        "article_version_number": candidate.version_number,
                        "content_hash": candidate.content_hash,
                        "canonical_url": candidate.url,
                        "published_at": (
                            candidate.published_at.isoformat()
                            if candidate.published_at is not None
                            else None
                        ),
                        "source_retrieved_at": candidate.retrieved_at.isoformat(),
                    },
                )
            )
        return tuple(results)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token.casefold() for token in _TOKEN_RE.findall(value)))


def _lexical_score(query_tokens: tuple[str, ...], material: str) -> int:
    haystack = set(_tokens(material))
    return sum(token in haystack for token in query_tokens)


def _matches_filters(request: SearchRequest, article: Article, source: Source) -> bool:
    return _matches_filter_values(
        request,
        language=article.language,
        published_at=article.published_at,
        source_domain=source.domain,
        canonical_url=article.canonical_url,
    )


def _matches_filter_values(
    request: SearchRequest,
    *,
    language: str | None,
    published_at: datetime | None,
    source_domain: str | None,
    canonical_url: str,
) -> bool:
    if request.language:
        if language is None:
            return False
        requested = request.language.casefold().split("-", 1)[0]
        actual = language.casefold().split("-", 1)[0]
        if requested != actual:
            return False
    published = _aware_utc(published_at)
    if request.published_after is not None and (
        published is None or published < request.published_after
    ):
        return False
    if request.published_before is not None and (
        published is None or published > request.published_before
    ):
        return False
    domain = (source_domain or urlsplit(canonical_url).hostname or "").casefold()
    if request.include_domains and domain not in request.include_domains:
        return False
    return not request.exclude_domains or domain not in request.exclude_domains


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _bounded_snippet(body: str, query_tokens: tuple[str, ...], limit: int = 4_000) -> str:
    normalized = " ".join(body.split())
    if len(normalized) <= limit:
        return normalized
    lowered = normalized.casefold()
    positions = [lowered.find(token) for token in query_tokens if lowered.find(token) >= 0]
    start = max(0, (min(positions) if positions else 0) - 300)
    return normalized[start : start + limit]
