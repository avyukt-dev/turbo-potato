from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from helpers.claim_semantics import SEMANTICS, presentation
from news_ai_ai import (
    AIRequest,
    AIResponse,
    AIRouteAttempt,
    AIRouteAttemptOutcome,
    AIRoutedResponse,
)
from news_ai_content import (
    AI_INPUT_PROJECTION_VERSION,
    ContentGenerationContext,
    ContentGenerationPrompt,
    ContentGenerationService,
    ContentTarget,
    EditorialBrief,
)
from news_ai_events import EventEnvelope, EventType
from news_ai_evidence import FactSheetArtifact
from news_ai_quality.contracts import QualityAssessmentOutput
from news_ai_quality.semantic import SemanticValidationReport
from news_ai_quality.service import (
    QualityAssessmentService,
    QualityContext,
    QualityVariantContext,
)


class CaptureContentRouter:
    def __init__(self) -> None:
        self.request: AIRequest | None = None

    async def execute(self, request: AIRequest, *, response_validator=None) -> AIRoutedResponse:
        self.request = request
        brief = request.input["editorial_brief"]
        claim_id = UUID(brief["claims"][0]["claim_id"])
        response = AIResponse(
            structured={
                "story_id": brief["story_id"],
                "fact_sheet_id": brief["fact_sheet_id"],
                "fact_sheet_version": brief["fact_sheet_version"],
                "platform": "INSTAGRAM",
                "format": "CAROUSEL",
                "language": request.input["generation_language"],
                "title": "Verified measurement",
                "slides": [
                    {
                        "position": 1,
                        "heading": "What is verified",
                        "body": "The river gauge measured two metres.",
                        "claim_ids": [str(claim_id)],
                    },
                    {
                        "position": 2,
                        "heading": "What remains uncertain",
                        "body": "The evacuation estimate remains unverified.",
                        "claim_ids": [str(claim_id)],
                    },
                ],
                "caption": "Evidence first; uncertainty remains visible.",
                "hashtags": [],
                "claim_ids_used": [str(claim_id)],
                "claim_presentations": [
                    {
                        "claim_id": str(claim_id),
                        "source_status": "PARTIALLY_SUPPORTED",
                        "source_fact_check_label": "PARTIALLY_TRUE",
                        "assertion_strength": "MEDIUM",
                        "frame": "QUALIFIED",
                    }
                ],
                "claim_semantic_presentations": [presentation(claim_id)],
                "claim_value_presentations": [],
            },
            provider="groq",
            model="content-test-model",
            latency_ms=1,
        )
        if response_validator is not None:
            response_validator(response)
        attempt = AIRouteAttempt(
            provider_id="groq",
            model="content-test-model",
            reasoning_effort=request.reasoning_effort,
            reasoning_policy_version=request.reasoning_policy_version,
            reasoning_reasons=request.reasoning_reasons,
            outcome=AIRouteAttemptOutcome.SUCCESS,
        )
        return AIRoutedResponse(response=response, attempts=(attempt,))


class CaptureQualityRouter:
    def __init__(self) -> None:
        self.request: AIRequest | None = None

    async def execute(self, request: AIRequest, *, response_validator=None) -> AIRoutedResponse:
        self.request = request
        response = AIResponse(
            structured=QualityAssessmentOutput(
                content_variant_id=UUID(request.input["content_artifact"]["content_variant_id"]),
                factual_accuracy_passed=True,
                source_alignment_passed=True,
                citation_alignment_passed=True,
                style_passed=True,
                unsupported_claims=(),
                fabricated_quotes=(),
                incorrect_names=(),
                incorrect_dates=(),
                incorrect_numbers=(),
                missing_context=(),
                defamation_risk=False,
                sensitive_topic_error=False,
                notes=(),
                certainty_escalations=(),
                claim_semantic_escalations=(),
                value_escalations=(),
            ).model_dump(mode="json"),
            provider="groq",
            model="quality-test-model",
            latency_ms=1,
        )
        if response_validator is not None:
            response_validator(response)
        attempt = AIRouteAttempt(
            provider_id="groq",
            model="quality-test-model",
            reasoning_effort=request.reasoning_effort,
            reasoning_policy_version=request.reasoning_policy_version,
            reasoning_reasons=request.reasoning_reasons,
            outcome=AIRouteAttemptOutcome.SUCCESS,
        )
        return AIRoutedResponse(response=response, attempts=(attempt,))


