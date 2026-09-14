"""Short transactional, first-writer-wins association of existing caller media."""

from collections.abc import Callable
from uuid import UUID

from news_ai_database import (
    AIRun,
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    FactSheet,
    ReviewDecisionRecord,
)
from news_ai_domain import ReviewState
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import ContentGenerationOutput
from .media import MediaValidationError, load_media_provenance


class MediaAttachmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content_variant_id: UUID
    expected_content_variant_version: int = Field(ge=1)
    ordered_media_asset_ids: tuple[UUID, ...] = Field(min_length=1, max_length=10)

    @field_validator("ordered_media_asset_ids")
    @classmethod
    def unique_ids(cls, ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(ids) != len(set(ids)):
            raise ValueError("media references must be unique")
        return ids


class MediaAttachmentConflict(ValueError):
    def __init__(self) -> None:
        super().__init__("content media attachment conflicts with durable artifact state")


class ContentMediaAttachmentService:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def attach_media(self, request: MediaAttachmentRequest) -> tuple[UUID, ...]:
        with self.session_factory() as session, session.begin():
            draft_id = session.scalar(
                select(ContentVariant.content_draft_id).where(
                    ContentVariant.id == request.content_variant_id
                )
            )
            draft = session.scalar(
                select(ContentDraft).where(ContentDraft.id == draft_id).with_for_update()
            )
            variant = session.scalar(
                select(ContentVariant)
                .where(ContentVariant.id == request.content_variant_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                draft is None
                or variant is None
                or variant.content_draft_id != draft.id
                or draft.created_by_ai_run_id is None
                or variant.version != request.expected_content_variant_version
                or variant.review_state != ReviewState.NOT_READY
                or draft.review_state != ReviewState.NOT_READY
            ):
                raise MediaAttachmentConflict()
            if (
                session.scalar(
                    select(ReviewDecisionRecord.id)
                    .where(
                        ReviewDecisionRecord.artifact_id == variant.id,
                        ReviewDecisionRecord.artifact_version == variant.version,
                    )
                    .limit(1)
                )
                is not None
            ):
                raise MediaAttachmentConflict()
            if (
                session.scalar(
                    select(ContentQualityCheck.id)
                    .where(
                        ContentQualityCheck.content_variant_id == variant.id,
                        ContentQualityCheck.content_variant_version == variant.version,
                        ContentQualityCheck.passed.is_(True),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise MediaAttachmentConflict()
            requested = [str(item) for item in request.ordered_media_asset_ids]
            if not isinstance(variant.media_asset_ids, list):
                raise MediaValidationError()
            run = session.get(AIRun, draft.created_by_ai_run_id)
            if (
                run is None
                or run.task_type != "CONTENT_GENERATION"
                or run.status != "SUCCEEDED"
                or run.validation_status != "VALIDATED"
            ):
                raise MediaAttachmentConflict()
            sheet = session.get(FactSheet, draft.fact_sheet_id)
            if (
                sheet is None
                or sheet.story_id != draft.story_id
                or sheet.version != draft.fact_sheet_version
            ):
                raise MediaAttachmentConflict()
            try:
                payload = variant.structured_payload
                if not isinstance(payload, dict) or set(payload) != {
                    "slides",
                    "hashtags",
                    "claim_ids_used",
                    "claim_presentations",
                }:
                    raise MediaValidationError()
                ContentGenerationOutput.model_validate(
                    {
                        "story_id": draft.story_id,
                        "fact_sheet_id": draft.fact_sheet_id,
                        "fact_sheet_version": draft.fact_sheet_version,
                        "platform": variant.platform,
                        "format": variant.format,
                        "language": variant.language,
                        "title": variant.title,
                        "slides": payload["slides"],
                        "caption": variant.caption,
                        "hashtags": payload["hashtags"],
                        "claim_ids_used": payload["claim_ids_used"],
                        "claim_presentations": payload["claim_presentations"],
                    }
                )
            except (ValidationError, KeyError, TypeError):
                raise MediaValidationError() from None
            if variant.media_asset_ids and variant.media_asset_ids != requested:
                raise MediaAttachmentConflict()
            # Validate a prospective reference list without mutating a caller-owned asset.
            candidate = ContentVariant(
                platform=variant.platform,
                format=variant.format,
                structured_payload=variant.structured_payload,
                media_asset_ids=requested,
            )
            load_media_provenance(session, candidate, lock=True)
            if not variant.media_asset_ids:
                variant.media_asset_ids = requested
            return request.ordered_media_asset_ids
