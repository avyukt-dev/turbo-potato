from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from news_ai_database import (
    Article,
    ArticleDiscovery,
    ArticleVersion,
    Base,
    ContentDraft,
    ContentVariant,
    EventOutbox,
    ProcessedEvent,
    Publication,
    PublicationAttempt,
    PublicationAttemptPhase,
    PublicationAttemptStatus,
    Source,
)
from news_ai_database.models import OutboxStatus
from news_ai_domain import PublicationStatus
from news_ai_events import (
    RECONCILIATION_POLICIES,
    RECONCILIATION_POLICY_BY_EVENT,
    EventEnvelope,
    EventReconciliationService,
    EventType,
    ReconciliationDisposition,
    ReconciliationMode,
)
from news_ai_events.consumer_contracts import (
    CONSUMER_CONTRACT_BY_EVENT,
    EVENT_CONSUMER_CONTRACTS,
    NORMALIZER_CONSUMER_GROUP,
)
from news_ai_events.outbox import build_outbox_record
from news_ai_events.streams import stream_for_event
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

NOW = datetime(2026, 9, 13, tzinfo=UTC)
SENTINEL = "SUPER_SECRET_RECONCILIATION_TOKEN_123"


class Publisher:
    def __init__(self, *, fail_after: int | None = None):
        self.events = []
        self.fail_after = fail_after

    async def publish(self, event):
        if self.fail_after is not None and len(self.events) >= self.fail_after:
            raise RuntimeError(f"Bearer {SENTINEL}")
        self.events.append(event)
        return f"{len(self.events)}-0"


class Groups:
    def __init__(self, *, existing=(), fail=False):
        self.existing = set(existing)
        self.calls = []
        self.fail = fail

    async def xgroup_create(self, name, groupname, **kwargs):
        self.calls.append((name, groupname, kwargs))
        if self.fail:
            raise RuntimeError(f"redis password={SENTINEL}")
        if (name, groupname) in self.existing:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.existing.add((name, groupname))


class Control:
    def __init__(self, *, paused=True, available=True, fail=False):
        self.paused = paused
        self.available = available
        self.fail = fail

    def snapshot(self):
        if self.fail:
            raise RuntimeError(f"database password={SENTINEL}")
        return SimpleNamespace(available=self.available, effective_pause=self.paused)


def factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def discovered(factory, *, at=NOW, title="Recovery event"):
    article_id, source_id, feed_id = uuid4(), uuid4(), uuid4()
    event = EventEnvelope(
        event_type=EventType.ARTICLE_DISCOVERED,
        occurred_at=at,
        producer="collector",
        producer_version="0.1.0",
        aggregate_type="article",
        aggregate_id=article_id,
        correlation_id=uuid4(),
        causation_id=uuid4(),
        idempotency_key=f"article.discovered:{article_id}:{title}",
        payload={
            "article_id": str(article_id),
            "source_id": str(source_id),
            "source_feed_id": str(feed_id),
            "canonical_url": f"https://example.org/{article_id}",
            "title": title,
            "published_at": at.isoformat(),
        },
    )
    with factory() as session, session.begin():
        session.add(Source(id=source_id, name="Example", source_type="NEWS"))
        session.add(
            Article(
                id=article_id,
                source_id=source_id,
                canonical_url=event.payload["canonical_url"],
                title=title,
            )
        )
        session.add(
            ArticleDiscovery(
                event_id=event.event_id,
                article_id=article_id,
                raw_hash="a" * 64,
                raw_payload={"title": title},
                retrieved_at=at,
            )
        )
        row = build_outbox_record(event)
        row.status = OutboxStatus.PUBLISHED
        row.published_at = at
        row.attempt_count = 1
        session.add(row)
        session.flush()
        return event, row.id


def service(factory, *, publisher=None, groups=None, control=None):
    publisher = publisher or Publisher()
    groups = groups or Groups()
    return (
        EventReconciliationService(
            factory,
            publisher,
            groups,
            control or Control(),
        ),
        publisher,
        groups,
    )