def _fact_sheet_and_brief(*, two_claims: bool = False) -> tuple[FactSheetArtifact, EditorialBrief]:
    story_id = uuid4()
    sheet_id = uuid4()
    claim_ids = [uuid4(), uuid4()] if two_claims else [uuid4()]
    check_ids = [uuid4() for _ in claim_ids]
    evidence_ids = [uuid4() for _ in claim_ids]
    source_ids = [uuid4() for _ in claim_ids]

    claims = []
    checks = []
    evidence = []
    sources = []
    brief_claims = []
    for index, claim_id in enumerate(claim_ids):
        claim_text = (
            "The river gauge measured two metres."
            if index == 0
            else "A second record measured three metres."
        )
        claims.append(
            {
                "claim_id": str(claim_id),
                "story_id": str(story_id),
                "claim_text": claim_text,
                "claim_type": "MEASUREMENT",
                "semantics": SEMANTICS,
                "values": {"policy_version": "value-integrity-policy-v1", "anchors": []},
                "status": "PARTIALLY_SUPPORTED",
                "confidence_score": 0.7,
                "importance_score": 0.9,
                "risk_level": "LOW",
                "sensitive_topics": [],
                "evidence_ids": [str(evidence_ids[index])],
                "contradictory_evidence_ids": [],
                "temporal_start": None,
                "temporal_end": None,
                "location_ids": [],
            }
        )
        checks.append(
            {
                "fact_check_id": str(check_ids[index]),
                "story_id": str(story_id),
                "claim_id": str(claim_id),
                "label": "PARTIALLY_TRUE",
                "confidence_score": 0.7,
                "summary": "Supported with qualification.",
                "supporting_evidence_ids": [str(evidence_ids[index])],
                "contradicting_evidence_ids": [],
                "review_required": True,
                "review_state": "NOT_READY",
            }
        )
        evidence.append(
            {
                "evidence_id": str(evidence_ids[index]),
                "claim_id": str(claim_id),
                "source_id": str(source_ids[index]),
                "article_id": str(uuid4()),
                "article_version_id": str(uuid4()),
                "title": f"Official gauge record {index}",
                "url": f"https://records.example/gauge/{index}",
                "published_at": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
                "retrieved_at": datetime(2026, 9, 2, tzinfo=UTC).isoformat(),
                "relation": "DIRECT_SUPPORT",
                "strength_score": 0.9,
                "excerpt": claim_text,
                "provenance_note": "Direct record.",
                "content_hash": "a" * 64,
                "source_level": 1,
                "source_policy_basis": "explicit-primary",
                "lineage_status": "INDEPENDENT",
                "lineage_basis": "primary_document_id",
                "independence_group": f"primary:gauge-{index}",
                "originating_reference": f"gauge-{index}",
                "assessment_ai_run_ids": [],
                "directness": "DIRECT",
                "origin_role": "ORIGINAL",
                "provenance_state": "DURABLE_VERSION_PRESERVED",
                "temporal_role": "CONTEMPORARY",
                "semantics_policy_version": "evidence-semantics-v1",
                "graph_relations": [],
            }
        )
        sources.append(
            {
                "source_id": str(source_ids[index]),
                "name": f"Records office {index}",
                "source_level": 1,
                "url": f"https://records.example/gauge/{index}",
                "publisher": "Records office",
                "published_at": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
                "retrieved_at": datetime(2026, 9, 2, tzinfo=UTC).isoformat(),
                "language": "en",
            }
        )
        brief_claims.append(
            {
                "claim_id": str(claim_id),
                "text": claim_text,
                "status": "PARTIALLY_SUPPORTED",
                "fact_check_id": str(check_ids[index]),
                "label": "PARTIALLY_TRUE",
                "semantics": SEMANTICS,
                "values": {"policy_version": "value-integrity-policy-v1", "anchors": []},
                "confidence_score": 0.7,
                "evidence_ids": [str(evidence_ids[index])],
                "evidence_excerpts": [claim_text],
            }
        )

    fact_sheet = FactSheetArtifact.model_validate(
        {
            "fact_sheet_id": str(sheet_id),
            "story_id": str(story_id),
            "version": 1,
            "headline": "Flood records reviewed",
            "summary": "Verified measurements with preserved uncertainty.",
            "claims": claims,
            "fact_checks": checks,
            "evidence": evidence,
            "sources": sources,
            "timeline": [],
            "entities": [],
            "locations": [],
            "context": [],
            "counterclaims": [],
            "unresolved_questions": ["The evacuation estimate remains unverified."],
            "confidence_score": 0.7,
            "risk_level": "LOW",
            "sensitive_topics": [],
            "created_at": datetime(2026, 9, 2, tzinfo=UTC).isoformat(),
        }
    )
    brief = EditorialBrief.model_validate(
        {
            "story_id": str(story_id),
            "fact_sheet_id": str(sheet_id),
            "fact_sheet_version": 1,
            "headline": "Flood records reviewed",
            "summary": "Verified measurements with preserved uncertainty.",
            "editorial_angle": "Lead with supported measurements and preserve qualification.",
            "key_points": [item["claim_text"] for item in claims],
            "exclusions": ["unsupported motive attribution"],
            "tone": "measured",
            "audience_relevance": None,
            "claims": brief_claims,
            "risk_level": "LOW",
            "sensitive_topics": [],
            "unresolved_questions": ["The evacuation estimate remains unverified."],
            "priority_topics": [],
            "style_rules": {},
            "target": {"platform": "INSTAGRAM", "format": "CAROUSEL"},
            "human_review_required": True,
        }
    )
    return fact_sheet, brief


