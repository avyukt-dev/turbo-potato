"""Claim extraction contracts and durable persistence orchestration."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from news_ai_database import (
    AIModel,
    AIRun,
    Article,
    ArticleVersion,
    Claim,
    EventOutbox,
    Story,
    StorySource,
)
from news_ai_domain import (
    CLAIM_SEMANTICS_POLICY_VERSION,
    ClaimSemanticState,
    ClaimSemanticType,
    ClaimVerificationStatus,
    RiskLevel,
)
from news_ai_domain.values import (
    VALUE_INTEGRITY_POLICY_VERSION,
    ClaimValueAnchor,
    ClaimValueCandidate,
    ClaimValueKind,
    anchors_for_claim,
    mechanical_span_present,
)
from news_ai_editorial import MandatoryReviewCategory
from news_ai_events import EventEnvelope, EventType, PermanentEventError
from news_ai_events.outbox import build_outbox_record
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import (
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AIResponseSchema,
    AITaskType,
    PromptReference,
)
from .provider import AIInvalidResponseError
from .reasoning import REASONING_ROUTING_POLICY_VERSION, select_reasoning_effort
from .routing import AIRoutedResponse, AIRouter

CLAIM_EXTRACTION_METHODOLOGY_VERSION = "claim-extraction-methodology-v5"


class ClaimExtractionItem(BaseModel):
    """One atomic claim candidate produced by the model.

    This is extraction metadata only. Factual verification status is assigned by the later evidence
    and fact-check stages; persisted claims always start UNASSESSED.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_text: str = Field(min_length=1, max_length=4000)
    semantic_type: ClaimSemanticType
    semantic_state: ClaimSemanticState
    value_candidates: tuple[ClaimValueCandidate, ...] = Field(max_length=100)
    importance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_level: RiskLevel = RiskLevel.LOW
    sensitive_topics: tuple[MandatoryReviewCategory, ...] = ()
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None

    @field_validator("claim_text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("claim_text must not be blank")
        return value

    @field_validator("sensitive_topics")
    @classmethod
    def validate_sensitive_topics(
        cls, value: tuple[MandatoryReviewCategory, ...]
    ) -> tuple[MandatoryReviewCategory, ...]:
        if len(value) != len(set(value)):
            raise ValueError("sensitive_topics must be unique")
        return value

    @field_validator("temporal_start", "temporal_end")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("claim timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_temporal_range(self) -> ClaimExtractionItem:
        if any(
            not mechanical_span_present(self.claim_text, item.source_text)
            for item in self.value_candidates
        ):
            raise ValueError("value source span is absent from atomic claim")
        materials = [item.model_dump_json() for item in self.value_candidates]
        if len(materials) != len(set(materials)):
            raise ValueError("value candidates must be unique")
        if self.temporal_start and self.temporal_end and self.temporal_start > self.temporal_end:
            raise ValueError("temporal_start must not be after temporal_end")
        return self


class ClaimExtractionOutput(BaseModel):
    """Strict model output for claim extraction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: tuple[ClaimExtractionItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_claims(self) -> ClaimExtractionOutput:
        normalized = [_normalize_claim_text(item.claim_text) for item in self.claims]
        if len(normalized) != len(set(normalized)):
            raise ValueError("claim extraction output contains duplicate claims")
        return self


def _conservative_value_fallback(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace invalid canonical guesses with exact-copy-only source annotations."""

    normalized = deepcopy(payload)
    claims = normalized.get("claims")
    if not isinstance(claims, list):
        return normalized
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("claim_text"), str):
            continue
        candidates = claim.get("value_candidates")
        if not isinstance(candidates, list):
            continue
        for index, candidate in enumerate(candidates):
            try:
                ClaimValueCandidate.model_validate(candidate)
                continue
            except ValidationError:
                pass
            if not isinstance(candidate, dict):
                continue
            source_text = candidate.get("source_text")
            value = candidate.get("value")
            if (
                not isinstance(source_text, str)
                or not mechanical_span_present(claim["claim_text"], source_text)
                or not isinstance(value, dict)
            ):
                continue
            kind = value.get("kind")
            if kind == "EXACT_COPY_ONLY":
                kind = value.get("value_kind")
            try:
                value_kind = ClaimValueKind(kind)
                candidates[index] = ClaimValueCandidate(
                    source_text=source_text,
                    value={"kind": "EXACT_COPY_ONLY", "value_kind": value_kind},
                ).model_dump(mode="json")
            except (TypeError, ValueError, ValidationError):
                continue
    return normalized


class StoryArticleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    article_id: UUID
    article_version_id: UUID
    source_id: UUID
    title: str | None = None
    canonical_url: str
    language: str | None = None
    published_at: datetime | None = None
    content_hash: str
    content: str | None = None


class StoryClaimContext(BaseModel):
    """Stable snapshot sent to the claim-extraction model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    story_id: UUID
    headline: str | None = None
    summary: str | None = None
    language: str | None = None
    sensitive_topics: tuple[str, ...] = ()
    articles: tuple[StoryArticleInput, ...]
    context_hash: str

    def ai_input(self) -> dict[str, Any]:
        return {
            "story_id": str(self.story_id),
            "headline": self.headline,
            "summary": self.summary,
            "language": self.language,
            "articles": [article.model_dump(mode="json") for article in self.articles],
        }


@dataclass(frozen=True, slots=True)
class ClaimExtractionPrompt:
    prompt_id: str
    version: str
    checksum: str
    system_prompt: str

    @classmethod
    def load(cls, path: Path, *, version: str = "v3") -> ClaimExtractionPrompt:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("claim extraction prompt must not be blank")
        return cls(
            prompt_id="claim-extraction",
            version=version,
            checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            system_prompt=text,
        )


@dataclass(frozen=True, slots=True)
class ClaimExtractionExecution:
    routed: AIRoutedResponse
    output: ClaimExtractionOutput


@dataclass(frozen=True, slots=True)
class ClaimExtractionResult:
    story_id: UUID
    claim_ids: tuple[UUID, ...]
    created_claims: int
    reused_claims: int
    ai_run_id: UUID
    model_id: UUID
    event_id: UUID

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "story_id": str(self.story_id),
            "claim_ids": [str(claim_id) for claim_id in self.claim_ids],
            "created_claims": self.created_claims,
            "reused_claims": self.reused_claims,
            "ai_run_id": str(self.ai_run_id),
            "model_id": str(self.model_id),
            "event_id": str(self.event_id),
        }


class StaleStoryContextError(RuntimeError):
    """The story changed after model input was prepared."""


class ClaimExtractionService:
    """Load story state, execute AI outside a DB transaction, then persist atomically."""

    def __init__(
        self,
        router: AIRouter,
        prompt: ClaimExtractionPrompt,
        *,
        producer: str = "ai-worker",
        producer_version: str = "0.1.0",
    ) -> None:
        self.router = router
        self.prompt = prompt
        self.producer = producer
        self.producer_version = producer_version

    def operation_identity(self, context_hash: str) -> str:
        material = {
            "context_hash": context_hash,
            "methodology_version": CLAIM_EXTRACTION_METHODOLOGY_VERSION,
            "semantic_policy_version": CLAIM_SEMANTICS_POLICY_VERSION,
            "value_policy_version": VALUE_INTEGRITY_POLICY_VERSION,
            "reasoning_policy_version": REASONING_ROUTING_POLICY_VERSION,
            "prompt_id": self.prompt.prompt_id,
            "prompt_version": self.prompt.version,
            "prompt_checksum": self.prompt.checksum,
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def load_context(
        self,
        session: Session,
        story_id: UUID,
        *,
        lock_story: bool = False,
    ) -> StoryClaimContext:
        statement = select(Story).where(Story.id == story_id)
        if lock_story:
            statement = statement.with_for_update()
        story = session.scalar(statement)
        if story is None:
            raise ValueError(f"story {story_id} does not exist")

        article_ids = list(
            session.scalars(
                select(StorySource.article_id)
                .where(StorySource.story_id == story_id)
                .order_by(StorySource.article_id.asc())
            )
        )
        if not article_ids:
            raise ValueError("story has no source articles")

        articles: list[StoryArticleInput] = []
        for article_id in article_ids:
            article = session.get(Article, article_id)
            if article is None:
                raise ValueError(f"story references missing article {article_id}")
            version = session.scalar(
                select(ArticleVersion)
                .where(ArticleVersion.article_id == article.id)
                .order_by(ArticleVersion.version_number.desc())
                .limit(1)
            )
            if version is None:
                raise ValueError(f"article {article.id} has no persisted version")
            if not version.body or not version.body.strip():
                raise ValueError("claim extraction requires usable source article body")
            articles.append(
                StoryArticleInput(
                    article_id=article.id,
                    article_version_id=version.id,
                    source_id=article.source_id,
                    title=article.title,
                    canonical_url=article.canonical_url,
                    language=article.language,
                    published_at=article.published_at,
                    content_hash=version.content_hash,
                    content=version.body,
                )
            )

        sensitive_topics = _story_sensitive_topics(story.story_metadata)
        material = {
            "story_id": str(story.id),
            "headline": story.canonical_headline,
            "summary": story.summary,
            "language": story.language,
            "sensitive_topics": list(sensitive_topics),
            "articles": [article.model_dump(mode="json") for article in articles],
        }
        context_hash = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return StoryClaimContext(
            story_id=story.id,
            headline=story.canonical_headline,
            summary=story.summary,
            language=story.language,
            sensitive_topics=sensitive_topics,
            articles=tuple(articles),
            context_hash=context_hash,
        )

    def existing_result(
        self,
        session: Session,
        *,
        story_id: UUID,
        context_hash: str,
    ) -> dict[str, Any] | None:
        row = session.scalar(
            select(EventOutbox)
            .where(
                EventOutbox.event_type == EventType.CLAIMS_EXTRACTED.value,
                EventOutbox.aggregate_id == story_id,
                EventOutbox.idempotency_key
                == (f"claims.extracted:{story_id}:{self.operation_identity(context_hash)}"),
            )
            .order_by(EventOutbox.created_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        for identity in row.payload.get("claim_ids", []):
            claim = session.get(Claim, UUID(identity))
            if (
                claim is None
                or claim.story_id != story_id
                or claim.semantic_type is None
                or claim.semantic_state is None
                or claim.semantic_ai_run_id is None
                or claim.semantic_policy_version != CLAIM_SEMANTICS_POLICY_VERSION
            ):
                raise PermanentEventError("CLAIM_SEMANTICS_MISSING")
            if (
                claim.value_anchors is None
                or claim.value_policy_version != VALUE_INTEGRITY_POLICY_VERSION
                or claim.value_ai_run_id is None
            ):
                raise PermanentEventError("CLAIM_VALUES_MISSING")
        return {
            "story_id": str(story_id),
            "claim_ids": list(row.payload.get("claim_ids", [])),
            "ai_run_id": row.payload.get("ai_run_id"),
            "model_id": row.payload.get("model_id"),
            "event_id": str(row.event_id),
            "reused_context": True,
        }

    async def generate(
        self,
        context: StoryClaimContext,
        triggering_event: EventEnvelope,
    ) -> ClaimExtractionExecution:
        _validate_triggering_event(triggering_event, context.story_id)
        parsed: ClaimExtractionOutput | None = None

        def validate_response(response: AIResponse) -> None:
            nonlocal parsed
            try:
                parsed = ClaimExtractionOutput.model_validate(
                    _conservative_value_fallback(response.structured)
                )
            except ValidationError as exc:
                raise AIInvalidResponseError(
                    "claim extraction output failed schema validation"
                ) from exc

        reasoning = select_reasoning_effort()
        request = AIRequest(
            task_type=AITaskType.CLAIM_EXTRACTION,
            system_prompt=self.prompt.system_prompt,
            input=context.ai_input(),
            prompt=PromptReference(
                prompt_id=self.prompt.prompt_id,
                version=self.prompt.version,
                checksum=self.prompt.checksum,
            ),
            reasoning_effort=reasoning.effort,
            reasoning_policy_version=reasoning.policy_version,
            reasoning_reasons=reasoning.reasons,
            response_format=AIResponseFormat.STRUCTURED,
            response_schema=AIResponseSchema(
                name="claim_extraction",
                json_schema=ClaimExtractionOutput.model_json_schema(),
            ),
            correlation_id=triggering_event.correlation_id,
            language=context.language,
            sensitivity=context.sensitive_topics,
            input_artifact_ids=(
                f"story:{context.story_id}",
                *(f"article_version:{article.article_version_id}" for article in context.articles),
            ),
            input_hash=self.operation_identity(context.context_hash),
        )
        routed = await self.router.execute(request, response_validator=validate_response)
        if parsed is None:
            raise AIInvalidResponseError("claim extraction response was not validated")
        return ClaimExtractionExecution(routed=routed, output=parsed)

    def persist(
        self,
        session: Session,
        *,
        context: StoryClaimContext,
        triggering_event: EventEnvelope,
        execution: ClaimExtractionExecution,
    ) -> ClaimExtractionResult:
        _validate_triggering_event(triggering_event, context.story_id)
        current = self.load_context(session, context.story_id, lock_story=True)
        if current.context_hash != context.context_hash:
            raise StaleStoryContextError("story source material changed during claim extraction")

        # The Story lock serializes semantic completion, even with a different event UUID.
        existing = self.existing_result(
            session, story_id=context.story_id, context_hash=context.context_hash
        )
        if existing is not None:
            return ClaimExtractionResult(
                story_id=context.story_id,
                claim_ids=tuple(UUID(item) for item in existing["claim_ids"]),
                created_claims=0,
                reused_claims=len(existing["claim_ids"]),
                ai_run_id=UUID(existing["ai_run_id"]),
                model_id=UUID(existing["model_id"]),
                event_id=UUID(existing["event_id"]),
            )

        response = execution.routed.response
        provider = self.router.registry.get(response.provider)
        model = self._get_or_create_model(
            session,
            provider_id=response.provider,
            model_name=response.model,
            locality=provider.capabilities.locality.value,
            capabilities=provider.capabilities.model_dump(mode="json"),
        )
        ai_run = AIRun(
            ai_model_id=model.id,
            task_type=AITaskType.CLAIM_EXTRACTION.value,
            prompt_id=self.prompt.prompt_id,
            prompt_version=self.prompt.version,
            prompt_checksum=self.prompt.checksum,
            input_artifact_ids=[
                f"story:{context.story_id}",
                *(f"article_version:{article.article_version_id}" for article in context.articles),
            ],
            input_hash=self.operation_identity(context.context_hash),
            output_payload=execution.output.model_dump(mode="json"),
            status="SUCCEEDED",
            validation_status="VALIDATED",
            latency_ms=response.latency_ms,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            correlation_id=triggering_event.correlation_id,
            routing_attempts=[
                attempt.model_dump(mode="json") for attempt in execution.routed.attempts
            ],
        )
        session.add(ai_run)
        session.flush()

        existing_claims = list(
            session.scalars(
                select(Claim).where(Claim.story_id == context.story_id).with_for_update()
            )
        )
        by_normalized: dict[str, Claim] = {}
        for claim in existing_claims:
            if claim.normalized_claim is None:
                continue
            if claim.normalized_claim in by_normalized:
                raise RuntimeError("story contains duplicate normalized claims")
            by_normalized[claim.normalized_claim] = claim

        claim_ids: list[UUID] = []
        created_claims = 0
        reused_claims = 0
        for item in execution.output.claims:
            normalized = _normalize_claim_text(item.claim_text)
            claim = by_normalized.get(normalized)
            if claim is None:
                claim = Claim(
                    story_id=context.story_id,
                    claim_text=item.claim_text,
                    normalized_claim=normalized,
                    claim_type=item.semantic_type.value,
                    semantic_type=item.semantic_type,
                    semantic_state=item.semantic_state,
                    semantic_policy_version=CLAIM_SEMANTICS_POLICY_VERSION,
                    semantic_ai_run_id=ai_run.id,
                    status=ClaimVerificationStatus.UNASSESSED,
                    confidence_score=None,
                    importance_score=(
                        Decimal(str(item.importance_score))
                        if item.importance_score is not None
                        else None
                    ),
                    risk_level=item.risk_level,
                    temporal_start=item.temporal_start,
                    temporal_end=item.temporal_end,
                    created_by_ai_run_id=ai_run.id,
                    claim_metadata={
                        "sensitive_topics": list(item.sensitive_topics),
                        "extraction_ai_run_ids": [str(ai_run.id)],
                        "extraction_context_hash": context.context_hash,
                    },
                )
                session.add(claim)
                session.flush()
                by_normalized[normalized] = claim
                created_claims += 1
            else:
                if claim.semantic_policy_version is not None:
                    if (
                        claim.semantic_policy_version != CLAIM_SEMANTICS_POLICY_VERSION
                        or claim.semantic_type != item.semantic_type
                        or claim.semantic_state != item.semantic_state
                    ):
                        raise PermanentEventError("CLAIM_SEMANTICS_CONFLICT")
                else:
                    claim.semantic_type = item.semantic_type
                    claim.semantic_state = item.semantic_state
                    claim.semantic_policy_version = CLAIM_SEMANTICS_POLICY_VERSION
                    claim.semantic_ai_run_id = ai_run.id
                metadata = dict(claim.claim_metadata or {})
                run_ids = list(metadata.get("extraction_ai_run_ids", []))
                if str(ai_run.id) not in run_ids:
                    run_ids.append(str(ai_run.id))
                metadata["extraction_ai_run_ids"] = run_ids
                claim.claim_metadata = metadata
                reused_claims += 1
            anchors = anchors_for_claim(claim.id, item.value_candidates)
            material = [anchor.model_dump(mode="json") for anchor in anchors]
            if claim.value_policy_version is not None:
                existing_values = sorted(
                    json.dumps(
                        ClaimValueAnchor.model_validate(anchor).value.model_dump(mode="json"),
                        sort_keys=True,
                    )
                    for anchor in claim.value_anchors or []
                )
                returned_values = sorted(
                    json.dumps(anchor.value.model_dump(mode="json"), sort_keys=True)
                    for anchor in anchors
                )
                if (
                    claim.value_policy_version != VALUE_INTEGRITY_POLICY_VERSION
                    or existing_values != returned_values
                ):
                    raise PermanentEventError("CLAIM_VALUES_CONFLICT")
            else:
                claim.value_anchors = material
                claim.value_policy_version = VALUE_INTEGRITY_POLICY_VERSION
                claim.value_ai_run_id = ai_run.id
            claim_ids.append(claim.id)

        event = EventEnvelope(
            event_type=EventType.CLAIMS_EXTRACTED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="story",
            aggregate_id=context.story_id,
            correlation_id=triggering_event.correlation_id,
            causation_id=triggering_event.event_id,
            idempotency_key=(
                f"claims.extracted:{context.story_id}:{self.operation_identity(context.context_hash)}"
            ),
            payload={
                "story_id": str(context.story_id),
                "claim_ids": [str(claim_id) for claim_id in claim_ids],
                "ai_run_id": str(ai_run.id),
                "model_id": str(model.id),
            },
        )
        session.add(build_outbox_record(event))
        session.flush()
        return ClaimExtractionResult(
            story_id=context.story_id,
            claim_ids=tuple(claim_ids),
            created_claims=created_claims,
            reused_claims=reused_claims,
            ai_run_id=ai_run.id,
            model_id=model.id,
            event_id=event.event_id,
        )

    @staticmethod
    def _get_or_create_model(
        session: Session,
        *,
        provider_id: str,
        model_name: str,
        locality: str,
        capabilities: dict[str, Any],
    ) -> AIModel:
        model = session.scalar(
            select(AIModel)
            .where(AIModel.provider == provider_id, AIModel.model_name == model_name)
            .with_for_update()
        )
        if model is not None:
            return model
        model = AIModel(
            provider=provider_id,
            model_name=model_name,
            locality=locality,
            capabilities=capabilities,
            enabled=True,
            model_metadata={},
        )
        session.add(model)
        session.flush()
        return model


def _validate_triggering_event(event: EventEnvelope, story_id: UUID) -> None:
    if event.event_type not in {EventType.STORY_CREATED, EventType.STORY_CLUSTERED}:
        raise ValueError(f"claim extraction cannot be caused by {event.event_type.value}")
    if event.aggregate_type != "story" or event.aggregate_id != story_id:
        raise ValueError("story event aggregate must match claim extraction story")
    payload_story_id = event.payload.get("story_id")
    if payload_story_id is None or UUID(str(payload_story_id)) != story_id:
        raise ValueError("story event payload story_id must match aggregate_id")


def _story_sensitive_topics(metadata: dict[str, Any]) -> tuple[str, ...]:
    value = metadata.get("sensitive_topics", [])
    if not isinstance(value, list):
        raise ValueError("story sensitive_topics metadata must be a list")
    normalized = tuple(str(item).strip().upper() for item in value)
    if any(not item for item in normalized):
        raise ValueError("story sensitive_topics must not contain blank values")
    if len(normalized) != len(set(normalized)):
        raise ValueError("story sensitive_topics must be unique")
    return normalized


def _normalize_claim_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