def run(service, mode=ReconciliationMode.DRY_RUN, **kwargs):
    return asyncio.run(service.reconcile(mode=mode, **kwargs))


def test_policy_registry_is_closed_and_matches_real_worker_constants():
    from news_ai_ai_worker import CLAIM_CONSUMER_GROUP, CONTENT_CONSUMER_GROUP
    from news_ai_processor import NORMALIZER_CONSUMER_GROUP as WORKER_NORMALIZER_GROUP
    from news_ai_research_worker import FACT_SHEET_CONSUMER_GROUP

    assert len(CONSUMER_CONTRACT_BY_EVENT) == len(EVENT_CONSUMER_CONTRACTS)
    assert set(RECONCILIATION_POLICY_BY_EVENT) == set(CONSUMER_CONTRACT_BY_EVENT)
    assert len(RECONCILIATION_POLICIES) == len(RECONCILIATION_POLICY_BY_EVENT)
    assert all(callable(item.current_state_predicate) for item in RECONCILIATION_POLICIES)
    assert all(
        item.stream == stream_for_event(item.event_type) for item in EVENT_CONSUMER_CONTRACTS
    )
    assert WORKER_NORMALIZER_GROUP == NORMALIZER_CONSUMER_GROUP
    assert (
        CONSUMER_CONTRACT_BY_EVENT[EventType.STORY_CREATED].consumer_group == CLAIM_CONSUMER_GROUP
    )
    assert (
        CONSUMER_CONTRACT_BY_EVENT[EventType.STORY_VERIFIED].consumer_group
        == FACT_SHEET_CONSUMER_GROUP
    )
    assert (
        CONSUMER_CONTRACT_BY_EVENT[EventType.CONTENT_REQUESTED].consumer_group
        == CONTENT_CONSUMER_GROUP
    )
    assert EventType.PUBLICATION_EXECUTED not in CONSUMER_CONTRACT_BY_EVENT
    assert EventType.ANALYTICS_REQUESTED not in CONSUMER_CONTRACT_BY_EVENT


def test_incomplete_published_event_requires_replay_and_processed_event_prevents_it():
    db = factory()
    event, _ = discovered(db)
    reconciler, publisher, _ = service(db)
    report = run(reconciler)
    assert report.replay_required == 1 and report.items[0].event_id == event.event_id
    assert publisher.events == []
    with db() as session, session.begin():
        session.add(ProcessedEvent(event_id=event.event_id, consumer_group="normalizer"))
    report = run(reconciler)
    assert report.already_processed == 1 and report.replay_required == 0


def test_domain_complete_discovery_is_not_replayed():
    db = factory()
    event, _ = discovered(db)
    with db() as session, session.begin():
        discovery = session.scalar(
            select(ArticleDiscovery).where(ArticleDiscovery.event_id == event.event_id)
        )
        version = ArticleVersion(
            article_id=event.aggregate_id,
            version_number=1,
            content_hash="b" * 64,
            body="body",
            retrieved_at=NOW,
        )
        session.add(version)
        session.flush()
        discovery.normalized_article_version_id = version.id
    reconciler, publisher, _ = service(db)
    report = run(reconciler, ReconciliationMode.APPLY, reason="verified Redis loss")
    assert report.domain_complete == 1 and report.replayed == 0 and publisher.events == []


def test_unsupported_and_malformed_durable_events_fail_closed_without_payload_leak():
    db = factory()
    unsupported = EventEnvelope(
        event_type=EventType.ANALYTICS_REQUESTED,
        producer="publisher",
        producer_version="1",
        aggregate_type="publication",
        aggregate_id=uuid4(),
        idempotency_key="analytics",
        payload={"publication_id": str(uuid4()), "platform": SENTINEL},
    )
    with db() as session, session.begin():
        row = build_outbox_record(unsupported)
        row.status = OutboxStatus.PUBLISHED
        row.published_at = NOW
        session.add(row)
        session.flush()
        malformed_id = row.id
    event, valid_id = discovered(db, at=NOW + timedelta(seconds=1))
    with db() as session, session.begin():
        session.get(EventOutbox, valid_id).schema_version = 99
    reconciler, publisher, _ = service(db)
    report = run(reconciler)
    assert report.unsupported == 1 and report.invalid == 1
    assert publisher.events == []
    rendered = report.model_dump_json()
    assert SENTINEL not in rendered and "payload" not in rendered
    assert str(malformed_id) in rendered and str(event.event_id) in rendered


