"""Closed classification vocabulary does not authorize factual status changes."""

import pytest
from news_ai_domain import (
    CLAIM_SEMANTICS_POLICY_VERSION,
    ClaimSemantics,
    ClaimSemanticState,
    ClaimSemanticType,
    ClaimVerificationStatus,
)
from pydantic import ValidationError


def test_closed_vocabulary_and_independent_dimensions():
    assert {item.value for item in ClaimSemanticType} == {
        "GENERAL_FACT",
        "EVENT",
        "QUANTITATIVE",
        "ATTRIBUTION",
        "LEGAL_PROCEDURAL",
        "CAUSAL",
        "PREDICTION_FORECAST",
        "POLICY_COMMITMENT",
    }
    assert {item.value for item in ClaimSemanticState} == {
        "OBSERVED",
        "ANNOUNCED",
        "PLANNED",
        "EXPECTED",
        "PREDICTED",
    }
    assert set(ClaimSemanticState).isdisjoint(ClaimVerificationStatus)
    assert CLAIM_SEMANTICS_POLICY_VERSION == "claim-semantics-policy-v1"


@pytest.mark.parametrize(
    "field,value",
    [("semantic_type", "ALLEGATION"), ("semantic_state", "TRUE"), ("semantic_state", "UNKNOWN")],
)
def test_untrusted_vocabulary_rejected(field, value):
    with pytest.raises(ValidationError):
        ClaimSemantics.model_validate(
            {
                "policy_version": CLAIM_SEMANTICS_POLICY_VERSION,
                "semantic_type": "EVENT",
                "semantic_state": "OBSERVED",
                field: value,
            }
        )
