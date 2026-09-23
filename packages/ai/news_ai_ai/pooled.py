"""Shared credential-aware execution for authenticated AI adapters."""

from __future__ import annotations

from typing import Protocol

from .contracts import AIRequest, AIResponse
from .credentials import CredentialStateUnavailableError, ResolvedCredential
from .provider import (
    AIProviderAuthenticationError,
    AIProviderCredentialPoolExhaustedError,
    AIProviderCredentialUnknownError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderUnavailableError,
)


class CredentialPool(Protocol):
    config: object

    async def acquire(self, attempted: set[int]) -> ResolvedCredential | None: ...

    async def record_success(self, credential: ResolvedCredential) -> None: ...

    async def record_rate_limit(
        self, credential: ResolvedCredential, retry_after_seconds: float | None
    ) -> None: ...

    async def record_auth_failure(self, credential: ResolvedCredential) -> None: ...

    async def record_unknown(self, credential: ResolvedCredential) -> None: ...

    async def minimum_cooldown_remaining(self) -> float | None: ...


class CredentialPooledProvider:
    """Mixin that isolates credential state transitions from provider SDK mapping."""

    credential_pool: CredentialPool

    async def _execute_from_pool(self, request: AIRequest) -> AIResponse:
        attempted: set[int] = set()
        last_error: AIProviderError | None = None
        while True:
            try:
                credential = await self.credential_pool.acquire(attempted)
            except CredentialStateUnavailableError as exc:
                raise AIProviderUnavailableError("AI credential state is unavailable") from exc
            if credential is None:
                break
            attempted.add(credential.slot)
            try:
                response = await self._execute_with_credential(request, credential.secret)
            except AIProviderRateLimitError as exc:
                last_error = exc
                await self._record("rate", credential, exc.retry_after_seconds)
                continue
            except AIProviderAuthenticationError as exc:
                last_error = exc
                await self._record("auth", credential)
                continue
            except AIProviderCredentialUnknownError as exc:
                last_error = exc
                await self._record("unknown", credential)
                continue
            await self._record("success", credential)
            return response
        if isinstance(
            last_error, (AIProviderAuthenticationError, AIProviderCredentialUnknownError)
        ):
            raise AIProviderCredentialPoolExhaustedError(
                "No configured AI credential is currently usable"
            ) from last_error
        if last_error is not None:
            raise last_error
        try:
            remaining = await self.credential_pool.minimum_cooldown_remaining()
        except CredentialStateUnavailableError as exc:
            raise AIProviderUnavailableError("AI credential state is unavailable") from exc
        raise AIProviderRateLimitError(
            "No configured AI credential is currently eligible",
            retry_after_seconds=remaining,
        )

    async def _record(
        self,
        outcome: str,
        credential: ResolvedCredential,
        retry_after_seconds: float | None = None,
    ) -> None:
        try:
            if outcome == "success":
                await self.credential_pool.record_success(credential)
            elif outcome == "rate":
                await self.credential_pool.record_rate_limit(credential, retry_after_seconds)
            elif outcome == "auth":
                await self.credential_pool.record_auth_failure(credential)
            else:
                await self.credential_pool.record_unknown(credential)
        except CredentialStateUnavailableError as exc:
            raise AIProviderUnavailableError("AI credential state is unavailable") from exc

    async def _execute_with_credential(self, request: AIRequest, api_key: str) -> AIResponse:
        raise NotImplementedError
