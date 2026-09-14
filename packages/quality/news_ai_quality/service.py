"""Hybrid deterministic and AIRouter-backed Stage-22 quality service."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from news_ai_ai import (
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
from news_ai_content import ContentGenerationOutput, EditorialBrief, content_artifact_hash
from news_ai_content.certainty import CERTAINTY_POLICY_VERSION
from news_ai_content.integrity import quality_artifact
from news_ai_content.media import MediaNotAttachedError, MediaValidationError
from news_ai_database import (
    AIModel,
    AIRun,
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    EventOutbox,
    FactSheet,
    Story,
)
from news_ai_domain import CLAIM_SEMANTICS_POLICY_VERSION, ReviewState
from news_ai_editorial import ContentStyleConfig, PublishingPolicyConfig
from news_ai_events import (
    ContentGeneratedV1,
    EventEnvelope,
    EventType,
    PermanentEventError,
    StaleWorkError,
    TransientEventError,
    parse_event_payload,
)
from news_ai_events.outbox import build_outbox_record
from news_ai_events.reliability import DeferredWorkError
from news_ai_evidence import FactSheetArtifact, FactSheetGenerator
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .contracts import QualityAssessmentOutput, QualityDecision, decide_quality
from .prompt import QualityPrompt
from .semantic import SemanticValidationReport, SemanticValidator, fabricated_quotes

# v6 adds statement type/state preservation; historical checks remain immutable.
QUALITY_METHODOLOGY_VERSION = "quality-gate-methodology-v6"
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")
_TRANSIENT_FAILURES = {
    AIFailureReason.UNAVAILABLE,
    AIFailureReason.TIMEOUT,
    AIFailureReason.RATE_LIMIT,
    AIFailureReason.LOCAL_RESOURCE_EXHAUSTED,
}


class _InvalidQualityMedia(PermanentEventError):
    """Invalid before AI; a changed in-flight media graph is instead stale."""


@dataclass(frozen=True, slots=True)
class QualityVariantContext:
    variant_id: UUID
    variant_version: int
    semantic_key: str
    artifact: dict[str, Any]
    artifact_hash: str
    deterministic_fabricated_quotes: tuple[str, ...]
    semantic_report: SemanticValidationReport


@dataclass(frozen=True, slots=True)
class QualityContext:
    draft_id: UUID
    draft_version: int
    story_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int
    risk_level: str
    sensitive_topics: tuple[str, ...]
    fact_sheet: dict[str, Any]
    editorial_brief: dict[str, Any]
    style: dict[str, Any]
    variants: tuple[QualityVariantContext, ...]

    @property
    def event_semantic_key(self) -> str:
        return _hash([variant.semantic_key for variant in self.variants])


@dataclass(frozen=True, slots=True)
class QualityExecution:
    variant: QualityVariantContext
    routed: AIRoutedResponse
    decision: QualityDecision


@dataclass(frozen=True, slots=True)
class QualityResult:
    draft_id: UUID
    quality_check_ids: tuple[UUID, ...]
    event_id: UUID
    passed: bool
    created: bool

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "content_draft_id": str(self.draft_id),
            "quality_check_ids": [str(item) for item in self.quality_check_ids],
            "event_id": str(self.event_id),
            "passed": self.passed,
            "created": self.created,
        }


class QualityAssessmentService:
    """Assess exact generated artifacts without changing their factual boundary."""

    def __init__(
        self,
        router: AIRouter,
        prompt: QualityPrompt,
        style: ContentStyleConfig,
        publishing_policy: PublishingPolicyConfig,
    ) -> None:
        self.router = router
        self.prompt = prompt
        self.style = style
        self.publishing_policy = publishing_policy
        self.review_required = publishing_policy.mvp.external_publication_requires_human_approval
        self.semantic_validator = SemanticValidator()

    def load_context(
        self, session: Session, event: EventEnvelope, *, lock_media: bool = False
    ) -> QualityContext:
        payload = _quality_request(event)
        draft = session.get(ContentDraft, payload.content_draft_id)
        if draft is None or draft.story_id != payload.story_id:
            raise PermanentEventError("content.generated references a missing or mismatched draft")
        if draft.created_by_ai_run_id != payload.ai_run_id:
            raise PermanentEventError("content.generated AI provenance does not match the draft")
        if draft.review_state not in {ReviewState.NOT_READY, ReviewState.READY_FOR_REVIEW}:
            raise PermanentEventError("quality gate cannot process a human-review state")
        variants = tuple(
            session.scalars(
                select(ContentVariant)
                .where(ContentVariant.content_draft_id == draft.id)
                .order_by(ContentVariant.id)
            )
        )
        if not variants or set(payload.content_variant_ids) != {item.id for item in variants}:
            raise PermanentEventError("content.generated variant set does not match durable state")
        sheet = session.get(FactSheet, draft.fact_sheet_id)
        if (
            sheet is None
            or sheet.story_id != draft.story_id
            or sheet.version != draft.fact_sheet_version
        ):
            raise PermanentEventError("draft does not reference its exact durable Fact Sheet")
        latest_version = session.scalar(
            select(func.max(FactSheet.version)).where(FactSheet.story_id == sheet.story_id)
        )
        if latest_version != sheet.version:
            raise StaleWorkError("content quality references a superseded Fact Sheet")
        if session.get(Story, sheet.story_id) is None:
            raise PermanentEventError("content Fact Sheet story no longer exists")
        try:
            artifact = FactSheetGenerator.artifact_from_row(sheet)
        except (ValidationError, ValueError, TypeError, KeyError):
            raise PermanentEventError("content Fact Sheet artifact is malformed") from None
        fact_sheet_topics = _sensitive_topics(artifact.sensitive_topics, owner="Fact Sheet")
        if any(
            claim.semantics is None
            or claim.semantics.policy_version != CLAIM_SEMANTICS_POLICY_VERSION
            for claim in artifact.claims
        ):
            raise PermanentEventError(
                "CLAIM_SEMANTICS_MISSING: re-extraction/regeneration required"
            )
        draft_topics = _sensitive_topics(draft.sensitive_topics, owner="ContentDraft")
        if draft.risk_level != artifact.risk_level or draft_topics != fact_sheet_topics:
            raise PermanentEventError("draft risk/sensitivity conflicts with its Fact Sheet")
        if not draft.review_required:
            raise PermanentEventError("content draft violates MVP human-review policy")
        try:
            brief_model = EditorialBrief.model_validate(draft.editorial_brief_snapshot)
        except ValidationError as exc:
            raise PermanentEventError("content draft has an invalid Editorial Brief") from exc
        brief_topics = _sensitive_topics(brief_model.sensitive_topics, owner="EditorialBrief")
        if any(claim.semantics is None for claim in brief_model.claims):
            raise PermanentEventError(
                "CLAIM_SEMANTICS_MISSING: Editorial Brief requires regeneration"
            )
        if (
            brief_model.story_id != draft.story_id
            or brief_model.story_id != artifact.story_id
            or brief_model.fact_sheet_id != artifact.fact_sheet_id
            or brief_model.fact_sheet_version != artifact.version
        ):
            raise PermanentEventError("Editorial Brief factual identity is inconsistent")
        if brief_model.risk_level != artifact.risk_level or brief_topics != fact_sheet_topics:
            raise PermanentEventError("Editorial Brief risk/sensitivity conflicts with Fact Sheet")
        if not brief_model.human_review_required or not self.review_required:
            raise PermanentEventError("Editorial Brief violates MVP human-review policy")
        fact_sheet = artifact.model_dump(mode="json")
        brief = brief_model.model_dump(mode="json")
        context_variants = tuple(
            self._variant_context(session, draft, item, fact_sheet, brief, lock_media=lock_media)
            for item in variants
        )
        return QualityContext(
            draft_id=draft.id,
            draft_version=draft.version,
            story_id=draft.story_id,
            fact_sheet_id=sheet.id,
            fact_sheet_version=sheet.version,
            risk_level=artifact.risk_level.value,
            sensitive_topics=fact_sheet_topics,
            fact_sheet=fact_sheet,
            editorial_brief=brief,
            style=self.style.model_dump(mode="json"),
            variants=context_variants,
        )

    def _variant_context(
        self,
        session: Session,
        draft: ContentDraft,
        variant: ContentVariant,
        fact_sheet: dict[str, Any],
        brief: dict[str, Any],
        *,
        lock_media: bool = False,
    ) -> QualityVariantContext:
        language = variant.language.strip().replace("_", "-").lower()
        if _LANGUAGE_RE.fullmatch(language) is None:
            raise PermanentEventError("content variant language is invalid")
        target = brief.get("target", {})
        if variant.platform != target.get("platform") or variant.format != target.get("format"):
            raise PermanentEventError("variant target conflicts with its Editorial Brief")
        if variant.review_state not in {ReviewState.NOT_READY, ReviewState.READY_FOR_REVIEW}:
            raise PermanentEventError("quality gate cannot process a human-review variant state")
        try:
            claims = tuple(str(UUID(item)) for item in variant.claim_ids_used)
            sources = tuple(str(UUID(item)) for item in variant.source_ids_used)
        except (TypeError, ValueError) as exc:
            raise PermanentEventError(
                "variant contains malformed claim/source identifiers"
            ) from exc
        if len(claims) != len(set(claims)) or len(sources) != len(set(sources)):
            raise PermanentEventError("variant contains duplicate claim/source identifiers")
        if not claims:
            raise PermanentEventError("content variant must reference at least one claim")
        known_claims = {item["claim_id"] for item in fact_sheet["claims"]}
        known_sources = {
            item["source_id"] for item in fact_sheet["sources"] if item.get("source_id")
        }
        evidence_pairs = {
            (item["claim_id"], item["source_id"])
            for item in fact_sheet["evidence"]
            if item.get("source_id")
        }
        if not set(claims) <= known_claims or not set(sources) <= known_sources:
            raise PermanentEventError("variant references unknown Fact Sheet claims or sources")
        expected_sources = {
            source_id for claim_id, source_id in evidence_pairs if claim_id in set(claims)
        }
        if set(sources) != expected_sources:
            raise PermanentEventError(
                "variant source provenance does not exactly match selected-claim evidence"
            )
        try:
            structured = dict(variant.structured_payload)
            if set(structured) != {
                "slides",
                "hashtags",
                "claim_ids_used",
                "claim_presentations",
                "claim_semantic_presentations",
            }:
                raise ValueError("carousel structured payload has unexpected fields")
            carousel = ContentGenerationOutput.model_validate(
                {
                    "story_id": draft.story_id,
                    "fact_sheet_id": draft.fact_sheet_id,
                    "fact_sheet_version": draft.fact_sheet_version,
                    "platform": variant.platform,
                    "format": variant.format,
                    "language": language,
                    "title": variant.title,
                    "slides": structured["slides"],
                    "caption": variant.caption,
                    "hashtags": structured["hashtags"],
                    "claim_ids_used": structured["claim_ids_used"],
                    "claim_presentations": structured["claim_presentations"],
                    "claim_semantic_presentations": structured["claim_semantic_presentations"],
                }
            )
        except (KeyError, TypeError, ValidationError, ValueError) as exc:
            raise PermanentEventError("content variant carousel payload is invalid") from exc
        carousel_claims = tuple(str(item) for item in carousel.claim_ids_used)
        if set(carousel_claims) != set(claims):
            raise PermanentEventError("carousel claim provenance conflicts with durable variant")
        expected_body = "\n\n".join(f"{slide.heading}\n{slide.body}" for slide in carousel.slides)
        if variant.body != expected_body:
            raise PermanentEventError("variant body conflicts with its carousel slides")
        try:
            artifact = quality_artifact(session, variant, lock_media=lock_media)
        except MediaNotAttachedError:
            raise DeferredWorkError("required caller media attachment is pending") from None
        except MediaValidationError:
            raise _InvalidQualityMedia("content variant caller media is invalid") from None
        semantic_report = self.semantic_validator.validate(
            FactSheetArtifact.model_validate(fact_sheet),
            carousel,
            source_ids_used=tuple(UUID(item) for item in sources),
            editorial_brief=EditorialBrief.model_validate(brief),
        )
        semantic_key = _hash(
            {
                "content_draft_id": draft.id,
                "content_draft_version": draft.version,
                "fact_sheet_id": draft.fact_sheet_id,
                "fact_sheet_version": draft.fact_sheet_version,
                "variant": artifact,
                "editorial_brief": brief,
                "style": self.style.model_dump(mode="json"),
                "methodology_version": QUALITY_METHODOLOGY_VERSION,
                "semantic_methodology_version": semantic_report.methodology_version,
                "certainty_policy_version": CERTAINTY_POLICY_VERSION,
                "claim_semantics_policy_version": CLAIM_SEMANTICS_POLICY_VERSION,
                "semantic_inputs": fact_sheet,
                "prompt_id": self.prompt.prompt_id,
                "prompt_version": self.prompt.version,
                "prompt_checksum": self.prompt.checksum,
            }
        )
        return QualityVariantContext(
            variant.id,
            variant.version,
            semantic_key,
            artifact,
            content_artifact_hash(artifact),
            fabricated_quotes(carousel, semantic_report),
            semantic_report,
        )

    def existing_result(self, session: Session, context: QualityContext) -> QualityResult | None:
        keys = tuple(item.semantic_key for item in context.variants)
        checks = tuple(
            session.scalars(
                select(ContentQualityCheck).where(ContentQualityCheck.semantic_key.in_(keys))
            )
        )
        event = session.scalar(
            select(EventOutbox).where(
                EventOutbox.event_type == EventType.CONTENT_QUALITY_CHECKED.value,
                EventOutbox.idempotency_key == self._event_idempotency_key(context),
            )
        )
        checks_by_key = {item.semantic_key: item for item in checks}
        if (
            len(checks_by_key) != len(keys)
            or event is None
            or any(
                check.methodology_version != QUALITY_METHODOLOGY_VERSION
                or check.content_artifact_hash != variant.artifact_hash
                or check.semantic_methodology_version != variant.semantic_report.methodology_version
                or check.semantic_validation_passed != variant.semantic_report.passed
                or check.semantic_findings != variant.semantic_report.model_dump(mode="json")
                for variant in context.variants
                if (check := checks_by_key.get(variant.semantic_key)) is not None
            )
        ):
            return None
        return QualityResult(
            context.draft_id,
            tuple(item.id for item in checks),
            event.event_id,
            all(item.passed for item in checks),
            False,
        )

    async def assess(
        self, context: QualityContext, event: EventEnvelope
    ) -> tuple[QualityExecution, ...]:
        results: list[QualityExecution] = []
        for variant in context.variants:
            parsed: QualityAssessmentOutput | None = None

            def validate(response: AIResponse, expected=variant) -> None:
                nonlocal parsed
                try:
                    output = QualityAssessmentOutput.model_validate(response.structured)
                    if output.content_variant_id != expected.variant_id:
                        raise ValueError("quality output references the wrong variant")
                    used_claims = set(expected.artifact["claim_ids_used"])
                    slides = expected.artifact["structured_payload"]["slides"]
                    allowed_paths = {"title", "caption"} | {
                        f"slides[{i}].{field}"
                        for i in range(len(slides))
                        for field in ("heading", "body")
                    }
                    for escalation in (
                        *output.certainty_escalations,
                        *output.claim_semantic_escalations,
                    ):
                        if (
                            str(escalation.claim_id) not in used_claims
                            or escalation.artifact_path not in allowed_paths
                        ):
                            raise ValueError(
                                "certainty escalation references an unknown content location"
                            )
                        if escalation.artifact_path.startswith("slides["):
                            slide_index = int(
                                escalation.artifact_path.split("[", 1)[1].split("]", 1)[0]
                            )
                            if str(escalation.claim_id) not in slides[slide_index]["claim_ids"]:
                                raise ValueError(
                                    "certainty escalation refers to an unrelated slide claim"
                                )
                except (ValidationError, ValueError) as exc:
                    raise AIInvalidResponseError(
                        "quality output failed contract validation"
                    ) from exc
                parsed = output

            request = AIRequest(
                task_type=AITaskType.QUALITY_CHECKING,
                system_prompt=self.prompt.system_prompt,
                input={
                    "immutable_fact_sheet": context.fact_sheet,
                    "content_artifact": {
                        key: value
                        for key, value in variant.artifact.items()
                        if key != "media_provenance"
                    },
                    "editorial_brief": context.editorial_brief,
                    "risk_level": context.risk_level,
                    "sensitive_topics": context.sensitive_topics,
                    "content_style": context.style,
                    "quality_methodology_version": QUALITY_METHODOLOGY_VERSION,
                    "certainty_policy_version": CERTAINTY_POLICY_VERSION,
                    "claim_semantics_policy_version": CLAIM_SEMANTICS_POLICY_VERSION,
                },
                prompt=PromptReference(
                    prompt_id=self.prompt.prompt_id,
                    version=self.prompt.version,
                    checksum=self.prompt.checksum,
                ),
                response_format=AIResponseFormat.STRUCTURED,
                correlation_id=event.correlation_id,
                language=variant.artifact["language"],
                sensitivity=context.sensitive_topics,
                input_artifact_ids=(
                    f"fact_sheet:{context.fact_sheet_id}:v{context.fact_sheet_version}",
                    f"content_variant:{variant.variant_id}:v{variant.variant_version}",
                ),
                input_hash=variant.semantic_key,
            )
            try:
                routed = await self.router.execute(request, response_validator=validate)
            except AIRoutingExecutionError as exc:
                reasons = {attempt.failure_reason for attempt in exc.attempts}
                if reasons and reasons <= _TRANSIENT_FAILURES:
                    raise TransientEventError(
                        "quality AI provider is temporarily unavailable"
                    ) from exc
                raise PermanentEventError(
                    "quality AI output or route is permanently invalid"
                ) from exc
            if parsed is None:
                raise PermanentEventError("quality AI response was not validated")
            results.append(
                QualityExecution(
                    variant,
                    routed,
                    decide_quality(
                        parsed,
                        deterministic_quotes=variant.deterministic_fabricated_quotes,
                        review_required=self.review_required,
                        semantic_report=variant.semantic_report,
                    ),
                )
            )
        return tuple(results)

    def persist(
        self,
        session: Session,
        *,
        context: QualityContext,
        event: EventEnvelope,
        executions: tuple[QualityExecution, ...],
    ) -> QualityResult:
        story = session.scalar(
            select(Story)
            .where(Story.id == context.story_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if story is None:
            raise PermanentEventError("content Fact Sheet story no longer exists")
        draft = session.scalar(
            select(ContentDraft)
            .where(ContentDraft.id == context.draft_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if draft is None:
            raise PermanentEventError("content draft no longer exists")
        tuple(
            session.scalars(
                select(ContentVariant)
                .where(ContentVariant.content_draft_id == context.draft_id)
                .order_by(ContentVariant.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        try:
            current = self.load_context(session, event, lock_media=True)
        except (_InvalidQualityMedia, DeferredWorkError):
            raise StaleWorkError("content quality inputs changed during assessment") from None
        if current.event_semantic_key != context.event_semantic_key:
            raise StaleWorkError("content quality inputs changed during assessment")
        existing = self.existing_result(session, current)
        if existing is not None:
            return existing
        if {item.variant.variant_id for item in executions} != {
            item.variant_id for item in current.variants
        }:
            raise StaleWorkError("quality execution no longer matches current variants")
        current_variants = {item.variant_id: item for item in current.variants}
        if any(item.variant != current_variants[item.variant.variant_id] for item in executions):
            raise StaleWorkError("quality execution inputs no longer match current variants")
        checks: list[ContentQualityCheck] = []
        for execution in executions:
            if len(execution.variant.artifact_hash) != 64:
                raise PermanentEventError(
                    "current quality methodology requires exact content hash provenance"
                )
            run = self._persist_ai_run(session, execution, current, event)
            decision = execution.decision
            check = ContentQualityCheck(
                content_draft_id=current.draft_id,
                content_variant_id=execution.variant.variant_id,
                content_variant_version=execution.variant.variant_version,
                fact_sheet_id=current.fact_sheet_id,
                fact_sheet_version=current.fact_sheet_version,
                methodology_version=QUALITY_METHODOLOGY_VERSION,
                content_artifact_hash=execution.variant.artifact_hash,
                ai_run_id=run.id,
                semantic_key=execution.variant.semantic_key,
                semantic_validation_passed=execution.variant.semantic_report.passed,
                semantic_methodology_version=execution.variant.semantic_report.methodology_version,
                semantic_findings=execution.variant.semantic_report.model_dump(mode="json"),
                **decision.model_dump(mode="json", exclude={"content_variant_id"}),
            )
            session.add(check)
            checks.append(check)
            variant = session.get(ContentVariant, execution.variant.variant_id)
            variant.review_state = (
                ReviewState.READY_FOR_REVIEW if decision.passed else ReviewState.NOT_READY
            )
        session.flush()
        passed = all(item.passed for item in checks)
        draft = session.get(ContentDraft, current.draft_id)
        draft.review_state = ReviewState.READY_FOR_REVIEW if passed else ReviewState.NOT_READY
        fact_passed = all(
            item.factual_accuracy_passed
            and item.semantic_validation_passed is True
            and not any(
                (
                    item.unsupported_claims,
                    item.fabricated_quotes,
                    item.incorrect_names,
                    item.incorrect_dates,
                    item.incorrect_numbers,
                    item.missing_context,
                    item.certainty_escalations,
                    item.claim_semantic_escalations,
                )
            )
            and not item.defamation_risk
            for item in checks
        )
        source_passed = all(
            item.source_alignment_passed and item.citation_alignment_passed for item in checks
        )
        style_passed = all(item.style_passed and not item.sensitive_topic_error for item in checks)
        envelope = EventEnvelope(
            event_type=EventType.CONTENT_QUALITY_CHECKED,
            producer="ai-worker",
            producer_version="0.1.0",
            aggregate_type="content_draft",
            aggregate_id=draft.id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            idempotency_key=self._event_idempotency_key(current),
            payload={
                "content_draft_id": str(draft.id),
                "passed": passed,
                "fact_check_passed": fact_passed,
                "source_check_passed": source_passed,
                "style_check_passed": style_passed,
                "risk_level": current.risk_level,
                "review_required": self.review_required,
            },
        )
        session.add(build_outbox_record(envelope))
        session.flush()
        return QualityResult(
            draft.id, tuple(item.id for item in checks), envelope.event_id, passed, True
        )

    def _persist_ai_run(
        self,
        session: Session,
        execution: QualityExecution,
        context: QualityContext,
        event: EventEnvelope,
    ) -> AIRun:
        response = execution.routed.response
        provider = self.router.registry.get(response.provider)
        model = session.scalar(
            select(AIModel).where(
                AIModel.provider == response.provider,
                AIModel.model_name == response.model,
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
        run = AIRun(
            ai_model_id=model.id,
            task_type=AITaskType.QUALITY_CHECKING.value,
            prompt_id=self.prompt.prompt_id,
            prompt_version=self.prompt.version,
            prompt_checksum=self.prompt.checksum,
            input_artifact_ids=[
                f"fact_sheet:{context.fact_sheet_id}:v{context.fact_sheet_version}",
                f"content_variant:{execution.variant.variant_id}:v{execution.variant.variant_version}",
            ],
            input_hash=execution.variant.semantic_key,
            output_payload=execution.routed.response.structured,
            status="SUCCEEDED",
            validation_status="VALIDATED",
            latency_ms=response.latency_ms,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            correlation_id=event.correlation_id,
            routing_attempts=[item.model_dump(mode="json") for item in execution.routed.attempts],
        )
        session.add(run)
        session.flush()
        return run

    @staticmethod
    def _event_idempotency_key(context: QualityContext) -> str:
        return (
            f"content.quality_checked:{context.draft_id}:{context.draft_version}:"
            f"{QUALITY_METHODOLOGY_VERSION}:{context.event_semantic_key}"
        )


def _quality_request(event: EventEnvelope) -> ContentGeneratedV1:
    if event.event_type is not EventType.CONTENT_GENERATED:
        raise PermanentEventError("quality worker requires content.generated")
    payload = parse_event_payload(
        event.event_type, event.schema_version, event.payload, ContentGeneratedV1
    )
    if event.aggregate_type != "content_draft" or event.aggregate_id != payload.content_draft_id:
        raise PermanentEventError("content.generated must use its ContentDraft aggregate")
    return payload


def _sensitive_topics(value: Any, *, owner: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise PermanentEventError(f"{owner} sensitive topics are invalid")
    normalized = tuple(item.strip() for item in value)
    if len(normalized) != len(set(normalized)):
        raise PermanentEventError(f"{owner} sensitive topics contain duplicates")
    return tuple(sorted(normalized))


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()