def test_content_service_projects_only_provider_input_and_keeps_canonical_hash() -> None:
    fact_sheet, brief = _fact_sheet_and_brief()
    router = CaptureContentRouter()
    service = ContentGenerationService(
        router,
        ContentGenerationPrompt("content-generation", "v5", "checksum", "Return JSON."),
        object(),
        object(),
    )
    context = ContentGenerationContext(
        fact_sheet=fact_sheet,
        brief=brief,
        target=ContentTarget(platform="INSTAGRAM", format="CAROUSEL"),
        generation_language="en",
        semantic_key="canonical-full-context-hash",
    )
    event = EventEnvelope(
        event_type=EventType.CONTENT_REQUESTED,
        producer="test",
        producer_version="1",
        aggregate_type="fact_sheet",
        aggregate_id=fact_sheet.fact_sheet_id,
        idempotency_key="content-boundary-test",
        payload={
            "story_id": str(fact_sheet.story_id),
            "fact_sheet_id": str(fact_sheet.fact_sheet_id),
            "requested_platforms": ["INSTAGRAM"],
            "requested_formats": ["CAROUSEL"],
        },
    )

    asyncio.run(service.generate(context, event))

    assert router.request is not None
    assert router.request.input_hash == context.semantic_key
    assert router.request.metadata == {"input_projection_version": AI_INPUT_PROJECTION_VERSION}
    request_sheet = router.request.input["immutable_fact_sheet"]
    request_brief = router.request.input["editorial_brief"]
    assert "url" not in request_sheet["evidence"][0]
    assert request_brief["claims"][0]["text"] == brief.claims[0].text
    assert request_brief["claims"][0]["semantics"] == brief.claims[0].semantics.model_dump(
        mode="json"
    )
    assert request_brief["claims"][0]["values"] == brief.claims[0].values.model_dump(mode="json")
    assert request_brief["unresolved_questions"] == list(brief.unresolved_questions)


