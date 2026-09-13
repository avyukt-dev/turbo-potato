from pathlib import Path
from uuid import uuid4

import pytest
from news_ai_common.config import ConfigLoader
from news_ai_evidence import (
    SearchBudgets,
    SearchCapability,
    SearchPolicy,
    SearchPolicyEnforcer,
    SearchPolicyLoader,
    SearchProviderPolicyError,
    SearchQueryFamily,
    SearchRequest,
    SearchRules,
)


def _request(
    family: SearchQueryFamily = SearchQueryFamily.EXACT_CLAIM,
    *,
    claim: bool = True,
    timeout_seconds: int | None = None,
) -> SearchRequest:
    return SearchRequest(
        query="claim",
        query_family=family,
        capability=SearchCapability.WEB,
        claim_id=uuid4() if claim else None,
        timeout_seconds=timeout_seconds,
    )


def _policy(**rule_updates: bool) -> SearchPolicy:
    return SearchPolicy(
        schema_version=1,
        rules=SearchRules(**rule_updates),
        budgets=SearchBudgets(
            default_timeout_seconds=90,
            breaking_news_timeout_seconds=45,
        ),
    )


def test_loader_accepts_canonical_search_policy() -> None:
    loader = SearchPolicyLoader(ConfigLoader(Path("config")))
    policy = loader.load()
    assert policy.rules.claim_driven is True
    assert policy.budgets.default_timeout_seconds == 90
    assert policy.budgets.breaking_news_timeout_seconds == 45


def test_policy_requires_claim_for_claim_driven_search() -> None:
    enforcer = SearchPolicyEnforcer(_policy())
    with pytest.raises(SearchProviderPolicyError, match="claim_id"):
        enforcer.prepare(_request(claim=False))


def test_policy_applies_bounded_normal_and_breaking_news_timeouts() -> None:
    enforcer = SearchPolicyEnforcer(_policy())

    normal = enforcer.prepare(_request())
    assert normal.request.timeout_seconds == 90
    assert normal.treat_retrieved_content_as_untrusted is True

    breaking = enforcer.prepare(_request(timeout_seconds=80), breaking_news=True)
    assert breaking.request.timeout_seconds == 45

    stricter = enforcer.prepare(_request(timeout_seconds=30), breaking_news=True)
    assert stricter.request.timeout_seconds == 30


@pytest.mark.parametrize(
    ("family", "rule"),
    [
        (SearchQueryFamily.PRIMARY_DOCUMENT, "search_primary_sources"),
        (SearchQueryFamily.CONTRADICTION, "search_contradictions"),
        (SearchQueryFamily.COUNTERCLAIM, "search_counterclaims"),
    ],
)
def test_policy_blocks_disabled_query_families(
    family: SearchQueryFamily,
    rule: str,
) -> None:
    enforcer = SearchPolicyEnforcer(_policy(**{rule: False}))
    with pytest.raises(SearchProviderPolicyError):
        enforcer.prepare(_request(family))
