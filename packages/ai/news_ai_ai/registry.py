"""Registry for provider adapters without embedding routing policy."""

from __future__ import annotations

from collections.abc import Iterable

from .contracts import AIRequest, AIResponse, AIResponseFormat, ProviderCapabilities
from .provider import AIInvalidResponseError, AIProvider, require_provider_compatibility


class AIProviderNotRegisteredError(KeyError):
    """Requested provider identifier has no registered adapter."""


class AIProviderRegistry:
    """Own provider adapter registration and explicit execution validation.

    This class intentionally does not select a provider. The routing layer may query capabilities
    and choose an adapter according to configured policy in a later pipeline stage.
    """

    def __init__(self, providers: Iterable[AIProvider] = ()) -> None:
        self._providers: dict[str, AIProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: AIProvider) -> None:
        provider_id = provider.provider_id
        if provider_id != provider.capabilities.provider_id:
            raise ValueError("provider_id must match capabilities.provider_id")
        if provider_id in self._providers:
            raise ValueError(f"AI provider is already registered: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> AIProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise AIProviderNotRegisteredError(provider_id) from exc

    def capabilities(self) -> tuple[ProviderCapabilities, ...]:
        return tuple(
            provider.capabilities
            for provider in sorted(self._providers.values(), key=lambda item: item.provider_id)
        )

    def compatible_provider_ids(self, request: AIRequest) -> tuple[str, ...]:
        """Report compatible adapters without applying preference/routing policy."""

        return tuple(
            provider.provider_id
            for provider in sorted(self._providers.values(), key=lambda item: item.provider_id)
            if provider.capabilities.supports(request)
        )

    async def execute(self, provider_id: str, request: AIRequest) -> AIResponse:
        """Execute through one explicitly chosen provider and validate normalized output."""

        provider = self.get(provider_id)
        require_provider_compatibility(provider.capabilities, request)
        response = await provider.execute(request)
        self._validate_response(provider, request, response)
        return response

    @staticmethod
    def _validate_response(provider: AIProvider, request: AIRequest, response: AIResponse) -> None:
        if response.provider != provider.provider_id:
            raise AIInvalidResponseError(
                "provider response identity does not match adapter identity"
            )
        if request.model is not None and response.model != request.model:
            raise AIInvalidResponseError("provider response model does not match requested model")
        if request.response_format is AIResponseFormat.STRUCTURED and response.structured is None:
            raise AIInvalidResponseError("structured request returned no structured output")
        if request.response_format is AIResponseFormat.TEXT and response.text is None:
            raise AIInvalidResponseError("text request returned no text output")

    def require_compatible(self, provider_id: str, request: AIRequest) -> None:
        provider = self.get(provider_id)
        require_provider_compatibility(provider.capabilities, request)