def test_apply_requires_pause_and_available_control_before_any_redis_call():
    db = factory()
    discovered(db)
    for control, code in (
        (Control(paused=False), "PUBLISHING_NOT_PAUSED"),
        (Control(available=False), "CONTROL_UNAVAILABLE"),
        (Control(fail=True), "CONTROL_UNAVAILABLE"),
    ):
        reconciler, publisher, groups = service(db, control=control)
        report = run(reconciler, ReconciliationMode.APPLY, reason="verified loss")
        assert report.error_code == code and report.replayed == 0
        assert publisher.events == [] and groups.calls == []
        assert SENTINEL not in report.model_dump_json()


def test_apply_restores_group_and_original_envelope_without_mutating_outbox():
    db = factory()
    event, row_id = discovered(db)
    with db() as session:
        row = session.get(EventOutbox, row_id)
        before = (
            row.status,
            row.attempt_count,
            row.published_at,
            row.next_attempt_at,
            row.last_error,
        )
    reconciler, publisher, groups = service(db)
    report = run(reconciler, ReconciliationMode.APPLY, reason="verified loss")
    assert report.replayed == 1
    assert report.groups_created[0].consumer_group == NORMALIZER_CONSUMER_GROUP
    assert groups.calls == [("news:articles", "normalizer", {"id": "0", "mkstream": True})]
    replay = publisher.events[0]
    assert replay.model_dump(mode="json") == event.model_dump(mode="json")
    with db() as session:
        row = session.get(EventOutbox, row_id)
        after = (
            row.status,
            row.attempt_count,
            row.published_at,
            row.next_attempt_at,
            row.last_error,
        )
    assert after == before


def test_existing_group_is_not_reset_and_repeated_apply_preserves_event_identity():
    db = factory()
    event, _ = discovered(db)
    groups = Groups(existing={("news:articles", "normalizer")})
    reconciler, publisher, groups = service(db, groups=groups)
    first = run(reconciler, ReconciliationMode.APPLY, reason="verified loss")
    second = run(reconciler, ReconciliationMode.APPLY, reason="verified loss")
    assert first.groups_created == second.groups_created == ()
    assert [item.event_id for item in publisher.events] == [event.event_id, event.event_id]
    assert all(call[2] == {"id": "0", "mkstream": True} for call in groups.calls)
    with db() as session, session.begin():
        session.add(ProcessedEvent(event_id=event.event_id, consumer_group="normalizer"))
    third = run(reconciler, ReconciliationMode.APPLY, reason="verified loss")
    assert third.already_processed == 1 and len(publisher.events) == 2


def test_limit_and_published_timestamp_order_are_deterministic():
    db = factory()
    late, _ = discovered(db, at=NOW + timedelta(seconds=2), title="late")
    first, _ = discovered(db, at=NOW, title="first")
    middle, _ = discovered(db, at=NOW + timedelta(seconds=1), title="middle")
    reconciler, _, _ = service(db)
    report = run(reconciler, limit=2)
    assert report.scanned == 2
    assert [item.event_id for item in report.items] == [first.event_id, middle.event_id]
    assert late.event_id not in {item.event_id for item in report.items}
    next_page = run(reconciler, limit=2, after_outbox_id=report.next_after_outbox_id)
    assert [item.event_id for item in next_page.items] == [late.event_id]
    assert next_page.after_outbox_id == report.next_after_outbox_id
    assert run(reconciler, after_outbox_id=uuid4()).error_code == "INVALID_CURSOR"
    assert run(reconciler, limit=0).error_code == "INVALID_LIMIT"
    assert run(reconciler, limit=501).error_code == "INVALID_LIMIT"


