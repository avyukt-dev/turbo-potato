"""Provider-neutral search and evidence-acquisition boundaries."""

from .contracts import (
    CandidateSourceType,
    SearchCapability,
    SearchProviderCapabilities,
    SearchQueryFamily,
    SearchRequest,
    SearchResponse,
    SearchResult,
    metadata_contains_secret_key,
)
from .policy import (
    SearchBudgets,
    SearchExecutionConstraints,
    SearchPolicy,
    SearchPolicyEnforcer,
    SearchPolicyLoader,
    SearchRules,
)
from .provider import (
    SearchCapabilityError,
    SearchInvalidResponseError,
    SearchProvider,
    SearchProviderError,
    SearchProviderPolicyError,
    SearchProviderRateLimitError,
    SearchProviderTimeoutError,
    SearchProviderUnavailableError,
    require_provider_compatibility,
)
from .registry import SearchProviderNotRegisteredError, SearchProviderRegistry

__all__ = [
    "CandidateSourceType",
    "SearchBudgets",
    "SearchCapability",
    "SearchCapabilityError",
    "SearchExecutionConstraints",
    "SearchInvalidResponseError",
    "SearchPolicy",
    "SearchPolicyEnforcer",
    "SearchPolicyLoader",
    "SearchProvider",
    "SearchProviderCapabilities",
    "SearchProviderError",
    "SearchProviderNotRegisteredError",
    "SearchProviderPolicyError",
    "SearchProviderRateLimitError",
    "SearchProviderRegistry",
    "SearchProviderTimeoutError",
    "SearchProviderUnavailableError",
    "SearchQueryFamily",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "SearchRules",
    "metadata_contains_secret_key",
    "require_provider_compatibility",
]
