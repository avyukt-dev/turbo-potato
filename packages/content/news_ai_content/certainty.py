"""Application-owned certainty ceilings; no prose inference or factual mutation."""

from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from news_ai_domain import ClaimVerificationStatus as Status
from news_ai_domain import FactCheckLabel as Label
from pydantic import BaseModel, ConfigDict

CERTAINTY_POLICY_VERSION = "certainty-policy-v1"


class ClaimAssertionStrength(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


_STRENGTH_RANK = MappingProxyType(
    {
        ClaimAssertionStrength.NONE: 0,
        ClaimAssertionStrength.LOW: 1,
        ClaimAssertionStrength.MEDIUM: 2,
        ClaimAssertionStrength.HIGH: 3,
    }
)


class ClaimPresentationFrame(StrEnum):
    DIRECT = "DIRECT"
    QUALIFIED = "QUALIFIED"
    DISPUTED = "DISPUTED"
    UNCERTAIN = "UNCERTAIN"
    REFUTATION = "REFUTATION"


class ClaimPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: UUID
    source_status: Status
    source_fact_check_label: Label
    assertion_strength: ClaimAssertionStrength
    frame: ClaimPresentationFrame


class CertaintyCeiling(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    maximum_strength: ClaimAssertionStrength
    allowed_frames: tuple[ClaimPresentationFrame, ...]


class CertaintyViolation(StrEnum):
    STATUS_MISMATCH = "STATUS_MISMATCH"
    LABEL_MISMATCH = "LABEL_MISMATCH"
    CEILING_EXCEEDED = "CEILING_EXCEEDED"
    FRAME_MISMATCH = "FRAME_MISMATCH"
    SOURCE_COMBINATION_INVALID = "SOURCE_COMBINATION_INVALID"


def certainty_ceiling(status: Status, label: Label) -> CertaintyCeiling:
    """Only pairs emitted by the current deterministic FactCheckEngine are authorized."""
    S, F = ClaimAssertionStrength, ClaimPresentationFrame
    if status == Status.SUPPORTED and label == Label.TRUE:
        return CertaintyCeiling(
            maximum_strength=S.HIGH, allowed_frames=(F.DIRECT, F.QUALIFIED, F.UNCERTAIN)
        )
    if status == Status.PARTIALLY_SUPPORTED and label == Label.PARTIALLY_TRUE:
        return CertaintyCeiling(
            maximum_strength=S.MEDIUM, allowed_frames=(F.QUALIFIED, F.UNCERTAIN)
        )
    if status == Status.DISPUTED and label == Label.UNVERIFIED:
        return CertaintyCeiling(maximum_strength=S.LOW, allowed_frames=(F.DISPUTED,))
    if status == Status.UNVERIFIED and label == Label.UNVERIFIED:
        return CertaintyCeiling(
            maximum_strength=S.LOW,
            allowed_frames=(F.UNCERTAIN,),
        )
    if status == Status.REFUTED and label == Label.FALSE:
        return CertaintyCeiling(maximum_strength=S.NONE, allowed_frames=(F.REFUTATION,))
    raise ValueError("unsupported immutable claim status/label combination")


def presentation_violations(
    presentation: ClaimPresentation, status: Status, label: Label
) -> tuple[CertaintyViolation, ...]:
    violations = []
    if presentation.source_status != status:
        violations.append(CertaintyViolation.STATUS_MISMATCH)
    if presentation.source_fact_check_label != label:
        violations.append(CertaintyViolation.LABEL_MISMATCH)
    try:
        ceiling = certainty_ceiling(status, label)
    except ValueError:
        return (*violations, CertaintyViolation.SOURCE_COMBINATION_INVALID)
    if _STRENGTH_RANK[presentation.assertion_strength] > _STRENGTH_RANK[ceiling.maximum_strength]:
        violations.append(CertaintyViolation.CEILING_EXCEEDED)
    if presentation.frame not in ceiling.allowed_frames:
        violations.append(CertaintyViolation.FRAME_MISMATCH)
    return tuple(violations)
