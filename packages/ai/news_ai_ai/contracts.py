"""Typed request, response, and capability contracts for AI providers."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ProviderId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")]
ModelId = Annotated[str, Field(min_length=1, max_length=255)]


class AITaskType(StrEnum):
    """Task vocabulary owned by the AI platform."""

    CLASSIFICATION = "CLASSIFICATION"
    LANGUAGE_DETECTION = "LANGUAGE_DETECTION"
    KEYWORD_EXTRACTION = "KEYWORD_EXTRACTION"
    ENTITY_EXTRACTION = "ENTITY_EXTRACTION"
    STORY_SIMILARITY = "STORY_SIMILARITY"
    CLAIM_EXTRACTION = "CLAIM_EXTRACTION"
    SUMMARIZATION = "SUMMARIZATION"
    EDITORIAL_SCORING = "EDITORIAL_SCORING"
    RESEARCH_SYNTHESIS = "RESEARCH_SYNTHESIS"
    EVIDENCE_ASSESSMENT = "EVIDENCE_ASSESSMENT"
    FACT_CHECK_ASSISTANCE = "FACT_CHECK_ASSISTANCE"
    FACT_SHEET_GENERATION = "FACT_SHEET_GENERATION"
    CONTENT_GENERATION = "CONTENT_GENERATION"
    TRANSLATION = "TRANSLATION"
    QUALITY_CHECKING = "QUALITY_CHECKING"
    IMAGE_BRIEF = "IMAGE_BRIEF"


class AIResponseFormat(StrEnum):
    TEXT = "text"
    STRUCTURED = "structured"


class ProviderLocality(StrEnum):
    LOCAL = "LOCAL"
    CLOUD = "CLOUD"


class PromptReference(BaseModel):
    """Versioned prompt identity supplied to a provider invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    checksum: str = Field(min_length=1, max_length=128)


class AIRequest(BaseModel):
    """Provider-neutral AI invocation request.

    Factual/evidence policy is deliberately not encoded here. Callers supply already-authorized
    context and the later router enforces provider policy before execution.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: AITaskType
    system_prompt: str = Field(min_length=1)
    input: Any
    prompt: PromptReference | None = None
    model: ModelId | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    response_format: AIResponseFormat = AIResponseFormat.TEXT
    timeout_seconds: float | None = Field(default=None, gt=0.0, le=600.0)
    priority: int = Field(default=3, ge=1, le=5)
    correlation_id: UUID | None = None
    language: str | None = Field(default=None, min_length=2, max_length=32)
    sensitivity: tuple[str, ...] = ()
    allowed_providers: tuple[ProviderId, ...] = ()
    input_artifact_ids: tuple[str, ...] = ()
    input_hash: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("system_prompt")
    @classmethod
    def strip_system_prompt(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("system_prompt must not be blank")
        return stripped

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("sensitivity", "input_artifact_ids")
    @classmethod
    def require_unique_non_blank_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("list values must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("list values must be unique")
        return normalized

    @field_validator("allowed_providers")
    @classmethod
    def require_unique_providers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("allowed_providers must be unique")
        return value


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class AIResponse(BaseModel):
    """Normalized provider response before downstream semantic validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str | None = None
    structured: dict[str, Any] | None = None
    provider: ProviderId
    model: ModelId
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = Field(ge=0)
    finish_reason: str | None = Field(default=None, max_length=128)
    provider_request_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_output(self) -> AIResponse:
        if self.text is None and self.structured is None:
            raise ValueError("AI response must contain text or structured output")
        return self


class ProviderCapabilities(BaseModel):
    """Capabilities declared by one registered provider adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    locality: ProviderLocality
    task_types: frozenset[AITaskType]
    response_formats: frozenset[AIResponseFormat]
    models: frozenset[ModelId] = frozenset()
    supports_vision: bool = False
    supports_tools: bool = False
    max_context_tokens: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_capabilities(self) -> ProviderCapabilities:
        if not self.task_types:
            raise ValueError("provider must declare at least one supported task")
        if not self.response_formats:
            raise ValueError("provider must declare at least one response format")
        return self

    def supports(self, request: AIRequest) -> bool:
        if request.task_type not in self.task_types:
            return False
        if request.response_format not in self.response_formats:
            return False
        if request.allowed_providers and self.provider_id not in request.allowed_providers:
            return False
        return request.model is None or not self.models or request.model in self.models


_SECRET_METADATA_KEY = re.compile(
    r"(^|_)(api_?key|authorization|access_?token|refresh_?token|password|secret)($|_)",
    re.IGNORECASE,
)


def metadata_contains_secret_key(metadata: dict[str, Any]) -> bool:
    """Detect obviously secret-bearing metadata keys without inspecting factual input text."""

    return any(_SECRET_METADATA_KEY.search(str(key)) is not None for key in metadata)
