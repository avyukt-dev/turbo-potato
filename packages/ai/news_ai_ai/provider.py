"""Provider protocol and normalized AI failure types."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import AIRequest, AIResponse, ProviderCapabilities


class AIProviderError(RuntimeError):
    """Base error for provider execution failures."""


class AIProviderUnavailableError(AIProviderError):
    """Provider endpoint or service is unavailable."""


class AIProviderTimeoutError(AIProviderError):
    """Provider execution exceeded the configured timeout."""


class AIProviderRateLimitError(AIProviderError):
    """Provider refused execution because of a rate limit."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AIProviderPolicyError(AIProviderError):
    """Provider or application policy disallows this request."""


class AIProviderAuthenticationError(AIProviderPolicyError):
    """The exact credential used by the request was rejected as invalid or revoked."""


class AIProviderCredentialUnknownError(AIProviderError):
    """A credential-specific failure cannot be safely classified for automatic reuse."""


class AIProviderCredentialPoolExhaustedError(AIProviderUnavailableError):
    """No credential in one provider pool is currently usable."""


class AIContextTooLargeError(AIProviderError):
    """Request exceeds a provider/model context limit."""


class AILocalResourceExhaustedError(AIProviderError):
    """Local inference cannot run because required resources are exhausted."""


class AIInvalidResponseError(AIProviderError):
    """Provider returned output that cannot be normalized safely."""


class AICapabilityError(AIProviderError):
    """Provider does not satisfy the request's declared requirements."""


@runtime_checkable
class AIProvider(Protocol):
    """Provider-neutral inference adapter contract."""

    @property
    def provider_id(self) -> str:
        """Stable provider identifier used by registry/routing policy."""
        ...

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Current capabilities declared by the adapter."""
        ...

    async def execute(self, request: AIRequest) -> AIResponse:
        """Execute one request and return normalized provider output."""
        ...


def require_provider_compatibility(
    capabilities: ProviderCapabilities,
    request: AIRequest,
) -> None:
    """Reject execution when an adapter cannot satisfy a request."""

    if request.allowed_providers and capabilities.provider_id not in request.allowed_providers:
        raise AICapabilityError(
            f"provider {capabilities.provider_id!r} is not allowed for this request"
        )
    if request.task_type not in capabilities.task_types:
        raise AICapabilityError(
            f"provider {capabilities.provider_id!r} does not support task {request.task_type.value}"
        )
    if request.response_format not in capabilities.response_formats:
        raise AICapabilityError(
            "provider "
            f"{capabilities.provider_id!r} does not support response format "
            f"{request.response_format.value!r}"
        )
    if (
        request.model is not None
        and capabilities.models
        and request.model not in capabilities.models
    ):
        raise AICapabilityError(
            f"provider {capabilities.provider_id!r} does not expose model {request.model!r}"
        )
