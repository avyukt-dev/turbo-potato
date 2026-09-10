from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from news_ai_evidence import (
    SearchCapability,
    SearchCapabilityError,
    SearchInvalidResponseError,
    SearchProviderCapabilities,
    SearchProviderRegistry,
    SearchQueryFamily,
    SearchRequest,
    SearchResponse,
    SearchResult,
)


@dataclass
class FakeProvider:
    provider_id: str
    response_provider_id: str | None = None

    @property
    def capabilities(self) -> SearchProviderCapabilities:
        return SearchProviderCapabilities(
            provider_id=self.provider_id,
            capabilities=frozenset({SearchCapability.WEB}),
            query_families=frozenset({SearchQueryFamily.EXACT_CLAIM}),
            max_results=5,
        )

    async def search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(
            request_id=request.request_id,
            provider_id=self.response_provider_id or self.provider_id,
            retrieved_at=datetime.now(UTC),
            results=(SearchResult(url="https://example.com/a", rank=1),),
        )


def _request(**updates: object) -> SearchRequest:
    request = SearchRequest(
        query="claim",
        query_family=SearchQueryFamily.EXACT_CLAIM,
        capability=SearchCapability.WEB,
        max_results=5,
    )
    return request.model_copy(update=updates)


def test_registry_reports_compatible_providers_and_executes_explicit_choice() -> None:
    registry = SearchProviderRegistry([FakeProvider("web-b"), FakeProvider("web-a")])
    assert registry.compatible_provider_ids(_request()) == ("web-a", "web-b")

    response = asyncio.run(registry.execute("web-a", _request()))
    assert response.provider_id == "web-a"


def test_registry_rejects_provider_identity_mismatch() -> None:
    registry = SearchProviderRegistry([FakeProvider("web-a", response_provider_id="other")])
    with pytest.raises(SearchInvalidResponseError, match="identity"):
        asyncio.run(registry.execute("web-a", _request()))


def test_registry_rejects_incompatible_explicit_provider() -> None:
    registry = SearchProviderRegistry([FakeProvider("web-a")])
    request = _request(capability=SearchCapability.NEWS)
    with pytest.raises(SearchCapabilityError):
        asyncio.run(registry.execute("web-a", request))


def test_registry_rejects_duplicate_registration() -> None:
    with pytest.raises(ValueError, match="already registered"):
        SearchProviderRegistry([FakeProvider("web-a"), FakeProvider("web-a")])
