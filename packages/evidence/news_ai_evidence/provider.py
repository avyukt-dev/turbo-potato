"""Search provider protocol and normalized provider failures."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import SearchProviderCapabilities, SearchRequest, SearchResponse


class SearchProviderError(RuntimeError):
    """Base provider-normalized search failure."""


class SearchProviderTimeoutError(SearchProviderError):
    """Provider exceeded the bounded request timeout."""


class SearchProviderUnavailableError(SearchProviderError):
    """Provider is temporarily unavailable."""


class SearchProviderRateLimitError(SearchProviderError):
    """Provider rejected work because of a rate limit."""


class SearchProviderPolicyError(SearchProviderError):
    """Provider cannot execute the request under configured policy."""


class SearchInvalidResponseError(SearchProviderError):
    """Provider returned a response that violates normalized contracts."""


class SearchCapabilityError(SearchProviderError):
    """Selected provider does not support the request."""


@runtime_checkable
class SearchProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def capabilities(self) -> SearchProviderCapabilities: ...

    async def search(self, request: SearchRequest) -> SearchResponse: ...


def require_provider_compatibility(
    capabilities: SearchProviderCapabilities,
    request: SearchRequest,
) -> None:
    if not capabilities.supports(request):
        raise SearchCapabilityError(
            f"search provider {capabilities.provider_id!r} is incompatible with request"
        )
