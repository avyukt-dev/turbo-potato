"""Secret-safe social platform failure taxonomy."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class SocialErrorClass(StrEnum):
    TRANSIENT = "TRANSIENT"
    RATE_LIMIT = "RATE_LIMIT"
    AUTHENTICATION = "AUTHENTICATION"
    PERMISSION = "PERMISSION"
    VALIDATION = "VALIDATION"
    MEDIA = "MEDIA"
    NOT_FOUND = "NOT_FOUND"
    PLATFORM = "PLATFORM"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


class SocialAdapterError(RuntimeError):
    """A bounded, sanitized adapter failure suitable for future durable handling."""

    def __init__(
        self,
        message: str,
        *,
        classification: SocialErrorClass,
        http_status: int | None = None,
        provider_code: int | str | None = None,
        retry_after_seconds: int | None = None,
        outcome_may_be_ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.classification = classification
        self.http_status = http_status
        self.provider_code = provider_code
        self.retry_after_seconds = retry_after_seconds
        self.outcome_may_be_ambiguous = outcome_may_be_ambiguous

    @property
    def safe_metadata(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "http_status": self.http_status,
            "provider_code": self.provider_code,
            "retry_after_seconds": self.retry_after_seconds,
            "outcome_may_be_ambiguous": self.outcome_may_be_ambiguous,
        }

    def __repr__(self) -> str:
        return (
            f"SocialAdapterError(classification={self.classification.value!r}, "
            f"http_status={self.http_status!r}, provider_code={self.provider_code!r}, "
            f"outcome_may_be_ambiguous={self.outcome_may_be_ambiguous!r})"
        )
