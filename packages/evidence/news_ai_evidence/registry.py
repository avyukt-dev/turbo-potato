"""Registry for search adapters without embedding research routing policy."""

from __future__ import annotations

from collections.abc import Iterable

from .contracts import SearchProviderCapabilities, SearchRequest, SearchResponse
from .provider import (
    SearchInvalidResponseError,
    SearchProvider,
    require_provider_compatibility,
)


class SearchProviderNotRegisteredError(KeyError):
    """Requested search-provider identifier has no registered adapter."""


class SearchProviderRegistry:
    """Own adapter registration and explicit provider execution validation."""

    def __init__(self, providers: Iterable[SearchProvider] = ()) -> None:
        self._providers: dict[str, SearchProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: SearchProvider) -> None:
        provider_id = provider.provider_id
        if provider_id != provider.capabilities.provider_id:
            raise ValueError("provider_id must match capabilities.provider_id")
        if provider_id in self._providers:
            raise ValueError(f"search provider is already registered: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> SearchProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise SearchProviderNotRegisteredError(provider_id) from exc

    def capabilities(self) -> tuple[SearchProviderCapabilities, ...]:
        return tuple(
            provider.capabilities
            for provider in sorted(self._providers.values(), key=lambda item: item.provider_id)
        )

    def compatible_provider_ids(self, request: SearchRequest) -> tuple[str, ...]:
        return tuple(
            provider.provider_id
            for provider in sorted(self._providers.values(), key=lambda item: item.provider_id)
            if provider.capabilities.supports(request)
        )

    async def execute(self, provider_id: str, request: SearchRequest) -> SearchResponse:
        provider = self.get(provider_id)
        require_provider_compatibility(provider.capabilities, request)
        response = await provider.search(request)
        self._validate_response(provider, request, response)
        return response

    @staticmethod
    def _validate_response(
        provider: SearchProvider,
        request: SearchRequest,
        response: SearchResponse,
    ) -> None:
        if response.provider_id != provider.provider_id:
            raise SearchInvalidResponseError(
                "search response provider identity does not match adapter identity"
            )
        if response.request_id != request.request_id:
            raise SearchInvalidResponseError("search response request_id does not match request")
        if len(response.results) > request.max_results:
            raise SearchInvalidResponseError("search response exceeds requested result limit")
