"""Short-transaction, fenced publication execution and conservative recovery.

The durable publish-intent checkpoint is irreversible: after it is committed,
no recovery path repeats media_publish. Provider I/O never holds row locks.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from news_ai_database import (
    ContentVariant,
    EventOutbox,
    Publication,
    PublicationAttempt,
    SocialAccount,
)
from news_ai_database import (
    PublicationAttemptPhase as Phase,
)
from news_ai_database import (
    PublicationAttemptStatus as AttemptStatus,
)
from news_ai_domain import PublicationStatus as Status
from news_ai_events import EventEnvelope, EventType, ProcessingOutcome
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.outbox import build_outbox_record, envelope_from_outbox
from news_ai_events.reliability import DeferredWorkError, PermanentEventError, TransientEventError
from news_ai_social import (
    InstagramCarouselRequest,
    InstagramContainerStatus,
    InstagramExecutionAdapter,
    InstagramPreparedPublication,
    PublicationVerificationStatus,
    SocialAdapterError,
    SocialErrorClass,
    instagram_request_hash,
)
from sqlalchemy import select

from .contracts import utc
from .errors import PublicationError
from .instagram_request import build_instagram_publication_request
from .service import PublicationService

GROUP = "publisher"
PRE_INTENT = {Phase.PREPARING, Phase.PREPARED}


def as_utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else utc(value)


@dataclass(frozen=True)
class ExecutionClaim:
    publication_id: UUID
    attempt_id: UUID
    token: UUID
    phase: Phase
    request: InstagramCarouselRequest | None
    prepared: InstagramPreparedPublication | None
    external_post_id: str | None


class PublicationExecutionService:
    def __init__(
        self,
        service: PublicationService,
        adapter: InstagramExecutionAdapter,
        config,
        *,
        paused: Callable[[], bool] = lambda: True,
        jitter: Callable[[], float] = random.random,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        live_account_id: str | None = None,
    ):
        self.service = service
        self.factory = service.session_factory
        self.adapter = adapter
        self.config = config
        self.paused = paused
        self.jitter = jitter
        self.sleep = sleeper
        self.live_account_id = live_account_id

    def _now(self):
        return utc(self.service.clock())

    def _validate_event(self, event, row):
        payload = event.payload
        if (
            event.event_type != EventType.PUBLICATION_SCHEDULED
            or event.schema_version != 1
            or event.aggregate_type != "publication"
            or event.aggregate_id != row.id
            or UUID(payload["publication_id"]) != row.id
            or UUID(payload["content_variant_id"]) != row.content_variant_id
            or UUID(payload["social_account_id"]) != row.social_account_id
            or row.scheduled_event_id != event.event_id
            or row.scheduled_at is None
            or as_utc(row.scheduled_at) != as_utc(datetime.fromisoformat(payload["scheduled_at"]))
        ):
            raise PermanentEventError("scheduled event does not match durable publication intent")

    def _request(self, session, row):
        if self.paused():
            raise DeferredWorkError("publishing is temporarily paused")
        self.service.revalidate(session, row)
        account = session.get(SocialAccount, row.social_account_id)
        if self.live_account_id is not None and account.account_identifier != self.live_account_id:
            raise PublicationError("ACCOUNT_DESTINATION_MISMATCH")
        variant = session.get(ContentVariant, row.content_variant_id)
        request = build_instagram_publication_request(
            session, variant, self.service.platform_config
        )
        self.adapter.validate_content(request)
        return request

    def _mark(self, session, event, row):
        if not was_processed(session, event_id=event.event_id, consumer_group=GROUP):
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=GROUP,
                result={"publication_id": str(row.id), "status": row.status.value},
            )

    def _event(self, session, row, attempt, source, *, success=False, retryable=False):
        kind = EventType.PUBLICATION_EXECUTED if success else EventType.PUBLICATION_FAILED
        key = f"{kind.value}:{row.id}:{attempt.id}"
        if session.scalar(select(EventOutbox.id).where(EventOutbox.idempotency_key == key)):
            return
        payload = {
            "publication_id": str(row.id),
            "attempt_id": str(attempt.id),
            "platform": row.platform,
        }
        if success:
            payload.update(
                external_post_id=row.external_post_id,
                external_url=row.external_url,
                published_at=row.published_at.isoformat(),
            )
        else:
            payload.update(error_code=attempt.error_code, retryable=retryable)
        session.add(
            build_outbox_record(
                EventEnvelope(
                    event_type=kind,
                    schema_version=2 if success else 1,
                    occurred_at=self._now(),
                    producer="publisher",
                    producer_version="0.1.0",
                    aggregate_type="publication",
                    aggregate_id=row.id,
                    correlation_id=source.correlation_id,
                    causation_id=source.event_id,
                    idempotency_key=key,
                    payload=payload,
                )
            )
        )

    def _fail(self, session, row, attempt, event, code, *, transient=False, ambiguous=False):
        now = self._now()
        retryable = (
            transient
            and not ambiguous
            and attempt.phase in PRE_INTENT
            and attempt.attempt_number < self.config.max_attempts
        )
        attempt.retryable = retryable
        attempt.ambiguous = ambiguous
        attempt.error_code = code
        attempt.error_class = (
            "AMBIGUOUS" if ambiguous else "TRANSIENT" if transient else "PERMANENT"
        )
        # Never retain an exception string or arbitrary provider response.
        attempt.error_message = "Publication execution did not complete safely"
        attempt.status = (
            AttemptStatus.RETRYABLE_FAILED
            if retryable
            else AttemptStatus.AMBIGUOUS
            if ambiguous
            else AttemptStatus.BLOCKED
            if not transient
            else AttemptStatus.TERMINAL_FAILED
        )
        attempt.completed_at = now
        attempt.updated_at = now
        row.failure_reason = code
        row.blocking_reason = None if retryable else code
        row.status = (
            Status.RETRYING if retryable else Status.FAILED if transient else Status.BLOCKED
        )
        row.next_retry_at = None
        if retryable:
            delay = self.config.retry_delays_seconds[attempt.attempt_number - 1]
            delay *= 1 + (2 * self.jitter() - 1) * self.config.jitter_ratio
            row.next_retry_at = now + timedelta(seconds=delay)
            attempt.retry_after_at = row.next_retry_at
        row.updated_at = now
        self.service.audit(session, row, "PUBLICATION_FAILED", None, now)
        if retryable:
            self.service.audit(session, row, "PUBLICATION_RETRY_SCHEDULED", None, now)
        self._event(session, row, attempt, event, retryable=retryable)
        self._mark(session, event, row)

    def claim(self, event, *, retry=False):
        with self.factory() as session, session.begin():
            row = self.service.load(session, event.aggregate_id, lock=True)
            self._validate_event(event, row)
            if row.status in {Status.PUBLISHED, Status.CANCELLED, Status.BLOCKED, Status.FAILED}:
                self._mark(session, event, row)
                return None
            if not retry and was_processed(session, event_id=event.event_id, consumer_group=GROUP):
                return None
            now = self._now()
            if row.status == Status.RETRYING:
                if not retry or as_utc(row.next_retry_at) > now:
                    return None
            elif row.status not in {Status.SCHEDULED, Status.PUBLISHING}:
                raise PermanentEventError("publication is not executable")
            if row.scheduled_at and as_utc(row.scheduled_at) > now:
                raise TransientEventError("publication is not due")
            attempt = session.scalar(
                select(PublicationAttempt)
                .where(
                    PublicationAttempt.publication_id == row.id,
                    PublicationAttempt.status == AttemptStatus.IN_PROGRESS,
                )
                .with_for_update()
            )
            if attempt and as_utc(attempt.lease_expires_at) > now:
                raise TransientEventError("publication execution is already leased")
            if attempt is None and row.external_post_id is not None:
                raise PermanentEventError(
                    "known external publication requires provenance reconciliation"
                )
            request = None
            # Known side effects must still be reconciled when approval/policy changed.
            # They cannot be sent again, nor can prerequisites erase provider identity.
            if attempt is None or attempt.phase in PRE_INTENT:
                try:
                    request = self._request(session, row)
                except (PublicationError, SocialAdapterError) as exc:
                    if attempt is None:
                        row.status = Status.BLOCKED
                        row.blocking_reason = (
                            exc.code if isinstance(exc, PublicationError) else "PLATFORM_CONSTRAINT"
                        )
                        row.updated_at = now
                        self.service.audit(session, row, "PUBLICATION_BLOCKED", None, now)
                        self._mark(session, event, row)
                    else:
                        self._fail(session, row, attempt, event, "PREREQUISITES_CHANGED")
                    return None
            token = uuid4()
            if attempt is None:
                row.attempt_count += 1
                attempt = PublicationAttempt(
                    publication_id=row.id,
                    attempt_number=row.attempt_count,
                    status=AttemptStatus.IN_PROGRESS,
                    phase=Phase.PREPARING,
                    execution_request_hash=instagram_request_hash(request),
                    trigger_event_id=event.event_id,
                    lease_token=token,
                    lease_expires_at=now + timedelta(seconds=self.config.lease_seconds),
                    started_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(attempt)
                session.flush()
                row.started_at = row.started_at or now
                self.service.audit(session, row, "PUBLICATION_EXECUTION_STARTED", None, now)
            else:
                if request and instagram_request_hash(request) != attempt.execution_request_hash:
                    self._fail(session, row, attempt, event, "EXECUTION_REQUEST_CHANGED")
                    return None
                attempt.lease_token = token
                attempt.lease_expires_at = now + timedelta(seconds=self.config.lease_seconds)
                self.service.audit(session, row, "PUBLICATION_RECOVERY", None, now)
            row.status = Status.PUBLISHING
            row.next_retry_at = None
            row.updated_at = now
            prepared = None
            if attempt.phase == Phase.PREPARED:
                prepared = InstagramPreparedPublication(
                    container_id=attempt.provider_operation_id,
                    child_container_ids=tuple(attempt.provider_metadata["child_container_ids"]),
                    request_hash=attempt.execution_request_hash,
                    mock=attempt.provider_metadata.get("mock") is True,
                )
            return ExecutionClaim(
                row.id,
                attempt.id,
                token,
                attempt.phase,
                request,
                prepared,
                attempt.external_post_id or row.external_post_id,
            )

    def _locked(self, session, claim):
        row = self.service.load(session, claim.publication_id, lock=True)
        attempt = session.scalar(
            select(PublicationAttempt)
            .where(PublicationAttempt.id == claim.attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            attempt.lease_token != claim.token
            or attempt.status != AttemptStatus.IN_PROGRESS
            or row.status != Status.PUBLISHING
        ):
            raise TransientEventError("execution lease was superseded")
        return row, attempt

    async def execute(self, event, *, retry=False):
        claim = self.claim(event, retry=retry)
        if claim is None:
            return ProcessingOutcome.DUPLICATE
        prepared = claim.prepared
        external_id = claim.external_post_id
        if claim.phase not in PRE_INTENT and external_id is None:
            # Container state can diagnose ambiguity, but cannot recover a media ID
            # or prove media_publish replay safe under the official Meta contract.
            status = InstagramContainerStatus.UNKNOWN
            with self.factory() as session:
                container_id = session.get(
                    PublicationAttempt, claim.attempt_id
                ).provider_operation_id
            if container_id:
                with suppress(SocialAdapterError):
                    status = (await self.adapter.get_container_status(container_id)).status
            with self.factory() as session, session.begin():
                row, attempt = self._locked(session, claim)
                attempt.provider_metadata = {
                    **attempt.provider_metadata,
                    "recovery_container_status": status.value,
                }
                self._fail(
                    session, row, attempt, event, "PUBLISH_OUTCOME_AMBIGUOUS", ambiguous=True
                )
            return ProcessingOutcome.PROCESSED
        if external_id is None:
            try:
                if self.paused():
                    raise DeferredWorkError("publishing is temporarily paused")
                if claim.phase == Phase.PREPARED:
                    await self._recovered_container_ready(prepared)
                if prepared is None:
                    prepared = await self.adapter.prepare_publication(claim.request)
                    with self.factory() as session, session.begin():
                        row, attempt = self._locked(session, claim)
                        if prepared.request_hash != attempt.execution_request_hash:
                            raise PublicationError("EXECUTION_REQUEST_CHANGED")
                        attempt.provider_operation_id = prepared.container_id
                        attempt.provider_metadata = {
                            "child_container_ids": list(prepared.child_container_ids),
                            "mock": prepared.mock,
                        }
                        attempt.phase = Phase.PREPARED
                with self.factory() as session, session.begin():
                    row, attempt = self._locked(session, claim)
                    current = self._request(session, row)
                    if instagram_request_hash(current) != attempt.execution_request_hash:
                        raise PublicationError("EXECUTION_REQUEST_CHANGED")
                    if as_utc(attempt.lease_expires_at) <= self._now():
                        raise TransientEventError("execution lease expired before publication")
                    attempt.phase = Phase.PUBLISH_INTENT_RECORDED
                # No ordinary retry is safe beyond the committed marker above.
                result = await self.adapter.publish_prepared(prepared, verify=False)
                external_id = result.external_post_id
                with self.factory() as session, session.begin():
                    row, attempt = self._locked(session, claim)
                    row.external_post_id = external_id
                    attempt.external_post_id = external_id
                    attempt.phase = Phase.VERIFYING
            except DeferredWorkError:
                with self.factory() as session, session.begin():
                    _, attempt = self._locked(session, claim)
                    attempt.lease_expires_at = self._now()
                raise
            except (SocialAdapterError, PublicationError) as exc:
                with self.factory() as session, session.begin():
                    row, attempt = self._locked(session, claim)
                    code = (
                        exc.code if isinstance(exc, PublicationError) else exc.classification.value
                    )
                    after_intent = attempt.phase not in PRE_INTENT
                    ambiguous = after_intent or (
                        isinstance(exc, SocialAdapterError)
                        and exc.classification == SocialErrorClass.AMBIGUOUS
                    )
                    transient = isinstance(exc, SocialAdapterError) and exc.classification in {
                        SocialErrorClass.TRANSIENT,
                        SocialErrorClass.RATE_LIMIT,
                    }
                    self._fail(
                        session,
                        row,
                        attempt,
                        event,
                        code,
                        transient=transient and not after_intent,
                        ambiguous=ambiguous,
                    )
                    if (
                        attempt.retryable
                        and isinstance(exc, SocialAdapterError)
                        and exc.retry_after_seconds is not None
                    ):
                        minimum = self._now() + timedelta(seconds=exc.retry_after_seconds)
                        row.next_retry_at = max(as_utc(row.next_retry_at), minimum)
                        attempt.retry_after_at = row.next_retry_at
                return ProcessingOutcome.PROCESSED
        await self._verify(claim, event, external_id)
        return ProcessingOutcome.PROCESSED

    async def _recovered_container_ready(self, prepared):
        """Read provider currency outside locks before committing a recovered intent."""
        for index in range(self.config.verification_max_attempts):
            if self.paused():
                raise DeferredWorkError("publishing is temporarily paused")
            result = await self.adapter.get_container_status(prepared.container_id)
            if result.status == InstagramContainerStatus.FINISHED:
                return
            if result.status in {InstagramContainerStatus.ERROR, InstagramContainerStatus.EXPIRED}:
                raise SocialAdapterError(
                    "prepared container requires safe re-preparation",
                    classification=SocialErrorClass.TRANSIENT,
                )
            if result.status == InstagramContainerStatus.PUBLISHED:
                raise SocialAdapterError(
                    "prepared container was unexpectedly published",
                    classification=SocialErrorClass.AMBIGUOUS,
                    outcome_may_be_ambiguous=True,
                )
            if result.status != InstagramContainerStatus.IN_PROGRESS:
                raise DeferredWorkError("prepared container readiness is unknown")
            if index + 1 < self.config.verification_max_attempts:
                await self.sleep(self.config.verification_poll_interval_seconds)
        raise DeferredWorkError("prepared container is still processing")

    async def _verify(self, claim, event, external_id):
        result = None
        start = self._now()
        for index in range(self.config.verification_max_attempts):
            try:
                result = await self.adapter.verify_publication(external_id)
            except SocialAdapterError:
                result = None
            if (
                result
                and result.external_post_id == external_id
                and (result.status == PublicationVerificationStatus.PUBLISHED)
            ):
                with self.factory() as session, session.begin():
                    row, attempt = self._locked(session, claim)
                    now = self._now()
                    row.status = Status.PUBLISHED
                    row.published_at = now
                    row.external_url = str(result.external_url) if result.external_url else None
                    row.failure_reason = row.blocking_reason = None
                    row.updated_at = now
                    attempt.external_url = row.external_url
                    attempt.status = AttemptStatus.SUCCEEDED
                    attempt.phase = Phase.COMPLETE
                    attempt.completed_at = now
                    attempt.retryable = False
                    self.service.audit(session, row, "PUBLICATION_EXECUTED", None, now)
                    self._event(session, row, attempt, event, success=True)
                    self._mark(session, event, row)
                return
            elapsed = (self._now() - start).total_seconds()
            delay = self.config.verification_poll_interval_seconds
            if (
                index + 1 == self.config.verification_max_attempts
                or elapsed + delay >= self.config.verification_timeout_seconds
            ):
                break
            await self.sleep(delay)
        with self.factory() as session, session.begin():
            row, attempt = self._locked(session, claim)
            self._fail(
                session, row, attempt, event, "PUBLICATION_VERIFICATION_UNCERTAIN", ambiguous=True
            )

    async def retry_due(self):
        now = self._now()
        if self.paused():
            return 0
        # Claim each selected row in execute(); active lease + uniqueness fence
        # competing scanners, even after this short selection transaction ends.
        with self.factory() as session, session.begin():
            candidates = tuple(
                session.scalars(
                    select(Publication.scheduled_event_id)
                    .where(
                        Publication.status == Status.RETRYING,
                        Publication.next_retry_at <= now,
                    )
                    .order_by(Publication.next_retry_at, Publication.id)
                    .limit(self.config.retry_batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            events = tuple(
                envelope_from_outbox(
                    session.scalar(select(EventOutbox).where(EventOutbox.event_id == identifier))
                )
                for identifier in candidates
            )
        count = 0
        for event in events:
            try:
                await self.execute(event, retry=True)
                count += 1
            except TransientEventError:
                continue
        return count