def test_group_and_transport_failures_are_normalized_and_rerunnable():
    db = factory()
    discovered(db)
    bad_groups = Groups(fail=True)
    reconciler, publisher, _ = service(db, groups=bad_groups)
    failed = run(reconciler, ReconciliationMode.APPLY, reason="verified loss")
    assert failed.error_code == "GROUP_RESTORE_FAILED" and failed.replayed == 0
    assert SENTINEL not in failed.model_dump_json() and publisher.events == []

    working_groups = Groups()
    failing_publisher = Publisher(fail_after=0)
    reconciler, _, _ = service(db, publisher=failing_publisher, groups=working_groups)
    failed = run(reconciler, ReconciliationMode.APPLY, reason="verified loss")
    assert failed.error_code == "TRANSPORT_REPLAY_FAILED" and failed.replayed == 0
    assert SENTINEL not in failed.model_dump_json()
    reconciler, publisher, _ = service(db, groups=working_groups)
    assert run(reconciler, ReconciliationMode.APPLY, reason="retry").replayed == 1
    assert len(publisher.events) == 1


def test_database_failure_is_normalized():
    def unavailable():
        raise RuntimeError(f"postgresql://user:{SENTINEL}@host/db")

    reconciler = EventReconciliationService(unavailable, Publisher(), Groups(), Control())
    report = run(reconciler)
    assert report.error_code == "DATABASE_UNAVAILABLE"
    assert SENTINEL not in report.model_dump_json()


def test_partial_transport_failure_cursor_retries_entire_page_without_skipping():
    db = factory()
    _, boundary = discovered(db, at=NOW - timedelta(seconds=1), title="prior page")
    events = [
        discovered(db, at=NOW + timedelta(seconds=index), title=f"candidate {index}")[0]
        for index in range(3)
    ]
    publisher = Publisher(fail_after=1)
    reconciler, _, _ = service(db, publisher=publisher)
    failed = run(reconciler, ReconciliationMode.APPLY, reason="recover", after_outbox_id=boundary)
    assert failed.error_code == "TRANSPORT_REPLAY_FAILED"
    assert failed.replayed == 1 and [item.replayed for item in failed.items] == [True, False, False]
    assert failed.next_after_outbox_id == boundary
    assert SENTINEL not in failed.model_dump_json()
    publisher.fail_after = None
    resumed = run(
        reconciler,
        ReconciliationMode.APPLY,
        reason="recover again",
        after_outbox_id=failed.next_after_outbox_id,
    )
    assert resumed.replayed == 3
    assert {item.event_id for item in publisher.events} == {item.event_id for item in events}
    # Completion remains consumer-owned; replay itself never fabricates history.
    with db() as session, session.begin():
        for event in events:
            session.add(ProcessedEvent(event_id=event.event_id, consumer_group="normalizer"))
    converged = run(reconciler, ReconciliationMode.APPLY, reason="verify", after_outbox_id=boundary)
    assert converged.already_processed == 3 and converged.replayed == 0


def test_group_failure_retains_first_page_cursor():
    db = factory()
    for index in range(3):
        discovered(db, at=NOW + timedelta(seconds=index), title=f"group candidate {index}")
    reconciler, publisher, _ = service(db, groups=Groups(fail=True))
    failed = run(reconciler, ReconciliationMode.APPLY, reason="recover")
    assert failed.error_code == "GROUP_RESTORE_FAILED"
    assert failed.next_after_outbox_id is None and publisher.events == []
    assert SENTINEL not in failed.model_dump_json()
    reconciler, _, _ = service(db)
    assert (
        run(
            reconciler,
            ReconciliationMode.APPLY,
            reason="retry",
            after_outbox_id=failed.next_after_outbox_id,
        ).replayed
        == 3
    )


