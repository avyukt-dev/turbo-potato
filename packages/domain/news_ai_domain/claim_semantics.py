"""Versioned statement classification, independent of verification and certainty."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

CLAIM_SEMANTICS_POLICY_VERSION = "claim-semantics-policy-v1"


class ClaimSemanticType(StrEnum):
    GENERAL_FACT = "GENERAL_FACT"
    EVENT = "EVENT"
    QUANTITATIVE = "QUANTITATIVE"
    ATTRIBUTION = "ATTRIBUTION"
    LEGAL_PROCEDURAL = "LEGAL_PROCEDURAL"
    CAUSAL = "CAUSAL"
    PREDICTION_FORECAST = "PREDICTION_FORECAST"
    POLICY_COMMITMENT = "POLICY_COMMITMENT"


class ClaimSemanticState(StrEnum):
    # OBSERVED describes the proposition's tense/state, never its truth.
    OBSERVED = "OBSERVED"
    ANNOUNCED = "ANNOUNCED"
    PLANNED = "PLANNED"
    EXPECTED = "EXPECTED"
    PREDICTED = "PREDICTED"


class ClaimSemantics(BaseModel):
    """Immutable classification copied from durable state; no inferred defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    policy_version: str
    semantic_type: ClaimSemanticType
    semantic_state: ClaimSemanticState
    ai_run_id: UUID | None = None
