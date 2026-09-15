"""AI-routed Stage-21 content generation and transactional persistence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from news_ai_ai import (
    REASONING_ROUTING_POLICY_VERSION,
    AIFailureReason,
    AIInvalidResponseError,
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AIRoutedResponse,
    AIRouter,
    AIRoutingExecutionError,
    AITaskType,
    PromptReference,
)
from news_ai_database import (
    AIModel,
    AIRun,
    ContentDraft,
    ContentVariant,
    EventOutbox,
    FactSheet,
    Story,
)
from news_ai_domain import CLAIM_SEMANTICS_POLICY_VERSION, ReviewState
from news_ai_domain.values import VALUE_INTEGRITY_POLICY_VERSION
from news_ai_editorial import PublishingPolicyConfig
from news_ai_events import (
    ContentRequestedV1,
    EventEnvelope,
    EventType,
    PermanentEventError,
    StaleWorkError,
    TransientEventError,
    parse_event_payload,
)
from news_ai_events.outbox import build_outbox_record
from news_ai_evidence import FactSheetArtifact, FactSheetGenerator
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai_projection import (
    AI_INPUT_PROJECTION_VERSION,
    project_content_generation_input,
)
from .brief import build_editorial_brief
from .certainty import (
    CERTAINTY_POLICY_VERSION,
    ClaimPresentation,
    certainty_ceiling,
    presentation_violations,
)
from .claim_semantics import ClaimSemanticPresentation, semantic_presentation_violations
from .configuration import ContentStyleConfig
from .contracts import (
    CarouselSlide,
    ContentDraftArtifact,
    ContentGenerationOutput,
    ContentTarget,
    ContentVariantArtifact,
    EditorialBrief,
    normalize_generation_language,
)
from .reasoning import editorial_reasoning_decision
from .values import ClaimValuePresentation, presentation_errors

_QUOTED_SPAN = re.compile(r'[“"]([^”"]+)[”"]')
_TRANSIENT_AI_FAILURES = {
    AIFailureReason.UNAVAILABLE,
    AIFailureReason.TIMEOUT,
    AIFailureReason.RATE_LIMIT,
    AIFailureReason.LOCAL_RESOURCE_EXHAUSTED,
}


@dataclass(frozen=True, slots=True)
class ContentGenerationPrompt:
    prompt_id: str
    version: str
    checksum: str
    system_prompt: str

    @classmethod
    def load(cls, path: Path, *, version: str = "v1") -> ContentGenerationPrompt:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("content generation prompt must not be blank")
        return cls(
            prompt_id="content-generation",
            version=version,
            checksum=hashlib.sha256(text.encode()).hexdigest(),
            system_prompt=text,
        )


class ContentGenerationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_sheet: FactSheetArtifact
    brief: EditorialBrief
    target: ContentTarget
    generation_language: str
    semantic_key: str

    @field_validator("generation_language")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return normalize_generation_language(value)


@dataclass(frozen=True, slots=True)
class ContentGenerationExecution:
    routed: AIRoutedResponse
    output: ContentGenerationOutput


@dataclass(frozen=True, slots=True)
class ContentGenerationResult:
    story_id: UUID
    content_draft_id: UUID
    content_variant_ids: tuple[UUID, ...]
    ai_run_id: UUID
    event_id: UUID
    created: bool

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "story_id": str(self.story_id),
            "content_draft_id": str(self.content_draft_id),
            "content_variant_ids": [str(item) for item in self.content_variant_ids],
            "ai_run_id": str(self.ai_run_id),
            "event_id": str(self.event_id),
            "created": self.created,
        }


class ContentGenerationService:
    """Use an exact Fact Sheet as the only factual generation boundary."""

    def __init__(
        self,
        router: AIRouter,
        prompt: ContentGenerationPrompt,
        style: ContentStyleConfig,
        publishing_policy: PublishingPolicyConfig,
        *,
        producer: str = "ai-worker",
        producer_version: str = "0.1.0",
    ) -> None:
        self.router = router
        self.prompt = prompt
        self.style = style
        self.publishing_policy = publishing_policy
        self.producer = producer
        self.producer_version = producer_version

    def load_context(self, session: Session, event: EventEnvelope) -> ContentGenerationContext:
        payload = _content_request(event)
        target = _single_target(payload)
        row = session.get(FactSheet, payload.fact_sheet_id)
        if row is None or row.story_id != payload.story_id:
            raise PermanentEventError(
                "content.requested references a missing or mismatched Fact Sheet"
            )
        latest_version = session.scalar(
            select(func.max(FactSheet.version)).where(FactSheet.story_id == row.story_id)
        )
        if latest_version != row.version:
            raise StaleWorkError("content.requested references a superseded Fact Sheet")
        story = session.get(Story, row.story_id)
        if story is None:
            raise PermanentEventError("content Fact Sheet story no longer exists")
        generation_language = _effective_generation_language(
            story.language, self.style.default_generation_language
        )
        artifact = FactSheetGenerator.artifact_from_row(row)
        if any(claim.status.value == "UNASSESSED" for claim in artifact.claims):
            raise PermanentEventError("UNASSESSED Fact Sheet claim cannot generate content")
        try:
            brief = build_editorial_brief(
                artifact,
                style=self.style,
                publishing_policy=self.publishing_policy,
            )
        except ValueError as exc:
            raise PermanentEventError(
                "content Fact Sheet has invalid current claim metadata"
            ) from exc
        if brief.target != target:
            raise PermanentEventError("requested target does not match configured Stage-21 target")
        for claim in brief.claims:
            try:
                certainty_ceiling(claim.status, claim.label)
            except ValueError as exc:
                raise PermanentEventError("content claim certainty source is invalid") from exc
        operation_key = _semantic_key(
            {
                "fact_sheet_id": row.id,
                "fact_sheet_version": row.version,
                "editorial_brief": brief.model_dump(mode="json"),
                "generation_language": generation_language,
                "target": target.model_dump(mode="json"),
                "methodology_version": self.style.methodology_version,
                "certainty_policy_version": CERTAINTY_POLICY_VERSION,
                "claim_semantics_policy_version": CLAIM_SEMANTICS_POLICY_VERSION,
                "value_integrity_policy_version": VALUE_INTEGRITY_POLICY_VERSION,
                "reasoning_policy_version": REASONING_ROUTING_POLICY_VERSION,
                "style": self.style.model_dump(mode="json"),
                "prompt_id": self.prompt.prompt_id,
                "prompt_version": self.prompt.version,
                "prompt_checksum": self.prompt.checksum,
            }
        )
        return ContentGenerationContext(
            fact_sheet=artifact,
            brief=brief,
            target=target,
            generation_language=generation_language,
            semantic_key=operation_key,
        )

    def existing_result(
        self, session: Session, context: ContentGenerationContext
    ) -> ContentGenerationResult | None:
        draft = session.scalar(
            select(ContentDraft).where(ContentDraft.semantic_key == context.semantic_key)
        )
        return self._existing_result(session, draft) if draft is not None else None

    @staticmethod
    def artifacts_from_rows(
        draft: ContentDraft,
        variants: tuple[ContentVariant, ...],
    ) -> tuple[ContentDraftArtifact, tuple[ContentVariantArtifact, ...]]:
        variant_artifacts = tuple(
            ContentVariantArtifact(
                content_variant_id=variant.id,
                content_draft_id=draft.id,
                story_id=draft.story_id,
                fact_sheet_id=draft.fact_sheet_id,
                fact_sheet_version=draft.fact_sheet_version,
                platform=variant.platform,
                format=variant.format,
                language=variant.language,
                title=variant.title,
                body=variant.body,
                caption=variant.caption,
                slides=tuple(
                    CarouselSlide.model_validate(item)
                    for item in variant.structured_payload.get("slides", [])
                ),
                hashtags=tuple(variant.structured_payload.get("hashtags", [])),
                media_asset_ids=tuple(UUID(item) for item in variant.media_asset_ids),
                claim_ids_used=tuple(UUID(item) for item in variant.claim_ids_used),
                source_ids_used=tuple(UUID(item) for item in variant.source_ids_used),
                claim_presentations=tuple(
                    ClaimPresentation.model_validate(item)
                    for item in variant.structured_payload["claim_presentations"]
                )
                if "claim_presentations" in variant.structured_payload
                else None,
                claim_semantic_presentations=tuple(
                    ClaimSemanticPresentation.model_validate(item)
                    for item in variant.structured_payload["claim_semantic_presentations"]
                )
                if "claim_semantic_presentations" in variant.structured_payload
                else None,
                risk_level=draft.risk_level,
                claim_value_presentations=tuple(
                    ClaimValuePresentation.model_validate(item)
                    for item in variant.structured_payload["claim_value_presentations"]
                )
                if "claim_value_presentations" in variant.structured_payload
                else None,
                sensitive_topics=tuple(draft.sensitive_topics),
                review_state=variant.review_state,
                version=variant.version,
            )
            for variant in variants
        )
        draft_artifact = ContentDraftArtifact(
            content_draft_id=draft.id,
            story_id=draft.story_id,
            fact_sheet_id=draft.fact_sheet_id,
            fact_sheet_version=draft.fact_sheet_version,
            editorial_brief_id=None,
            risk_level=draft.risk_level,
            sensitive_topics=tuple(draft.sensitive_topics),
            review_state=draft.review_state,
            variant_ids=tuple(item.content_variant_id for item in variant_artifacts),
            created_by_ai_run_id=draft.created_by_ai_run_id,
            version=draft.version,
        )
        return draft_artifact, variant_artifacts

    async def generate(
        self, context: ContentGenerationContext, event: EventEnvelope
    ) -> ContentGenerationExecution:
        parsed: ContentGenerationOutput | None = None

        def validate(response: AIResponse) -> None:
            nonlocal parsed
            try:
                output = ContentGenerationOutput.model_validate(response.structured)
                _validate_output(output, context, system_prompt=self.prompt.system_prompt)
            except (ValidationError, ValueError) as exc:
                raise AIInvalidResponseError("content output failed contract validation") from exc
            parsed = output

        reasoning = editorial_reasoning_decision(context.brief)
        provider_input = project_content_generation_input(
            {
                "immutable_fact_sheet": context.fact_sheet.model_dump(mode="json"),
                "editorial_brief": context.brief.model_dump(mode="json"),
                "generation_language": context.generation_language,
                "value_integrity_policy_version": VALUE_INTEGRITY_POLICY_VERSION,
                "claim_semantics_policy_version": CLAIM_SEMANTICS_POLICY_VERSION,
                "certainty_ceilings": {
                    str(claim.claim_id): certainty_ceiling(claim.status, claim.label).model_dump(
                        mode="json"
                    )
                    for claim in context.brief.claims
                },
            }
        )
        request = AIRequest(
            task_type=AITaskType.CONTENT_GENERATION,
            system_prompt=self.prompt.system_prompt,
            input=provider_input,
            prompt=PromptReference(
                prompt_id=self.prompt.prompt_id,
                version=self.prompt.version,
                checksum=self.prompt.checksum,
            ),
            reasoning_effort=reasoning.effort,
            reasoning_policy_version=reasoning.policy_version,
            reasoning_reasons=reasoning.reasons,
            response_format=AIResponseFormat.STRUCTURED,
            correlation_id=event.correlation_id,
            language=context.generation_language,
            sensitivity=context.fact_sheet.sensitive_topics,
            input_artifact_ids=(
                f"fact_sheet:{context.fact_sheet.fact_sheet_id}:v{context.fact_sheet.version}",
            ),
            input_hash=context.semantic_key,
            metadata={"input_projection_version": AI_INPUT_PROJECTION_VERSION},
        )
        try:
            routed = await self.router.execute(request, response_validator=validate)
        except AIRoutingExecutionError as exc:
            reasons = {attempt.failure_reason for attempt in exc.attempts}
            if reasons and reasons <= _TRANSIENT_AI_FAILURES:
                raise TransientEventError("content AI provider is temporarily unavailable") from exc
            raise PermanentEventError("content AI output or route is permanently invalid") from exc
        if parsed is None:
            raise PermanentEventError("content AI response was not validated")
        return ContentGenerationExecution(routed, parsed)

    def persist(
        self,
        session: Session,
        *,
        context: ContentGenerationContext,
        event: EventEnvelope,
        execution: ContentGenerationExecution,
    ) -> ContentGenerationResult:
        story = session.scalar(
            select(Story).where(Story.id == context.fact_sheet.story_id).with_for_update()
        )
        if story is None:
            raise PermanentEventError("content Fact Sheet story no longer exists")
        current = self.load_context(session, event)
        if current.semantic_key != context.semantic_key:
            raise StaleWorkError("Fact Sheet or content policy changed during generation")
        existing = self.existing_result(session, current)
        if existing is not None:
            return existing

        response = execution.routed.response
        provider = self.router.registry.get(response.provider)
        model = session.scalar(
            select(AIModel).where(
                AIModel.provider == response.provider, AIModel.model_name == response.model
            )
        )
        if model is None:
            model = AIModel(
                provider=response.provider,
                model_name=response.model,
                locality=provider.capabilities.locality.value,
                capabilities=provider.capabilities.model_dump(mode="json"),
            )
            session.add(model)
            session.flush()
        ai_run = AIRun(
            ai_model_id=model.id,
            task_type=AITaskType.CONTENT_GENERATION.value,
            prompt_id=self.prompt.prompt_id,
            prompt_version=self.prompt.version,
            prompt_checksum=self.prompt.checksum,
            input_artifact_ids=[
                f"fact_sheet:{current.fact_sheet.fact_sheet_id}:v{current.fact_sheet.version}"
            ],
            input_hash=current.semantic_key,
            output_payload=execution.output.model_dump(mode="json"),
            status="SUCCEEDED",
            validation_status="VALIDATED",
            latency_ms=response.latency_ms,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            correlation_id=event.correlation_id,
            routing_attempts=[item.model_dump(mode="json") for item in execution.routed.attempts],
        )
        session.add(ai_run)
        session.flush()
        next_version = (
            session.scalar(
                select(func.max(ContentDraft.version)).where(ContentDraft.story_id == story.id)
            )
            or 0
        ) + 1
        draft = ContentDraft(
            story_id=story.id,
            fact_sheet_id=current.fact_sheet.fact_sheet_id,
            fact_sheet_version=current.fact_sheet.version,
            version=next_version,
            methodology_version=self.style.methodology_version,
            editorial_brief_snapshot=current.brief.model_dump(mode="json"),
            risk_level=current.fact_sheet.risk_level,
            sensitive_topics=list(current.fact_sheet.sensitive_topics),
            review_required=current.brief.human_review_required,
            review_state=ReviewState.NOT_READY,
            created_by_ai_run_id=ai_run.id,
            semantic_key=current.semantic_key,
        )
        session.add(draft)
        session.flush()
        output = execution.output
        claim_ids = tuple(sorted(output.claim_ids_used))
        selected_claims = set(claim_ids)
        source_ids = tuple(
            sorted(
                {
                    item.source_id
                    for item in current.fact_sheet.evidence
                    if item.claim_id in selected_claims and item.source_id is not None
                },
                key=str,
            )
        )
        variant = ContentVariant(
            content_draft_id=draft.id,
            platform=output.platform.value,
            format=output.format.value,
            language=output.language,
            title=output.title,
            body="\n\n".join(f"{slide.heading}\n{slide.body}" for slide in output.slides),
            caption=output.caption,
            structured_payload=output.structured_payload(),
            claim_ids_used=[str(item) for item in claim_ids],
            source_ids_used=[str(item) for item in source_ids],
            media_asset_ids=[],
            review_state=ReviewState.NOT_READY,
            version=1,
        )
        session.add(variant)
        session.flush()
        generated = EventEnvelope(
            event_type=EventType.CONTENT_GENERATED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="content_draft",
            aggregate_id=draft.id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            idempotency_key=f"content.generated:{current.semantic_key}",
            payload={
                "story_id": str(story.id),
                "content_draft_id": str(draft.id),
                "content_variant_ids": [str(variant.id)],
                "ai_run_id": str(ai_run.id),
            },
        )
        session.add(build_outbox_record(generated))
        session.flush()
        return ContentGenerationResult(
            story.id, draft.id, (variant.id,), ai_run.id, generated.event_id, True
        )

    @staticmethod
    def _existing_result(session: Session, draft: ContentDraft) -> ContentGenerationResult:
        variants = tuple(
            session.scalars(
                select(ContentVariant)
                .where(ContentVariant.content_draft_id == draft.id)
                .order_by(ContentVariant.id)
            )
        )
        outbox = session.scalar(
            select(EventOutbox).where(
                EventOutbox.event_type == EventType.CONTENT_GENERATED.value,
                EventOutbox.aggregate_id == draft.id,
            )
        )
        if not variants or outbox is None:
            raise RuntimeError("content semantic result is incomplete")
        return ContentGenerationResult(
            draft.story_id,
            draft.id,
            tuple(item.id for item in variants),
            draft.created_by_ai_run_id,
            outbox.event_id,
            False,
        )


def _content_request(event: EventEnvelope) -> ContentRequestedV1:
    if event.event_type is not EventType.CONTENT_REQUESTED:
        raise PermanentEventError("content worker requires content.requested")
    payload = parse_event_payload(
        event.event_type, event.schema_version, event.payload, ContentRequestedV1
    )
    if event.aggregate_type != "fact_sheet" or event.aggregate_id != payload.fact_sheet_id:
        raise PermanentEventError("content.requested must use its Fact Sheet aggregate")
    return payload


def _single_target(payload: ContentRequestedV1) -> ContentTarget:
    if len(payload.requested_platforms) != 1 or len(payload.requested_formats) != 1:
        raise PermanentEventError("Stage 21 requires exactly one platform and one format")
    try:
        return ContentTarget(
            platform=payload.requested_platforms[0], format=payload.requested_formats[0]
        )
    except ValidationError as exc:
        raise PermanentEventError("unsupported Stage-21 content target") from exc


def _validate_output(
    output: ContentGenerationOutput,
    context: ContentGenerationContext,
    *,
    system_prompt: str,
) -> None:
    if (
        output.story_id != context.fact_sheet.story_id
        or output.fact_sheet_id != context.fact_sheet.fact_sheet_id
        or output.fact_sheet_version != context.fact_sheet.version
        or output.platform != context.target.platform
        or output.format != context.target.format
        or output.language != context.generation_language
    ):
        raise ValueError("content output references the wrong input artifact or target")
    known_claims = {item.claim_id for item in context.brief.claims}
    referenced = {claim_id for slide in output.slides for claim_id in slide.claim_ids}
    if not referenced <= known_claims:
        raise ValueError("content output references an unknown claim")
    claims = {claim.claim_id: claim for claim in context.brief.claims}
    if presentation_errors(output, {identity: claim.values for identity, claim in claims.items()}):
        raise ValueError("content value presentation violates immutable source values")
    for presentation in output.claim_semantic_presentations:
        claim = claims[presentation.claim_id]
        if claim.semantics is None or semantic_presentation_violations(
            presentation, claim.semantics
        ):
            raise ValueError(
                "content claim semantic presentation violates immutable source semantics"
            )
    for presentation in output.claim_presentations:
        claim = claims[presentation.claim_id]
        if presentation_violations(presentation, claim.status, claim.label):
            raise ValueError("content presentation violates immutable certainty ceiling")
    allowed_quote_material = "\n".join(
        [
            context.brief.headline,
            context.brief.summary,
            *(claim.text for claim in context.brief.claims),
            *(excerpt for claim in context.brief.claims for excerpt in claim.evidence_excerpts),
        ]
    )
    normalized_material = _normalize_space(allowed_quote_material)
    generated_text = "\n".join(
        [
            output.title,
            output.caption,
            *(part for slide in output.slides for part in (slide.heading, slide.body)),
        ]
    )
    if _normalize_space(system_prompt).casefold() in _normalize_space(generated_text).casefold():
        raise ValueError("content output must not reproduce the system prompt")
    for quoted in _QUOTED_SPAN.findall(generated_text):
        if _normalize_space(quoted) not in normalized_material:
            raise ValueError("content output contains an unsupported quotation")


def _effective_generation_language(story_language: str | None, default_language: str) -> str:
    if story_language:
        try:
            return normalize_generation_language(story_language)
        except ValueError:
            pass
    return normalize_generation_language(default_language)


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def _semantic_key(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"content-generation:{hashlib.sha256(encoded).hexdigest()}"