@pytest.mark.parametrize("lost_control", ["resumed", "unavailable", "exception"])
def test_each_xadd_rechecks_control_and_interruption_cursor_converges(lost_control):
    class ChangingControl(Control):
        calls = 0

        def snapshot(self):
            self.calls += 1
            if self.calls == 3:
                if lost_control == "exception":
                    raise RuntimeError(f"postgresql://user:{SENTINEL}@host/database")
                return SimpleNamespace(
                    available=lost_control != "unavailable", effective_pause=False
                )
            return super().snapshot()

    db = factory()
    events = [
        discovered(db, at=NOW + timedelta(seconds=index), title=f"pause candidate {index}")[0]
        for index in range(2)
    ]
    control = ChangingControl()
    reconciler, publisher, _ = service(db, control=control)
    failed = run(reconciler, ReconciliationMode.APPLY, reason="recover")
    assert control.calls == 3
    assert failed.error_code == (
        "PUBLISHING_NOT_PAUSED" if lost_control == "resumed" else "CONTROL_UNAVAILABLE"
    )
    assert [item.event_id for item in publisher.events] == [events[0].event_id]
    assert failed.replayed == 1 and failed.next_after_outbox_id is None
    assert SENTINEL not in failed.model_dump_json()
    resumed = run(
        reconciler,
        ReconciliationMode.APPLY,
        reason="paused again",
        after_outbox_id=failed.next_after_outbox_id,
    )
    assert resumed.replayed == 2
    assert {item.event_id for item in publisher.events} == {item.event_id for item in events}


def test_content_request_unrelated_draft_is_not_completion_but_exact_causation_is():
    from unit.review.test_review_service import seed_reviewable

    db = factory()
    variant_id = seed_reviewable(db)[0]
    with db() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        draft = session.get(ContentDraft, variant.content_draft_id)
        request = EventEnvelope(
            event_type=EventType.CONTENT_REQUESTED,
            producer="fact-sheet-builder",
            producer_version="1",
            aggregate_type="story",
            aggregate_id=draft.story_id,
            idempotency_key="lost-content-request",
            payload={
                "story_id": str(draft.story_id),
                "fact_sheet_id": str(draft.fact_sheet_id),
                "requested_platforms": ["INSTAGRAM"],
                "requested_formats": ["CAROUSEL"],
            },
        )
        row = build_outbox_record(request)
        row.status = OutboxStatus.PUBLISHED
        row.published_at = NOW
        session.add(row)
        generated = EventEnvelope(
            event_type=EventType.CONTENT_GENERATED,
            producer="content-worker",
            producer_version="1",
            aggregate_type="content_draft",
            aggregate_id=draft.id,
            causation_id=request.event_id,
            idempotency_key="generated-exact-request",
            payload={
                "story_id": str(draft.story_id),
                "content_draft_id": str(draft.id),
                "content_variant_ids": [str(variant.id)],
                "ai_run_id": str(draft.created_by_ai_run_id),
            },
        )
    reconciler, publisher, _ = service(db)
    assert run(reconciler).replay_required == 1
    assert run(reconciler, ReconciliationMode.APPLY, reason="recover").replayed == 1
    assert publisher.events[0].event_id == request.event_id
    with db() as session, session.begin():
        session.add(build_outbox_record(generated))
    report = run(reconciler)
    assert report.domain_complete == 1 and report.replay_required == 0


def test_reconciled_content_request_leaves_semantic_identity_to_normal_worker():
    from news_ai_ai_worker import ContentGenerationWorker
    from unit.content.test_content_generation import (
        ContentAI,
        FakeConsumer,
        _message,
        _seed,
        _service,
    )

    db, ai = factory(), ContentAI()
    event, _, _ = _seed(db)
    previous = event.model_copy(update={"event_id": uuid4(), "idempotency_key": "previous"})
    consumer = FakeConsumer([_message(previous)])
    content_service = _service(ai)
    worker = ContentGenerationWorker(consumer, db, content_service)
    assert asyncio.run(worker.run_once()).processed == 1
    with db() as session, session.begin():
        row = build_outbox_record(event)
        row.status = OutboxStatus.PUBLISHED
        row.published_at = NOW
        session.add(row)
    # A changed methodology is a legitimate distinct operation, owned exclusively
    # by the content service, even though the referenced Fact Sheet is identical.
    content_service.style = content_service.style.model_copy(
        update={"methodology_version": "content-generation-methodology-v2"}
    )
    reconciler, publisher, _ = service(db)
    report = run(
        reconciler,
        ReconciliationMode.APPLY,
        reason="recover",
        event_type=EventType.CONTENT_REQUESTED,
    )
    assert report.replayed == 1
    consumer.messages = [_message(publisher.events[0], "2-0")]
    assert asyncio.run(worker.run_once()).processed == 1
    with db() as session:
        assert len(list(session.scalars(select(ContentDraft)))) == 2
    assert ai.calls == 2
    assert run(reconciler, event_type=EventType.CONTENT_REQUESTED).already_processed == 1