def test_quality_service_projects_exact_used_claim_and_source_after_validation() -> None:
    fact_sheet, brief = _fact_sheet_and_brief(two_claims=True)
    selected_claim = fact_sheet.claims[0]
    selected_source = fact_sheet.sources[0]
    variant_id = uuid4()
    artifact = {
        "content_variant_id": str(variant_id),
        "content_variant_version": 1,
        "platform": "INSTAGRAM",
        "format": "CAROUSEL",
        "language": "en",
        "title": "What the record shows",
        "body": "Measured record\nThe river gauge measured two metres.",
        "caption": "The estimate remains preliminary.",
        "structured_payload": {
            "slides": [
                {
                    "position": 1,
                    "heading": "Measured record",
                    "body": "The river gauge measured two metres.",
                    "claim_ids": [str(selected_claim.claim_id)],
                }
            ],
            "hashtags": [],
            "claim_ids_used": [str(selected_claim.claim_id)],
            "claim_presentations": [],
            "claim_semantic_presentations": [],
            "claim_value_presentations": [],
        },
        "claim_ids_used": [str(selected_claim.claim_id)],
        "source_ids_used": [str(selected_source.source_id)],
        "media_asset_ids": [str(uuid4())],
        "media_provenance": [{"opaque": "do-not-send"}],
    }
    variant = QualityVariantContext(
        variant_id=variant_id,
        variant_version=1,
        semantic_key="quality-canonical-full-context-hash",
        artifact=artifact,
        artifact_hash="a" * 64,
        deterministic_fabricated_quotes=(),
        semantic_report=SemanticValidationReport(passed=True),
    )
    context = QualityContext(
        draft_id=uuid4(),
        draft_version=1,
        story_id=fact_sheet.story_id,
        fact_sheet_id=fact_sheet.fact_sheet_id,
        fact_sheet_version=fact_sheet.version,
        risk_level="LOW",
        sensitive_topics=(),
        fact_sheet=fact_sheet.model_dump(mode="json"),
        editorial_brief=brief.model_dump(mode="json"),
        style={},
        variants=(variant,),
    )
    router = CaptureQualityRouter()
    publishing_policy = SimpleNamespace(
        mvp=SimpleNamespace(external_publication_requires_human_approval=True)
    )
    service = QualityAssessmentService(
        router,
        SimpleNamespace(
            prompt_id="content-quality",
            version="v5",
            checksum="checksum",
            system_prompt="Return JSON.",
        ),
        object(),
        publishing_policy,
    )
    event = EventEnvelope(
        event_type=EventType.CONTENT_GENERATED,
        producer="test",
        producer_version="1",
        aggregate_type="content_draft",
        aggregate_id=context.draft_id,
        idempotency_key="quality-boundary-test",
        payload={
            "story_id": str(context.story_id),
            "content_draft_id": str(context.draft_id),
            "content_variant_ids": [str(variant_id)],
            "ai_run_id": str(uuid4()),
        },
    )

    asyncio.run(service.assess(context, event))

    assert router.request is not None
    assert router.request.input_hash == variant.semantic_key
    assert router.request.metadata == {"input_projection_version": AI_INPUT_PROJECTION_VERSION}
    request_sheet = router.request.input["immutable_fact_sheet"]
    assert {item["claim_id"] for item in request_sheet["claims"]} == {
        str(selected_claim.claim_id)
    }
    assert {item["source_id"] for item in request_sheet["sources"]} == {
        str(selected_source.source_id)
    }
    assert "media_provenance" not in router.request.input["content_artifact"]
