from datetime import UTC, datetime

import pytest
from news_ai_evidence import (
    SearchCapability,
    SearchProviderCapabilities,
    SearchQueryFamily,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from pydantic import ValidationError


def _request(**updates: object) -> SearchRequest:
    request = SearchRequest(
        query="  exact   claim query  ",
        query_family=SearchQueryFamily.EXACT_CLAIM,
        capability=SearchCapability.WEB,
        max_results=5,
    )
    return request.model_copy(update=updates)


def test_request_normalizes_query_domains_and_rejects_secrets() -> None:
    request = SearchRequest(
        query="  exact   claim query  ",
        query_family=SearchQueryFamily.EXACT_CLAIM,
        capability=SearchCapability.WEB,
        include_domains=("Example.COM.", "example.com"),
    )
    assert request.query == "exact claim query"
    assert request.include_domains == ("example.com",)

    with pytest.raises(ValidationError, match="must not contain secrets"):
        SearchRequest(
            query="claim",
            query_family=SearchQueryFamily.EXACT_CLAIM,
            capability=SearchCapability.WEB,
            metadata={"api_token": "secret"},
        )


def test_request_rejects_invalid_filter_ranges_and_domain_overlap() -> None:
    with pytest.raises(ValidationError, match="must not overlap"):
        SearchRequest(
            query="claim",
            query_family=SearchQueryFamily.EXACT_CLAIM,
            capability=SearchCapability.WEB,
            include_domains=("example.com",),
            exclude_domains=("example.com",),
        )

    with pytest.raises(ValidationError, match="published_after"):
        SearchRequest(
            query="claim",
            query_family=SearchQueryFamily.EXACT_CLAIM,
            capability=SearchCapability.WEB,
            published_after=datetime(2026, 9, 11, tzinfo=UTC),
            published_before=datetime(2026, 9, 10, tzinfo=UTC),
        )


def test_search_result_rejects_credentials_and_naive_dates() -> None:
    with pytest.raises(ValidationError, match="credentials"):
        SearchResult(url="https://user:pass@example.com/x", rank=1)

    with pytest.raises(ValidationError, match="timezone-aware"):
        SearchResult(
            url="https://example.com/x",
            rank=1,
            published_at=datetime(2026, 9, 10, 12, 0),
        )


def test_response_rejects_duplicate_candidate_urls() -> None:
    request = _request()
    result = SearchResult(url="https://example.com/a", rank=1)
    with pytest.raises(ValidationError, match="duplicate"):
        SearchResponse(
            request_id=request.request_id,
            provider_id="web-a",
            retrieved_at=datetime.now(UTC),
            results=(result, result),
        )


def test_provider_capabilities_check_filters_limits_and_language() -> None:
    capabilities = SearchProviderCapabilities(
        provider_id="web-a",
        capabilities=frozenset({SearchCapability.WEB}),
        query_families=frozenset({SearchQueryFamily.EXACT_CLAIM}),
        max_results=10,
        supports_domain_filter=True,
        supports_date_filter=False,
        languages=frozenset({"en", "hi"}),
    )
    assert capabilities.supports(_request(language="en-IN", include_domains=("example.com",)))
    assert not capabilities.supports(_request(language="fr"))
    assert not capabilities.supports(
        _request(published_after=datetime(2026, 9, 10, tzinfo=UTC))
    )
