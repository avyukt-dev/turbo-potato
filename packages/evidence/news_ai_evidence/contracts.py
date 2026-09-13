"""Provider-neutral search contracts for claim-driven research."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SearchCapability(StrEnum):
    WEB = "WEB"
    NEWS = "NEWS"
    SPECIALIST = "SPECIALIST"


class SearchQueryFamily(StrEnum):
    EXACT_CLAIM = "EXACT_CLAIM"
    ENTITY_EVENT = "ENTITY_EVENT"
    OFFICIAL_SOURCE = "OFFICIAL_SOURCE"
    PRIMARY_DOCUMENT = "PRIMARY_DOCUMENT"
    CONTRADICTION = "CONTRADICTION"
    COUNTERCLAIM = "COUNTERCLAIM"
    LOCAL_LANGUAGE = "LOCAL_LANGUAGE"
    HISTORICAL_SOURCE = "HISTORICAL_SOURCE"
    ACADEMIC_SCHOLARLY = "ACADEMIC_SCHOLARLY"


class CandidateSourceType(StrEnum):
    WEB_PAGE = "WEB_PAGE"
    NEWS_ARTICLE = "NEWS_ARTICLE"
    PRIMARY_DOCUMENT = "PRIMARY_DOCUMENT"
    ACADEMIC = "ACADEMIC"
    DATASET = "DATASET"
    SOCIAL = "SOCIAL"
    OTHER = "OTHER"


_SECRET_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


def metadata_contains_secret_key(value: Any) -> bool:
    """Detect secret-looking mapping keys recursively."""

    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS):
                return True
            if metadata_contains_secret_key(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(metadata_contains_secret_key(item) for item in value)
    return False


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("search timestamps must be timezone-aware")
    return value


def _normalize_domains(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        domain = value.strip().casefold().rstrip(".")
        if not domain or "/" in domain or ":" in domain or " " in domain:
            raise ValueError("search domains must be bare hostnames")
        if domain not in seen:
            normalized.append(domain)
            seen.add(domain)
    return tuple(normalized)


class SearchRequest(BaseModel):
    """One bounded search request associated with a research claim/story."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID = Field(default_factory=uuid4)
    query: str = Field(min_length=1, max_length=2048)
    query_family: SearchQueryFamily
    capability: SearchCapability
    claim_id: UUID | None = None
    story_id: UUID | None = None
    language: str | None = Field(default=None, min_length=2, max_length=32)
    region: str | None = Field(default=None, min_length=2, max_length=64)
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()
    published_after: datetime | None = None
    published_before: datetime | None = None
    max_results: int = Field(default=10, ge=1, le=100)
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("search query must not be blank")
        return normalized

    @field_validator("include_domains", "exclude_domains")
    @classmethod
    def validate_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_domains(value)

    @field_validator("published_after", "published_before")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)

    @field_validator("metadata")
    @classmethod
    def reject_secret_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if metadata_contains_secret_key(value):
            raise ValueError("search request metadata must not contain secrets")
        return value

    @model_validator(mode="after")
    def validate_filters(self) -> SearchRequest:
        overlap = set(self.include_domains) & set(self.exclude_domains)
        if overlap:
            raise ValueError("include_domains and exclude_domains must not overlap")
        if (
            self.published_after is not None
            and self.published_before is not None
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after must not be later than published_before")
        return self


class SearchResult(BaseModel):
    """Candidate source discovered by a provider; not an evidence verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=2048)
    snippet: str | None = Field(default=None, max_length=12000)
    source_name: str | None = Field(default=None, max_length=512)
    candidate_type: CandidateSourceType = CandidateSourceType.OTHER
    rank: int = Field(ge=1)
    published_at: datetime | None = None
    updated_at: datetime | None = None
    language: str | None = Field(default=None, min_length=2, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def require_public_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("search result URL must use http or https")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("search result URL must not contain credentials")
        return value

    @field_validator("published_at", "updated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)

    @field_validator("metadata")
    @classmethod
    def reject_secret_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if metadata_contains_secret_key(value):
            raise ValueError("search result metadata must not contain secrets")
        return value


class SearchResponse(BaseModel):
    """Normalized provider response containing candidate sources."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    provider_id: str = Field(min_length=1, max_length=128)
    retrieved_at: datetime
    results: tuple[SearchResult, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("retrieved_at")
    @classmethod
    def validate_retrieved_at(cls, value: datetime) -> datetime:
        result = _require_aware(value)
        assert result is not None
        return result

    @field_validator("metadata")
    @classmethod
    def reject_secret_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if metadata_contains_secret_key(value):
            raise ValueError("search response metadata must not contain secrets")
        return value

    @model_validator(mode="after")
    def reject_duplicate_urls(self) -> SearchResponse:
        urls = [item.url for item in self.results]
        if len(urls) != len(set(urls)):
            raise ValueError("search response must not contain duplicate result URLs")
        return self


class SearchProviderCapabilities(BaseModel):
    """Capabilities advertised by one provider adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1, max_length=128)
    capabilities: frozenset[SearchCapability]
    query_families: frozenset[SearchQueryFamily]
    max_results: int = Field(default=10, ge=1, le=100)
    supports_domain_filter: bool = False
    supports_date_filter: bool = False
    languages: frozenset[str] = frozenset()

    @field_validator("capabilities", "query_families")
    @classmethod
    def require_nonempty_capability_sets(cls, value: frozenset[Any]) -> frozenset[Any]:
        if not value:
            raise ValueError("provider capability sets must not be empty")
        return value

    def supports(self, request: SearchRequest) -> bool:
        if request.capability not in self.capabilities:
            return False
        if request.query_family not in self.query_families:
            return False
        if request.max_results > self.max_results:
            return False
        if (request.include_domains or request.exclude_domains) and not self.supports_domain_filter:
            return False
        if (
            request.published_after is not None or request.published_before is not None
        ) and not self.supports_date_filter:
            return False
        if self.languages and request.language is not None:
            requested = request.language.casefold().split("-", maxsplit=1)[0]
            supported = {item.casefold().split("-", maxsplit=1)[0] for item in self.languages}
            if requested not in supported:
                return False
        return True
