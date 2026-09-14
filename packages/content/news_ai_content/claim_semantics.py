"""Exact immutable statement-semantics preservation, separate from certainty."""

from enum import StrEnum
from uuid import UUID

from news_ai_domain import (
    CLAIM_SEMANTICS_POLICY_VERSION,
    ClaimSemantics,
    ClaimSemanticState,
    ClaimSemanticType,
)
from pydantic import BaseModel, ConfigDict


class ClaimSemanticPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: UUID
    source_semantic_type: ClaimSemanticType
    source_semantic_state: ClaimSemanticState
    presented_semantic_type: ClaimSemanticType
    presented_semantic_state: ClaimSemanticState


class ClaimSemanticViolation(StrEnum):
    SOURCE_TYPE_MISMATCH = "SOURCE_TYPE_MISMATCH"
    SOURCE_STATE_MISMATCH = "SOURCE_STATE_MISMATCH"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    STATE_MISMATCH = "STATE_MISMATCH"
    POLICY_MISMATCH = "POLICY_MISMATCH"


def semantic_presentation_violations(
    presentation: ClaimSemanticPresentation, semantics: ClaimSemantics
) -> tuple[ClaimSemanticViolation, ...]:
    """Never infer state from prose, truth, evidence strength, or another claim."""
    checks = (
        (
            semantics.policy_version != CLAIM_SEMANTICS_POLICY_VERSION,
            ClaimSemanticViolation.POLICY_MISMATCH,
        ),
        (
            presentation.source_semantic_type != semantics.semantic_type,
            ClaimSemanticViolation.SOURCE_TYPE_MISMATCH,
        ),
        (
            presentation.source_semantic_state != semantics.semantic_state,
            ClaimSemanticViolation.SOURCE_STATE_MISMATCH,
        ),
        (
            presentation.presented_semantic_type != semantics.semantic_type,
            ClaimSemanticViolation.TYPE_MISMATCH,
        ),
        (
            presentation.presented_semantic_state != semantics.semantic_state,
            ClaimSemanticViolation.STATE_MISMATCH,
        ),
    )
    return tuple(code for failed, code in checks if failed)
