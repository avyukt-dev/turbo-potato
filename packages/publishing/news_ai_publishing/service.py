"""Transactional scheduling-only operations, exact approval and prerequisite checks."""

import hashlib
import ipaddress
import json
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from news_ai_database import (
    AuditLog,
    ContentVariant,
    MediaAsset,
    Publication,
    SocialAccount,
    SocialAccountStatus,
)
from news_ai_domain import PublicationStatus
from news_ai_review import ApprovalEligibilityService, ReviewCapability, ReviewerPrincipal
from news_ai_social.config import load_instagram_config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .contracts import CreatePublicationRequest, PublicationView, system_clock, utc
from .errors import PublicationError

PRE_EXECUTION = {PublicationStatus.DRAFT, PublicationStatus.APPROVED, PublicationStatus.SCHEDULED}


class PublicationService:
    def __init__(
        self,
        session_factory,
        eligibility: ApprovalEligibilityService,
        *,
        clock: Callable[[], datetime] = system_clock,
        platform_config=None,
    ):
        self.session_factory = session_factory
        self.eligibility = eligibility
        self.clock = clock
        self.platform_config = platform_config or load_instagram_config("config")

    @staticmethod
    def authorize(actor: ReviewerPrincipal) -> None:
        if not actor.has_any(ReviewCapability.PUBLISH):
            raise PublicationError("FORBIDDEN", "Publishing capability required")

    def create(
        self,
        request: CreatePublicationRequest,
        actor: ReviewerPrincipal,
        *,
        correlation_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> PublicationView:
        self.authorize(actor)
        material = request.model_dump(mode="json")
        request_hash = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        try:
            with self.session_factory() as session, session.begin():
                prior = session.scalar(
                    select(Publication)
                    .where(
                        Publication.created_by_actor_id == actor.reviewer_id,
                        Publication.idempotency_key == request.idempotency_key,
                    )
                    .with_for_update()
                )
                if prior is not None:
                    current = session.get(ContentVariant, prior.content_variant_id)
                    if (
                        prior.request_hash != request_hash
                        or current is None
                        or current.version != prior.content_variant_version
                    ):
                        raise PublicationError("IDEMPOTENCY_CONFLICT")
                    return PublicationView.model_validate(prior)
                now = utc(self.clock())
                if request.scheduled_at is not None and request.scheduled_at < now:
                    raise PublicationError("INVALID_SCHEDULE")
                ref = self.eligibility.load_approved_reference(session, request.content_variant_id)
                if ref is None:
                    raise PublicationError("CONTENT_NOT_APPROVED")
                self.account(session, request.social_account_id, ref.variant, request.platform)
                blocked = self.media_reason(session, ref.variant)
                row = Publication(
                    content_variant_id=ref.variant.id,
                    content_variant_version=ref.variant.version,
                    content_draft_id=ref.content_draft_id,
                    fact_sheet_id=ref.fact_sheet_id,
                    fact_sheet_version=ref.fact_sheet_version,
                    review_decision_id=ref.review_decision_id,
                    quality_check_id=ref.quality_check_id,
                    social_account_id=request.social_account_id,
                    platform=ref.variant.platform,
                    status=PublicationStatus.BLOCKED
                    if blocked
                    else PublicationStatus.SCHEDULED
                    if request.scheduled_at is not None
                    else PublicationStatus.APPROVED,
                    scheduled_at=request.scheduled_at,
                    created_by_actor_id=actor.reviewer_id,
                    idempotency_key=request.idempotency_key,
                    request_hash=request_hash,
                    correlation_id=correlation_id,
                    blocking_reason=blocked,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
                self.audit(
                    session,
                    row,
                    "PUBLICATION_CREATED",
                    actor.reviewer_id,
                    now,
                    request_id=request_id,
                )
                if row.status == PublicationStatus.SCHEDULED:
                    self.audit(
                        session,
                        row,
                        "PUBLICATION_SCHEDULE_REGISTERED",
                        actor.reviewer_id,
                        now,
                        request_id=request_id,
                    )
                return PublicationView.model_validate(row)
        except IntegrityError as exc:
            # Uniqueness is the concurrency arbiter. A same-key race is a safe replay.
            with self.session_factory() as session:
                prior = session.scalar(
                    select(Publication).where(
                        Publication.created_by_actor_id == actor.reviewer_id,
                        Publication.idempotency_key == request.idempotency_key,
                    )
                )
                if prior is not None and prior.request_hash == request_hash:
                    return PublicationView.model_validate(prior)
            raise PublicationError("PUBLICATION_CONFLICT") from exc

    def get(self, publication_id: UUID) -> PublicationView:
        with self.session_factory() as session:
            return PublicationView.model_validate(self.load(session, publication_id))

    def cancel(
        self,
        publication_id: UUID,
        actor: ReviewerPrincipal,
        *,
        reason: str | None = None,
        request_id: UUID | None = None,
    ) -> PublicationView:
        self.authorize(actor)
        with self.session_factory() as session, session.begin():
            row = self.load(session, publication_id, lock=True)
            if row.status == PublicationStatus.CANCELLED:
                return PublicationView.model_validate(row)
            if row.status not in PRE_EXECUTION | {PublicationStatus.BLOCKED}:
                raise PublicationError("INVALID_STATE")
            now = utc(self.clock())
            old = row.status
            row.status = PublicationStatus.CANCELLED
            row.cancelled_at = now
            row.cancellation_reason = reason
            row.updated_at = now
            self.audit(
                session,
                row,
                "PUBLICATION_CANCELLED",
                actor.reviewer_id,
                now,
                old=old,
                request_id=request_id,
            )
            return PublicationView.model_validate(row)

    def publish_now(
        self, publication_id: UUID, actor: ReviewerPrincipal, *, request_id: UUID | None = None
    ) -> PublicationView:
        self.authorize(actor)
        with self.session_factory() as session, session.begin():
            row = self.load(session, publication_id, lock=True)
            if row.status not in PRE_EXECUTION or row.scheduled_event_id is not None:
                raise PublicationError("INVALID_STATE")
            self.revalidate(session, row)
            now = utc(self.clock())
            old = row.status
            row.scheduled_at = now
            row.status = PublicationStatus.SCHEDULED
            row.updated_at = now
            self.audit(
                session,
                row,
                "PUBLICATION_PUBLISH_NOW",
                actor.reviewer_id,
                now,
                old=old,
                request_id=request_id,
            )
            return PublicationView.model_validate(row)

    def revalidate(self, session: Session, row: Publication) -> None:
        ref = self.eligibility.load_approved_reference(session, row.content_variant_id)
        if ref is None or (
            ref.variant.version,
            ref.content_draft_id,
            ref.fact_sheet_id,
            ref.fact_sheet_version,
            ref.review_decision_id,
            ref.quality_check_id,
        ) != (
            row.content_variant_version,
            row.content_draft_id,
            row.fact_sheet_id,
            row.fact_sheet_version,
            row.review_decision_id,
            row.quality_check_id,
        ):
            raise PublicationError("APPROVAL_STALE")
        self.account(session, row.social_account_id, ref.variant, row.platform)
        reason = self.media_reason(session, ref.variant)
        if reason:
            raise PublicationError(reason)

    @staticmethod
    def load(session: Session, publication_id: UUID, *, lock: bool = False) -> Publication:
        stmt = select(Publication).where(Publication.id == publication_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        row = session.scalar(stmt)
        if row is None:
            raise PublicationError("PUBLICATION_NOT_FOUND")
        return row

    def account(self, session, account_id, variant, platform):
        account = session.scalar(
            select(SocialAccount)
            .where(SocialAccount.id == account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            account is None
            or account.status != SocialAccountStatus.ACTIVE
            or not self.platform_config.enabled
        ):
            raise PublicationError("ACCOUNT_INACTIVE")
        if (
            variant.platform != "INSTAGRAM"
            or variant.format != "CAROUSEL"
            or account.platform != variant.platform
            or (platform is not None and platform != account.platform)
        ):
            raise PublicationError("PLATFORM_MISMATCH")
        if (
            not isinstance(account.capabilities, dict)
            or account.capabilities.get("carousel") is not True
            or account.capabilities.get("image") is not True
        ):
            raise PublicationError("PLATFORM_MISMATCH")
        return account

    def media_reason(self, session, variant):
        ids = variant.media_asset_ids
        if not isinstance(variant.structured_payload, dict) or not isinstance(ids, list):
            return "MEDIA_UNAVAILABLE"
        slides = variant.structured_payload.get("slides", [])
        if not isinstance(slides, list):
            return "MEDIA_UNAVAILABLE"
        constraints = self.platform_config.constraints
        if not constraints.min_carousel_items <= len(slides) <= constraints.max_carousel_items:
            return "MEDIA_UNAVAILABLE"
        if len(variant.caption or "") > constraints.caption_max_characters:
            return "PLATFORM_CONSTRAINT"
        if not ids or len(ids) != len(slides) or len(set(ids)) != len(ids):
            return "MEDIA_UNAVAILABLE"
        for identifier in ids:
            try:
                asset = session.get(MediaAsset, UUID(str(identifier)))
            except (ValueError, TypeError):
                return "MEDIA_UNAVAILABLE"
            if (
                asset is None
                or asset.asset_type != "IMAGE"
                or asset.mime_type != "image/jpeg"
                or not isinstance(asset.source_metadata, dict)
                or asset.source_metadata.get("media_format") != "JPEG"
                or asset.visual_check_status != "VALIDATED"
                or not asset.public_url
            ):
                return "MEDIA_UNAVAILABLE"
            try:
                parsed = urlsplit(asset.public_url)
                _port = parsed.port
            except ValueError:
                return "MEDIA_UNAVAILABLE"
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                return "MEDIA_UNAVAILABLE"
            hostname = parsed.hostname.casefold()
            if (
                hostname == "localhost"
                or "." not in hostname
                or hostname.endswith((".local", ".internal", ".localhost"))
            ):
                return "MEDIA_UNAVAILABLE"
            try:
                if not ipaddress.ip_address(hostname).is_global:
                    return "MEDIA_UNAVAILABLE"
            except ValueError:
                pass
        return None

    @staticmethod
    def audit(session, row, action, actor, now, *, old=None, request_id=None):
        session.add(
            AuditLog(
                actor_id=actor,
                action=action,
                artifact_type="publication",
                artifact_id=row.id,
                artifact_version=1,
                review_decision_id=row.review_decision_id,
                result="BLOCKED" if row.status == PublicationStatus.BLOCKED else "SUCCESS",
                correlation_id=row.correlation_id,
                request_id=request_id,
                reason=row.cancellation_reason
                if action == "PUBLICATION_CANCELLED"
                else row.blocking_reason,
                created_at=now,
                audit_metadata={
                    "actor_kind": "SYSTEM" if actor is None else "HUMAN",
                    "content_variant_id": str(row.content_variant_id),
                    "content_variant_version": row.content_variant_version,
                    "social_account_id": str(row.social_account_id),
                    "fact_sheet_id": str(row.fact_sheet_id),
                    "fact_sheet_version": row.fact_sheet_version,
                    "old_status": old.value if old else None,
                    "new_status": row.status.value,
                    "scheduled_at": row.scheduled_at.isoformat() if row.scheduled_at else None,
                    "blocking_reason": row.blocking_reason,
                },
            )
        )
