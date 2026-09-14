"""Deterministic Fact-Sheet-to-editorial-brief transformation."""

from __future__ import annotations

from news_ai_domain import CLAIM_SEMANTICS_POLICY_VERSION, ClaimVerificationStatus
from news_ai_domain.values import VALUE_INTEGRITY_POLICY_VERSION
from news_ai_editorial import PublishingPolicyConfig
from news_ai_evidence import FactSheetArtifact

from .configuration import ContentStyleConfig
from .contracts import BriefClaim, ContentTarget, EditorialBrief


def build_editorial_brief(
    fact_sheet: FactSheetArtifact,
    *,
    style: ContentStyleConfig,
    publishing_policy: PublishingPolicyConfig,
) -> EditorialBrief:
    if any(claim.status is ClaimVerificationStatus.UNASSESSED for claim in fact_sheet.claims):
        raise ValueError("Content generation cannot use an UNASSESSED Fact Sheet claim")
    checks = {item.claim_id: item for item in fact_sheet.fact_checks}
    evidence = {item.evidence_id: item for item in fact_sheet.evidence}
    claims: list[BriefClaim] = []
    for claim in fact_sheet.claims:
        if (
            claim.semantics is None
            or claim.semantics.policy_version != CLAIM_SEMANTICS_POLICY_VERSION
        ):
            raise ValueError("CLAIM_SEMANTICS_MISSING")
        check = checks.get(claim.claim_id)
        if claim.values is None or claim.values.policy_version != VALUE_INTEGRITY_POLICY_VERSION:
            raise ValueError("CLAIM_VALUES_MISSING")
        if check is None:
            raise ValueError("Fact Sheet claim is missing its FactCheck snapshot")
        excerpts = tuple(
            item.excerpt
            for evidence_id in claim.evidence_ids
            if (item := evidence.get(evidence_id)) is not None and item.excerpt
        )
        claims.append(
            BriefClaim(
                claim_id=claim.claim_id,
                text=claim.claim_text,
                status=claim.status,
                fact_check_id=check.fact_check_id,
                label=check.label,
                semantics=claim.semantics,
                values=claim.values,
                confidence_score=check.confidence_score,
                evidence_ids=claim.evidence_ids,
                evidence_excerpts=excerpts,
            )
        )
    return EditorialBrief(
        story_id=fact_sheet.story_id,
        fact_sheet_id=fact_sheet.fact_sheet_id,
        fact_sheet_version=fact_sheet.version,
        headline=fact_sheet.headline,
        summary=fact_sheet.summary,
        editorial_angle=(
            "Explain the verified and unresolved state clearly, leading with the strongest "
            "supported material while preserving qualification, contradiction, and uncertainty."
        ),
        key_points=tuple(
            f"{claim.status.value}: {claim.claim_text}" for claim in fact_sheet.claims
        ),
        exclusions=(
            "unsupported motive attribution",
            "sensational overstatement",
            "factual certainty beyond the Fact Sheet",
        ),
        tone=style.default_tone,
        audience_relevance=None,
        claims=tuple(claims),
        risk_level=fact_sheet.risk_level,
        sensitive_topics=fact_sheet.sensitive_topics,
        unresolved_questions=fact_sheet.unresolved_questions,
        # Global editorial weights guide prioritization; they are not story classifications.
        priority_topics=(),
        style_rules=style.defaults.model_dump(),
        target=ContentTarget.model_validate(style.default_target.model_dump()),
        human_review_required=(publishing_policy.mvp.external_publication_requires_human_approval),
    )
