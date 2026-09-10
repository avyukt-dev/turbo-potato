"""Typed enforcement for canonical config/research/search-policy.yaml."""

from __future__ import annotations

from dataclasses import dataclass

from news_ai_common.config import ConfigDomain, ConfigLoader
from pydantic import BaseModel, ConfigDict, Field

from .contracts import SearchQueryFamily, SearchRequest
from .provider import SearchProviderPolicyError


class SearchRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_driven: bool = True
    search_primary_sources: bool = True
    search_independent_reporting: bool = True
    search_contradictions: bool = True
    search_counterclaims: bool = True
    treat_retrieved_content_as_untrusted: bool = True


class SearchBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default_timeout_seconds: int = Field(ge=1, le=300)
    breaking_news_timeout_seconds: int = Field(ge=1, le=300)


class SearchPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    rules: SearchRules
    budgets: SearchBudgets


class SearchPolicyLoader:
    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self) -> SearchPolicy:
        return self.loader.load_domain_file(
            ConfigDomain.RESEARCH,
            "search-policy.yaml",
            SearchPolicy,
        )


@dataclass(frozen=True, slots=True)
class SearchExecutionConstraints:
    request: SearchRequest
    treat_retrieved_content_as_untrusted: bool


class SearchPolicyEnforcer:
    """Apply methodology-owned constraints without judging candidate truth."""

    def __init__(self, policy: SearchPolicy) -> None:
        self.policy = policy

    def prepare(
        self,
        request: SearchRequest,
        *,
        breaking_news: bool = False,
    ) -> SearchExecutionConstraints:
        self._require_allowed(request)
        budget = (
            self.policy.budgets.breaking_news_timeout_seconds
            if breaking_news
            else self.policy.budgets.default_timeout_seconds
        )
        requested_timeout = request.timeout_seconds
        effective_timeout = budget if requested_timeout is None else min(requested_timeout, budget)
        prepared = request.model_copy(update={"timeout_seconds": effective_timeout})
        return SearchExecutionConstraints(
            request=prepared,
            treat_retrieved_content_as_untrusted=(
                self.policy.rules.treat_retrieved_content_as_untrusted
            ),
        )

    def _require_allowed(self, request: SearchRequest) -> None:
        rules = self.policy.rules
        if rules.claim_driven and request.claim_id is None:
            raise SearchProviderPolicyError("claim-driven search requires claim_id")

        if request.query_family in {
            SearchQueryFamily.OFFICIAL_SOURCE,
            SearchQueryFamily.PRIMARY_DOCUMENT,
        } and not rules.search_primary_sources:
            raise SearchProviderPolicyError("primary-source search is disabled by research policy")

        if (
            request.query_family is SearchQueryFamily.CONTRADICTION
            and not rules.search_contradictions
        ):
            raise SearchProviderPolicyError("contradiction search is disabled by research policy")

        if (
            request.query_family is SearchQueryFamily.COUNTERCLAIM
            and not rules.search_counterclaims
        ):
            raise SearchProviderPolicyError("counterclaim search is disabled by research policy")
