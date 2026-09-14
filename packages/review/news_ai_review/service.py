"""Transactional exact-version human review and evidence-packet queries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from news_ai_content import EditorialBrief, content_artifact_hash
from news_ai_content.integrity import quality_artifact
from news_ai_content.media import MediaValidationError, load_media_provenance
from news_ai_database import (
    AIModel,
    AIRun,
    AuditLog,
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    FactSheet,
    ReviewDecisionRecord,
    Story,
)
from news_ai_domain import ReviewState, RiskLevel
from news_ai_editorial import PublishingPolicyConfig
from news_ai_evidence import FactSheetArtifact
from news_ai_quality import QUALITY_METHODOLOGY_VERSION
from pydantic import ValidationError
from sqlalchemy import String, and_, case, cast, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from .contracts import (
    TERMINAL_DECISIONS,
    ArtifactType,
    ReviewActionRequest,
    ReviewActionResult,
    ReviewDetail,
    ReviewerPrincipal,
    ReviewQueueItem,
    ReviewQueuePage,
    SafeAIProvenance,
)
from .errors import (
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewPreconditionError,
    ReviewValidationError,
    UnsupportedArtifactError,
)

SessionFactory = Callable[[], Session]
_REVIEWABLE = frozenset({ReviewState.READY_FOR_REVIEW, ReviewState.IN_REVIEW})
_QUEUE_BATCH_MIN = 50
_QUEUE_BATCH_MAX = 200


@dataclass(frozen=True, slots=True)
class _ReviewGraph:
    story: Story
    draft: ContentDraft
    variant: ContentVariant
    fact_sheet: FactSheet
    latest_fact_sheet_version: int
    quality_check: ContentQualityCheck | None
    existing_decision: ReviewDecisionRecord | None
    brief: EditorialBrief | None


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalized_reason(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class ReviewService:
    """Review ContentVariants without editing content or producing publication work."""

    def __init__(
        self,
        session_factory: SessionFactory,
        publishing_policy: PublishingPolicyConfig,
    ) -> None:
        self.session_factory = session_factory
        self.publishing_policy = publishing_policy
        self._validate_policy()

    def decide(
        self,
        *,
        artifact_type: str,
        artifact_id: UUID,
        request: ReviewActionRequest,
        decision: ReviewState,
        principal: ReviewerPrincipal,
        idempotency_key: str,
        request_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> ReviewActionResult:
        artifact = self._artifact_type(artifact_type)
        if decision not in TERMINAL_DECISIONS:
            raise ReviewConflictError("review action is not a terminal human decision")
        reason = _normalized_reason(request.reason)
        if decision in {ReviewState.REJECTED, ReviewState.CHANGES_REQUESTED} and reason is None:
            raise ReviewValidationError("this review decision requires a non-empty reason")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ReviewValidationError("a bounded Idempotency-Key is required")

        try:
            with self.session_factory() as session, session.begin():
                prior = session.scalar(
                    select(ReviewDecisionRecord)
                    .where(ReviewDecisionRecord.idempotency_key == idempotency_key)
                    .with_for_update()
                )
                if prior is not None:
                    return self._idempotent_result(
                        prior,
                        artifact,
                        artifact_id,
                        request.artifact_version,
                        decision,
                        principal.reviewer_id,
                        reason,
                    )

                graph = self._load_graph(
                    session,
                    artifact_id,
                    lock=True,
                    expected_version=request.artifact_version,
                )
                if graph.existing_decision is not None:
                    if graph.existing_decision.idempotency_key == idempotency_key:
                        return self._idempotent_result(
                            graph.existing_decision,
                            artifact,
                            artifact_id,
                            request.artifact_version,
                            decision,
                            principal.reviewer_id,
                            reason,
                        )
                    raise ReviewConflictError(
                        "this exact artifact version already has a terminal decision"
                    )
                self._require_reviewable(graph)
                snapshot = self._artifact_snapshot(graph, lock_media=True)
                artifact_hash = self._artifact_hash(snapshot)
                record = ReviewDecisionRecord(
                    artifact_type=artifact.value,
                    artifact_id=graph.variant.id,
                    artifact_version=graph.variant.version,
                    decision=decision,
                    reviewer_id=principal.reviewer_id,
                    reason=reason,
                    content_draft_id=graph.draft.id,
                    fact_sheet_id=graph.fact_sheet.id,
                    fact_sheet_version=graph.fact_sheet.version,
                    quality_check_id=graph.quality_check.id,
                    artifact_hash=artifact_hash,
                    artifact_snapshot=snapshot,
                    idempotency_key=idempotency_key,
                )
                session.add(record)
                session.flush()
                graph.variant.review_state = decision
                self._update_draft_state(session, graph.draft)
                session.add(
                    AuditLog(
                        actor_id=principal.reviewer_id,
                        action=decision.value,
                        artifact_type=artifact.value,
                        artifact_id=graph.variant.id,
                        artifact_version=graph.variant.version,
                        review_decision_id=record.id,
                        result="SUCCESS",
                        reason=reason,
                        request_id=request_id,
                        correlation_id=correlation_id,
                        audit_metadata={
                            "content_draft_id": str(graph.draft.id),
                            "fact_sheet_id": str(graph.fact_sheet.id),
                            "fact_sheet_version": graph.fact_sheet.version,
                            "quality_check_id": str(graph.quality_check.id),
                            "artifact_hash": artifact_hash,
                        },
                    )
                )
                session.flush()
                return self._result(record, decision)
        except IntegrityError as exc:
            raise ReviewConflictError("a terminal decision already exists") from exc

    def queue(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        risk_level: RiskLevel | None = None,
        review_state: ReviewState | None = None,
        platform: str | None = None,
        format: str | None = None,
        language: str | None = None,
        sensitive_topic: str | None = None,
    ) -> ReviewQueuePage:
        if offset < 0 or limit < 1 or limit > 100:
            raise ReviewPreconditionError("review queue pagination is outside allowed bounds")
        if review_state is not None and review_state not in _REVIEWABLE:
            return ReviewQueuePage(items=(), offset=offset, limit=limit, total=0)
        risk_rank = case(
            (ContentDraft.risk_level == RiskLevel.CRITICAL, 0),
            (ContentDraft.risk_level == RiskLevel.HIGH, 1),
            (ContentDraft.risk_level == RiskLevel.MEDIUM, 2),
            else_=3,
        )
        latest_fact_sheet_version = (
            select(func.max(FactSheet.version))
            .where(FactSheet.story_id == ContentDraft.story_id)
            .correlate(ContentDraft)
            .scalar_subquery()
        )
        terminal_decision_exists = exists().where(
            ReviewDecisionRecord.artifact_type == ArtifactType.CONTENT_VARIANT.value,
            ReviewDecisionRecord.artifact_id == ContentVariant.id,
            ReviewDecisionRecord.artifact_version == ContentVariant.version,
        )
        current_quality_exists = exists().where(
            ContentQualityCheck.content_draft_id == ContentDraft.id,
            ContentQualityCheck.content_variant_id == ContentVariant.id,
            ContentQualityCheck.content_variant_version == ContentVariant.version,
            ContentQualityCheck.fact_sheet_id == FactSheet.id,
            ContentQualityCheck.fact_sheet_version == FactSheet.version,
            ContentQualityCheck.methodology_version == QUALITY_METHODOLOGY_VERSION,
            ContentQualityCheck.content_artifact_hash.is_not(None),
            ContentQualityCheck.passed.is_(True),
            ContentQualityCheck.review_required.is_(True),
        )
        durable_filters = [
            ContentVariant.review_state.in_(tuple(_REVIEWABLE)),
            ContentDraft.fact_sheet_version == FactSheet.version,
            FactSheet.version == latest_fact_sheet_version,
            ContentDraft.risk_level == FactSheet.risk_level,
            ContentDraft.sensitive_topics == FactSheet.sensitive_topics,
            ContentDraft.review_required.is_(True),
            ~terminal_decision_exists,
            current_quality_exists,
        ]
        statement = (
            select(ContentVariant.id)
            .join(ContentDraft, ContentDraft.id == ContentVariant.content_draft_id)
            .join(FactSheet, FactSheet.id == ContentDraft.fact_sheet_id)
            .where(and_(*durable_filters))
            .order_by(risk_rank, ContentVariant.updated_at, ContentVariant.id)
        )
        if risk_level is not None:
            statement = statement.where(ContentDraft.risk_level == risk_level)
        if review_state is not None:
            statement = statement.where(ContentVariant.review_state == review_state)
        if platform is not None:
            statement = statement.where(ContentVariant.platform == platform)
        if format is not None:
            statement = statement.where(ContentVariant.format == format)
        if language is not None:
            statement = statement.where(ContentVariant.language == language)
        if sensitive_topic is not None:
            statement = statement.where(
                cast(FactSheet.sensitive_topics, String).contains(f'"{sensitive_topic}"')
            )

        items: list[ReviewQueueItem] = []
        eligible_seen = 0
        sql_offset = 0
        batch_size = min(_QUEUE_BATCH_MAX, max(_QUEUE_BATCH_MIN, limit * 2))
        with self.session_factory() as session:
            total = (
                session.scalar(
                    select(func.count()).select_from(statement.order_by(None).subquery())
                )
                or 0
            )
            while len(items) < limit:
                variant_ids = tuple(session.scalars(statement.offset(sql_offset).limit(batch_size)))
                if not variant_ids:
                    break
                sql_offset += len(variant_ids)
                for variant_id in variant_ids:
                    graph = self._load_graph(session, variant_id)
                    if (
                        self._precondition_error(graph) is not None
                        or graph.existing_decision is not None
                    ):
                        continue
                    if (
                        sensitive_topic is not None
                        and sensitive_topic not in graph.fact_sheet.sensitive_topics
                    ):
                        continue
                    if eligible_seen < offset:
                        eligible_seen += 1
                        continue
                    items.append(self._queue_item(graph))
                    if len(items) == limit:
                        break
                if len(variant_ids) < batch_size:
                    break
        return ReviewQueuePage(items=tuple(items), offset=offset, limit=limit, total=total)

    def detail(self, *, artifact_type: str, artifact_id: UUID) -> ReviewDetail:
        self._artifact_type(artifact_type)
        with self.session_factory() as session:
            graph = self._load_graph(session, artifact_id)
            return self._detail(graph)

    def is_currently_eligible(
        self, session: Session, variant: ContentVariant, *, lock: bool = False
    ) -> bool:
        if variant.review_state is not ReviewState.APPROVED:
            return False
        try:
            graph = self._load_graph(session, variant.id, lock=lock)
        except (ReviewNotFoundError, ReviewPreconditionError):
            return False
        decision = graph.existing_decision
        quality = graph.quality_check
        if decision is None or quality is None:
            return False
        if decision.decision is not ReviewState.APPROVED:
            return False
        if decision.artifact_version != variant.version:
            return False
        if decision.content_draft_id != graph.draft.id:
            return False
        if decision.fact_sheet_id != graph.fact_sheet.id:
            return False
        if decision.fact_sheet_version != graph.fact_sheet.version:
            return False
        if graph.latest_fact_sheet_version != graph.fact_sheet.version:
            return False
        if (
            decision.quality_check_id != quality.id
            or not quality.passed
            or not quality.review_required
        ):
            return False
        try:
            if self._precondition_error(graph, allow_approved=True) is not None:
                return False
            current_hash = self._artifact_hash(self._artifact_snapshot(graph, lock_media=lock))
        except (ValidationError, ValueError, TypeError, ReviewPreconditionError):
            return False
        return current_hash == decision.artifact_hash

    def _load_graph(
        self,
        session: Session,
        variant_id: UUID,
        *,
        lock: bool = False,
        expected_version: int | None = None,
    ) -> _ReviewGraph:
        routing = session.execute(
            select(ContentVariant.content_draft_id, ContentDraft.story_id)
            .join(ContentDraft, ContentDraft.id == ContentVariant.content_draft_id)
            .where(ContentVariant.id == variant_id)
        ).one_or_none()
        if routing is None:
            raise ReviewNotFoundError("review artifact was not found")
        draft_id, story_id = routing
        if lock:
            story = session.scalar(
                select(Story)
                .where(Story.id == story_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            draft = session.scalar(
                select(ContentDraft)
                .where(ContentDraft.id == draft_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            variant = session.scalar(
                select(ContentVariant)
                .where(ContentVariant.id == variant_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        else:
            story = session.get(Story, story_id)
            draft = session.get(ContentDraft, draft_id)
            variant = session.get(ContentVariant, variant_id)
        if story is None or draft is None or variant is None:
            raise ReviewPreconditionError("review artifact graph is incomplete")
        if draft.story_id != story.id or variant.content_draft_id != draft.id:
            raise ReviewPreconditionError("review artifact routing changed during locking")
        if expected_version is not None and variant.version != expected_version:
            raise ReviewConflictError("requested artifact version is stale")
        if lock and variant.media_asset_ids:
            try:
                load_media_provenance(session, variant, lock=True)
            except MediaValidationError:
                raise ReviewPreconditionError("reviewed media provenance is invalid") from None
        fact_sheet = session.get(FactSheet, draft.fact_sheet_id)
        if fact_sheet is None:
            raise ReviewPreconditionError("content Fact Sheet is missing")
        latest = session.scalar(
            select(func.max(FactSheet.version)).where(FactSheet.story_id == story.id)
        )
        if latest is None:
            raise ReviewPreconditionError("story has no Fact Sheet")
        quality = session.scalar(
            select(ContentQualityCheck)
            .where(
                ContentQualityCheck.content_draft_id == draft.id,
                ContentQualityCheck.content_variant_id == variant.id,
                ContentQualityCheck.content_variant_version == variant.version,
                ContentQualityCheck.methodology_version == QUALITY_METHODOLOGY_VERSION,
                ContentQualityCheck.content_artifact_hash.is_not(None),
            )
            .order_by(ContentQualityCheck.created_at.desc(), ContentQualityCheck.id.desc())
            .limit(1)
        )
        decision = session.scalar(
            select(ReviewDecisionRecord).where(
                ReviewDecisionRecord.artifact_type == ArtifactType.CONTENT_VARIANT.value,
                ReviewDecisionRecord.artifact_id == variant.id,
                ReviewDecisionRecord.artifact_version == variant.version,
            )
        )
        try:
            brief = EditorialBrief.model_validate(draft.editorial_brief_snapshot)
        except ValidationError:
            brief = None
        return _ReviewGraph(story, draft, variant, fact_sheet, latest, quality, decision, brief)

    def _precondition_error(
        self, graph: _ReviewGraph, *, allow_approved: bool = False
    ) -> str | None:
        allowed_states = _REVIEWABLE | ({ReviewState.APPROVED} if allow_approved else set())
        if graph.variant.review_state not in allowed_states:
            return "content variant is not at the human-review boundary"
        if graph.fact_sheet.story_id != graph.story.id:
            return "content Fact Sheet belongs to another story"
        if graph.draft.fact_sheet_version != graph.fact_sheet.version:
            return "content draft Fact Sheet version is inconsistent"
        if graph.latest_fact_sheet_version != graph.fact_sheet.version:
            return "content is based on a superseded Fact Sheet"
        if graph.draft.risk_level != graph.fact_sheet.risk_level:
            return "content risk differs from its immutable Fact Sheet"
        if graph.draft.sensitive_topics != graph.fact_sheet.sensitive_topics:
            return "content sensitivity differs from its immutable Fact Sheet"
        try:
            self._fact_sheet_payload(graph.fact_sheet)
        except (ValidationError, ValueError, TypeError):
            return "immutable Fact Sheet snapshot is invalid"
        if not graph.draft.review_required:
            return "content draft does not preserve the MVP human-review requirement"
        if graph.brief is None:
            return "editorial brief snapshot is invalid"
        if (
            graph.brief.story_id != graph.story.id
            or graph.brief.fact_sheet_id != graph.fact_sheet.id
            or graph.brief.fact_sheet_version != graph.fact_sheet.version
            or graph.brief.risk_level != graph.fact_sheet.risk_level
            or list(graph.brief.sensitive_topics) != graph.fact_sheet.sensitive_topics
            or not graph.brief.human_review_required
            or graph.brief.target.platform.value != graph.variant.platform
            or graph.brief.target.format.value != graph.variant.format
        ):
            return "editorial brief does not match the exact review artifact"
        quality = graph.quality_check
        if quality is None:
            return "exact quality assessment is missing"
        if (
            quality.content_draft_id != graph.draft.id
            or quality.content_variant_id != graph.variant.id
            or quality.content_variant_version != graph.variant.version
            or quality.fact_sheet_id != graph.fact_sheet.id
            or quality.fact_sheet_version != graph.fact_sheet.version
            or quality.methodology_version != QUALITY_METHODOLOGY_VERSION
            or not quality.passed
            or not quality.review_required
        ):
            return "exact passing quality assessment is missing or invalid"
        try:
            current_hash = content_artifact_hash(self._quality_artifact(graph.variant))
        except (MediaValidationError, ValueError, TypeError):
            return "reviewed media or content provenance is invalid"
        if quality.content_artifact_hash != current_hash:
            return "content changed after its exact quality assessment"
        return None

    def _require_reviewable(self, graph: _ReviewGraph) -> None:
        if graph.existing_decision is not None:
            raise ReviewConflictError("this exact artifact version already has a terminal decision")
        error = self._precondition_error(graph)
        if error is not None:
            raise ReviewPreconditionError(error)

    def _artifact_snapshot(
        self, graph: _ReviewGraph, *, lock_media: bool = False
    ) -> dict[str, Any]:
        variant = graph.variant
        draft = graph.draft
        snapshot = {
            "content_variant_id": str(variant.id),
            "content_draft_id": str(draft.id),
            "version": variant.version,
            "platform": variant.platform,
            "format": variant.format,
            "language": variant.language,
            "title": variant.title,
            "body": variant.body,
            "caption": variant.caption,
            "structured_payload": variant.structured_payload,
            "claim_ids_used": variant.claim_ids_used,
            "source_ids_used": variant.source_ids_used,
            "media_asset_ids": variant.media_asset_ids,
            "review_state_before_decision": variant.review_state.value,
            "content_draft_version": draft.version,
            "fact_sheet_id": str(graph.fact_sheet.id),
            "fact_sheet_version": graph.fact_sheet.version,
            "risk_level": graph.fact_sheet.risk_level.value,
            "sensitive_topics": graph.fact_sheet.sensitive_topics,
        }
        # Empty-media historical approvals retain their original hash. Once
        # caller-owned media exists, approval binds the reviewed bytes/format,
        # not merely an ID that could later resolve to different material.
        if variant.media_asset_ids:
            session = object_session(variant)
            if session is None:
                raise ReviewPreconditionError("reviewed media reference is detached")
            try:
                snapshot["media_provenance"] = load_media_provenance(
                    session, variant, lock=lock_media
                )
            except MediaValidationError:
                raise ReviewPreconditionError("reviewed media provenance is invalid") from None
        return snapshot

    @staticmethod
    def _quality_artifact(variant: ContentVariant) -> dict[str, Any]:
        session = object_session(variant)
        if session is None:
            raise ReviewPreconditionError("quality artifact is detached")
        return quality_artifact(session, variant)

    @staticmethod
    def _artifact_hash(snapshot: dict[str, Any]) -> str:
        material = dict(snapshot)
        material.pop("review_state_before_decision", None)
        return _canonical_hash(material)

    def _update_draft_state(self, session: Session, draft: ContentDraft) -> None:
        variants = tuple(
            session.scalars(
                select(ContentVariant)
                .where(ContentVariant.content_draft_id == draft.id)
                .order_by(ContentVariant.id)
                .with_for_update()
            )
        )
        states = tuple(item.review_state for item in variants)
        if states and all(state is ReviewState.APPROVED for state in states):
            draft.review_state = ReviewState.APPROVED
        elif ReviewState.CHANGES_REQUESTED in states:
            draft.review_state = ReviewState.CHANGES_REQUESTED
        elif states and all(state is ReviewState.REJECTED for state in states):
            draft.review_state = ReviewState.REJECTED
        elif any(state in TERMINAL_DECISIONS for state in states):
            draft.review_state = ReviewState.IN_REVIEW
        elif states and all(state is ReviewState.READY_FOR_REVIEW for state in states):
            draft.review_state = ReviewState.READY_FOR_REVIEW
        elif ReviewState.IN_REVIEW in states:
            draft.review_state = ReviewState.IN_REVIEW
        else:
            draft.review_state = ReviewState.NOT_READY

    def _detail(self, graph: _ReviewGraph) -> ReviewDetail:
        variant = graph.variant
        draft = graph.draft
        quality = graph.quality_check
        fact_sheet = self._fact_sheet_payload(graph.fact_sheet)
        return ReviewDetail(
            artifact_id=variant.id,
            artifact_version=variant.version,
            current_review_state=variant.review_state,
            current_reviewable=(
                graph.existing_decision is None and self._precondition_error(graph) is None
            ),
            content_variant=self._artifact_snapshot(graph),
            content_draft={
                "content_draft_id": str(draft.id),
                "story_id": str(draft.story_id),
                "version": draft.version,
                "fact_sheet_id": str(draft.fact_sheet_id),
                "fact_sheet_version": draft.fact_sheet_version,
                "review_state": draft.review_state.value,
                "risk_level": draft.risk_level.value,
                "sensitive_topics": draft.sensitive_topics,
            },
            fact_sheet=fact_sheet,
            quality_check=self._quality_payload(quality) if quality else None,
            editorial_brief=draft.editorial_brief_snapshot,
            generation_ai_provenance=self._safe_ai(graph.draft.created_by_ai_run_id),
            quality_ai_provenance=self._safe_ai(quality.ai_run_id) if quality else None,
            existing_decision=(
                self._result(graph.existing_decision, graph.variant.review_state)
                if graph.existing_decision
                else None
            ),
            risk_level=graph.fact_sheet.risk_level,
            sensitive_topics=tuple(graph.fact_sheet.sensitive_topics),
        )

    def _safe_ai(self, run_id: UUID | None) -> SafeAIProvenance | None:
        if run_id is None:
            return None
        with self.session_factory() as session:
            run = session.get(AIRun, run_id)
            if run is None:
                return None
            model = session.get(AIModel, run.ai_model_id)
            if model is None:
                return None
            return SafeAIProvenance(
                ai_run_id=run.id,
                provider=model.provider,
                model=model.model_name,
                task=run.task_type,
                prompt_id=run.prompt_id,
                prompt_version=run.prompt_version,
                prompt_checksum=run.prompt_checksum,
                validation_status=run.validation_status,
            )

    @staticmethod
    def _quality_payload(check: ContentQualityCheck) -> dict[str, Any]:
        return {
            "quality_check_id": str(check.id),
            "content_variant_id": str(check.content_variant_id),
            "content_variant_version": check.content_variant_version,
            "fact_sheet_id": str(check.fact_sheet_id),
            "fact_sheet_version": check.fact_sheet_version,
            "methodology_version": check.methodology_version,
            "semantic_validation_passed": check.semantic_validation_passed,
            "semantic_methodology_version": check.semantic_methodology_version,
            "semantic_findings": check.semantic_findings,
            "certainty_escalations": check.certainty_escalations,
            "claim_semantic_escalations": check.claim_semantic_escalations,
            "value_escalations": check.value_escalations,
            "content_artifact_hash": check.content_artifact_hash,
            "passed": check.passed,
            "review_required": check.review_required,
            "factual_accuracy_passed": check.factual_accuracy_passed,
            "source_alignment_passed": check.source_alignment_passed,
            "citation_alignment_passed": check.citation_alignment_passed,
            "style_passed": check.style_passed,
            "unsupported_claims": check.unsupported_claims,
            "fabricated_quotes": check.fabricated_quotes,
            "incorrect_names": check.incorrect_names,
            "incorrect_dates": check.incorrect_dates,
            "incorrect_numbers": check.incorrect_numbers,
            "missing_context": check.missing_context,
            "defamation_risk": check.defamation_risk,
            "sensitive_topic_error": check.sensitive_topic_error,
            "notes": check.notes,
        }

    @staticmethod
    def _fact_sheet_payload(sheet: FactSheet) -> dict[str, Any]:
        artifact = FactSheetArtifact(
            fact_sheet_id=sheet.id,
            story_id=sheet.story_id,
            version=sheet.version,
            headline=sheet.headline,
            summary=sheet.summary,
            claims=sheet.claims_snapshot,
            fact_checks=sheet.fact_checks_snapshot,
            evidence=sheet.evidence_snapshot,
            sources=sheet.sources_snapshot,
            timeline=sheet.timeline,
            entities=sheet.entities,
            locations=sheet.locations,
            context=sheet.context,
            counterclaims=sheet.counterclaims,
            unresolved_questions=sheet.unresolved_questions,
            confidence_score=sheet.confidence_score,
            risk_level=sheet.risk_level,
            sensitive_topics=sheet.sensitive_topics,
            created_at=sheet.created_at,
        )
        return artifact.model_dump(mode="json")

    @staticmethod
    def _queue_item(graph: _ReviewGraph) -> ReviewQueueItem:
        return ReviewQueueItem(
            artifact_id=graph.variant.id,
            artifact_version=graph.variant.version,
            content_draft_id=graph.draft.id,
            story_id=graph.story.id,
            fact_sheet_id=graph.fact_sheet.id,
            fact_sheet_version=graph.fact_sheet.version,
            title=graph.variant.title,
            platform=graph.variant.platform,
            format=graph.variant.format,
            language=graph.variant.language,
            risk_level=graph.fact_sheet.risk_level,
            sensitive_topics=tuple(graph.fact_sheet.sensitive_topics),
            review_state=graph.variant.review_state,
            review_ready_at=graph.variant.updated_at,
        )

    @staticmethod
    def _result(record: ReviewDecisionRecord, state: ReviewState) -> ReviewActionResult:
        return ReviewActionResult(
            review_decision_id=record.id,
            artifact_type=record.artifact_type,
            artifact_id=record.artifact_id,
            artifact_version=record.artifact_version,
            decision=record.decision,
            reviewer_id=record.reviewer_id,
            reason=record.reason,
            decided_at=record.decided_at,
            current_review_state=state,
        )

    def _idempotent_result(
        self,
        record: ReviewDecisionRecord,
        artifact: ArtifactType,
        artifact_id: UUID,
        artifact_version: int,
        decision: ReviewState,
        reviewer_id: UUID,
        reason: str | None,
    ) -> ReviewActionResult:
        if (
            record.artifact_type != artifact.value
            or record.artifact_id != artifact_id
            or record.artifact_version != artifact_version
            or record.decision is not decision
            or record.reviewer_id != reviewer_id
            or record.reason != reason
        ):
            raise ReviewConflictError("Idempotency-Key was reused for a different operation")
        return self._result(record, record.decision)

    @staticmethod
    def _artifact_type(value: str) -> ArtifactType:
        try:
            return ArtifactType(value)
        except ValueError as exc:
            raise UnsupportedArtifactError("artifact type is not supported by Stage 23") from exc

    def _validate_policy(self) -> None:
        if (
            not self.publishing_policy.mvp.external_publication_requires_human_approval
            or self.publishing_policy.mvp.low_risk_auto_approval_enabled
            or self.publishing_policy.future.mandatory_review_categories_may_bypass_human_review
        ):
            raise ValueError("publishing policy weakens canonical MVP human approval")