def publication_event(db, *, status=PublicationStatus.SCHEDULED):
    publication_id, variant_id, account_id = uuid4(), uuid4(), uuid4()
    event = EventEnvelope(
        event_type=EventType.PUBLICATION_SCHEDULED,
        occurred_at=NOW,
        producer="scheduler",
        producer_version="1",
        aggregate_type="publication",
        aggregate_id=publication_id,
        idempotency_key=f"publication.scheduled:{publication_id}",
        payload={
            "publication_id": str(publication_id),
            "content_variant_id": str(variant_id),
            "social_account_id": str(account_id),
            "scheduled_at": NOW.isoformat(),
        },
    )
    with db() as session, session.begin():
        outbox = build_outbox_record(event)
        outbox.status = OutboxStatus.PUBLISHED
        outbox.published_at = NOW
        session.add(outbox)
        session.add(
            Publication(
                id=publication_id,
                content_variant_id=variant_id,
                content_variant_version=1,
                content_draft_id=uuid4(),
                fact_sheet_id=uuid4(),
                fact_sheet_version=1,
                review_decision_id=uuid4(),
                quality_check_id=uuid4(),
                social_account_id=account_id,
                platform="INSTAGRAM",
                status=status,
                scheduled_at=NOW,
                created_by_actor_id=uuid4(),
                idempotency_key=str(uuid4()),
                request_hash="a" * 64,
                correlation_id=event.correlation_id,
                scheduled_event_id=event.event_id,
                external_post_id="known-id" if status is PublicationStatus.PUBLISHED else None,
                published_at=NOW if status is PublicationStatus.PUBLISHED else None,
            )
        )
    return event


def test_publication_state_fences_safe_ambiguous_and_known_id_reconciliation():
    scheduled_db = factory()
    publication_event(scheduled_db)
    report = run(service(scheduled_db)[0])
    assert report.items[0].disposition is ReconciliationDisposition.REPLAY_REQUIRED

    published_db = factory()
    publication_event(published_db, status=PublicationStatus.PUBLISHED)
    assert run(service(published_db)[0]).domain_complete == 1

    ambiguous_db = factory()
    event = publication_event(ambiguous_db, status=PublicationStatus.PUBLISHING)
    with ambiguous_db() as session, session.begin():
        session.add(
            PublicationAttempt(
                publication_id=event.aggregate_id,
                attempt_number=1,
                status=PublicationAttemptStatus.AMBIGUOUS,
                phase=PublicationAttemptPhase.PUBLISH_INTENT_RECORDED,
                execution_request_hash="b" * 64,
                trigger_event_id=event.event_id,
                ambiguous=True,
                lease_token=uuid4(),
                lease_expires_at=NOW,
                started_at=NOW,
            )
        )
    report = run(service(ambiguous_db)[0], ReconciliationMode.APPLY, reason="loss")
    assert report.manual_review_required == 1 and report.replayed == 0

    known_db = factory()
    event = publication_event(known_db, status=PublicationStatus.PUBLISHING)
    with known_db() as session, session.begin():
        session.add(
            PublicationAttempt(
                publication_id=event.aggregate_id,
                attempt_number=1,
                status=PublicationAttemptStatus.IN_PROGRESS,
                phase=PublicationAttemptPhase.VERIFYING,
                execution_request_hash="c" * 64,
                trigger_event_id=event.event_id,
                external_post_id="known-id",
                lease_token=uuid4(),
                lease_expires_at=NOW,
                started_at=NOW,
            )
        )
    assert run(service(known_db)[0]).replay_required == 1
