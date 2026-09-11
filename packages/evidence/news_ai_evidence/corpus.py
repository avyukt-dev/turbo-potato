"""Production search adapter over versioned articles owned by PostgreSQL."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from news_ai_database import Article, ArticleVersion, Source
from sqlalchemy import func, select
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


class PostgresArticleSearchProvider:
    """Search the latest immutable version of each collected article deterministically."""

    provider_id = "postgres-article-corpus"

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
        scan_limit: int = 2_000,
    ) -> None:
        if scan_limit < 1:
            raise ValueError("corpus scan_limit must be positive")
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))
        self.scan_limit = scan_limit
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

        latest = (
            select(
                ArticleVersion.article_id,
                func.max(ArticleVersion.version_number).label("version_number"),
            )
            .group_by(ArticleVersion.article_id)
            .subquery()
        )
        with self.session_factory() as session:
            rows = list(
                session.execute(
                    select(Article, ArticleVersion, Source)
                    .join(Source, Source.id == Article.source_id)
                    .join(latest, latest.c.article_id == Article.id)
                    .join(
                        ArticleVersion,
                        (ArticleVersion.article_id == latest.c.article_id)
                        & (ArticleVersion.version_number == latest.c.version_number),
                    )
                    .where(Source.is_active.is_(True))
                    .order_by(Article.id, ArticleVersion.id)
                    .limit(self.scan_limit)
                )
            )

        ranked: list[tuple[int, str, str, SearchResult]] = []
        for article, version, source in rows:
            if not _matches_filters(request, article, source):
                continue
            material = " ".join(item for item in (article.title, version.body) if item)
            score = _lexical_score(query_tokens, material)
            if score == 0:
                continue
            snippet = _bounded_snippet(version.body or article.title or "", query_tokens)
            result = SearchResult(
                url=article.canonical_url,
                title=article.title,
                snippet=snippet,
                source_name=source.name,
                candidate_type=CandidateSourceType.NEWS_ARTICLE,
                rank=1,
                published_at=_aware_utc(article.published_at),
                updated_at=_aware_utc(version.retrieved_at),
                language=article.language,
                metadata={
                    "source_id": str(source.id),
                    "article_id": str(article.id),
                    "article_version_id": str(version.id),
                    "article_version_number": version.version_number,
                    "content_hash": version.content_hash,
                    "canonical_url": article.canonical_url,
                    "published_at": (
                        _aware_utc(article.published_at).isoformat()
                        if article.published_at is not None
                        else None
                    ),
                    "source_retrieved_at": _aware_utc(version.retrieved_at).isoformat(),
                },
            )
            ranked.append((score, article.canonical_url, str(version.id), result))

        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        deduplicated: list[SearchResult] = []
        seen_urls: set[str] = set()
        for _, url, _, result in ranked:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            deduplicated.append(result.model_copy(update={"rank": len(deduplicated) + 1}))
            if len(deduplicated) == request.max_results:
                break
        return SearchResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            retrieved_at=self.clock(),
            results=tuple(deduplicated),
        )


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token.casefold() for token in _TOKEN_RE.findall(value)))


def _lexical_score(query_tokens: tuple[str, ...], material: str) -> int:
    haystack = set(_tokens(material))
    return sum(token in haystack for token in query_tokens)


def _matches_filters(request: SearchRequest, article: Article, source: Source) -> bool:
    if request.language:
        if article.language is None:
            return False
        requested = request.language.casefold().split("-", 1)[0]
        actual = article.language.casefold().split("-", 1)[0]
        if requested != actual:
            return False
    published = _aware_utc(article.published_at)
    if request.published_after is not None and (
        published is None or published < request.published_after
    ):
        return False
    if request.published_before is not None and (
        published is None or published > request.published_before
    ):
        return False
    domain = (source.domain or urlsplit(article.canonical_url).hostname or "").casefold()
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
