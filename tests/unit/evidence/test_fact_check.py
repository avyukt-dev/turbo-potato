from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from news_ai_common.config import ConfigLoader
from news_ai_database import (
    Base,
    Claim,
    ClaimEvidence,
    EventOutbox,
    EvidenceItem,
    FactCheck,
    Job,
    Story,
)
from news_ai_domain import ClaimVerificationStatus, FactCheckLabel, ReviewState, RiskLevel
from news_ai_editorial import EditorialConfigLoader
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import envelope_from_outbox
from news_ai_evidence import EvidenceRelation, FactCheckEngine, FactCheckPolicyLoader
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _engine() -> FactCheckEngine:
    loader = ConfigLoader(Path("config"))
    return FactCheckEngine(
        FactCheckPolicyLoader(loader).load(),
        EditorialConfigLoader(loader).load().risk_policy,
    )


def _seed(
    factory: sessionmaker[Session],
    *,
    risk: RiskLevel = RiskLevel.LOW,
    evidence: tuple[tuple[EvidenceRelation, str, str | None, int | None], ...] = (),
) -> tuple[EventEnvelope, UUID, UUID]:
    research_run_id = uuid4()
    evidence_ids: list[UUID] = []
    with factory() as session, session.begin():
        story = Story(
            canonical_headline="Verification example",
            status="DISCOVERED",
            risk_level=risk,
            story_metadata={},
        )
        session.add(story)
        session.flush()
        claim = Claim(
            story_id=story.id,
            claim_text="The event occurred.",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=risk,
            claim_metadata={},
        )
        session.add(claim)
        session.add(
            Job(
                id=research_run_id,
                job_type="RESEARCH",
                status="COMPLETED",
                priority=2,
                payload={},
                result={},
            )
        )
        session.flush()
        for index, (relation, strength, group, source_level) in enumerate(evidence):
            url = f"https://example.com/evidence-{index}"
            item = EvidenceItem(
                title=f"Evidence {index}",
                url=url,
                evidence_type="ARTICLE",
                evidence_metadata={
                    "relationship_assessments": [
                        {
                            "claim_id": str(claim.id),
                            "candidate_url": url,
                            "relation": relation.value,
                            "strength_score": strength,
                            "source_level": source_level,
                            "independence_group": group,
                        }
                    ]
                },
            )
            session.add(item)
            session.flush()
            evidence_ids.append(item.id)
            session.add(
                ClaimEvidence(
                    claim_id=claim.id,
                    evidence_id=item.id,
                    relation=relation.value,
                    strength_score=Decimal(strength),
                )
            )
    event = EventEnvelope(
        event_type=EventType.EVIDENCE_COLLECTED,
        producer="research-worker",
        producer_version="0.1.0",
        aggregate_type="research_run",
        aggregate_id=research_run_id,
        idempotency_key=f"evidence.collected:{research_run_id}",
        payload={
            "story_id": str(story.id),
            "claim_ids": [str(claim.id)],
            "evidence_ids": [str(item) for item in evidence_ids],
            "research_run_id": str(research_run_id),
        },
    )
    return event, story.id, claim.id


def _verify(
    *,
    risk: RiskLevel = RiskLevel.LOW,
    evidence: tuple[tuple[EvidenceRelation, str, str | None, int | None], ...] = (),
):
    factory = _factory()
    event, story_id, claim_id = _seed(factory, risk=risk, evidence=evidence)
    with factory() as session, session.begin():
        result = _engine().verify_evidence_collection(session, event)
    with factory() as session:
        claim = session.get(Claim, claim_id)
        fact_check = session.scalar(select(FactCheck).where(FactCheck.claim_id == claim_id))
        outbox = session.scalar(
            select(EventOutbox).where(
                EventOutbox.event_type == EventType.FACT_CHECK_COMPLETED.value
            )
        )
        assert claim is not None
        assert fact_check is not None
        assert outbox is not None
        return factory, result, story_id, claim, fact_check, outbox


def test_loader_accepts_canonical_fact_check_policy() -> None:
    policy = FactCheckPolicyLoader(ConfigLoader(Path("config"))).load()
    assert set(policy.labels) == set(FactCheckLabel)
    assert policy.invariants.unverified_is_false is False
    assert policy.thresholds[RiskLevel.HIGH].min_independent_groups == 2


def test_no_evidence_is_unverified_not_false() -> None:
    _, result, _, claim, fact_check, _ = _verify()
    assert result.claim_results[0].status is ClaimVerificationStatus.UNVERIFIED
    assert claim.status is ClaimVerificationStatus.UNVERIFIED
    assert fact_check.label is FactCheckLabel.UNVERIFIED
    assert fact_check.label is not FactCheckLabel.FALSE
    assert fact_check.primary_evidence_count == 0
    assert fact_check.supporting_count == 0
    assert fact_check.contradicting_count == 0
    assert fact_check.ai_run_id is None


def test_strong_low_risk_support_is_supported_true() -> None:
    _, result, _, claim, fact_check, _ = _verify(
        evidence=((EvidenceRelation.DIRECT_SUPPORT, "0.90", None, 2),)
    )
    assert result.claim_results[0].status is ClaimVerificationStatus.SUPPORTED
    assert claim.status is ClaimVerificationStatus.SUPPORTED
    assert fact_check.label is FactCheckLabel.TRUE


def test_high_risk_support_requires_independent_groups() -> None:
    _, result, _, claim, fact_check, _ = _verify(
        risk=RiskLevel.HIGH,
        evidence=((EvidenceRelation.DIRECT_SUPPORT, "0.90", "wire-a", 2),),
    )
    assert result.claim_results[0].status is ClaimVerificationStatus.PARTIALLY_SUPPORTED
    assert claim.status is ClaimVerificationStatus.PARTIALLY_SUPPORTED
    assert fact_check.label is FactCheckLabel.PARTIALLY_TRUE

    _, result, _, claim, fact_check, _ = _verify(
        risk=RiskLevel.HIGH,
        evidence=(
            (EvidenceRelation.DIRECT_SUPPORT, "0.90", "wire-a", 2),
            (EvidenceRelation.DIRECT_SUPPORT, "0.88", "reporter-b", 2),
        ),
    )
    assert result.claim_results[0].independent_support_groups == 2
    assert claim.status is ClaimVerificationStatus.SUPPORTED
    assert fact_check.label is FactCheckLabel.TRUE


def test_primary_evidence_can_satisfy_corroboration() -> None:
    _, _, _, claim, fact_check, _ = _verify(
        risk=RiskLevel.CRITICAL,
        evidence=((EvidenceRelation.DIRECT_SUPPORT, "0.95", None, 1),),
    )
    assert claim.status is ClaimVerificationStatus.SUPPORTED
    assert fact_check.label is FactCheckLabel.TRUE


def test_strong_contradiction_can_refute_but_credible_conflict_is_disputed() -> None:
    _, _, _, claim, fact_check, _ = _verify(
        evidence=((EvidenceRelation.CONTRADICTS, "0.90", None, 1),)
    )
    assert claim.status is ClaimVerificationStatus.REFUTED
    assert fact_check.label is FactCheckLabel.FALSE

    _, _, _, claim, fact_check, _ = _verify(
        evidence=(
            (EvidenceRelation.DIRECT_SUPPORT, "0.90", None, 1),
            (EvidenceRelation.CONTRADICTS, "0.55", None, 2),
        )
    )
    assert claim.status is ClaimVerificationStatus.DISPUTED
    assert fact_check.label is FactCheckLabel.UNVERIFIED


def test_qualifying_evidence_downgrades_decisive_support() -> None:
    _, _, _, claim, fact_check, _ = _verify(
        evidence=(
            (EvidenceRelation.DIRECT_SUPPORT, "0.90", None, 1),
            (EvidenceRelation.QUALIFIES, "0.60", None, 2),
        )
    )
    assert claim.status is ClaimVerificationStatus.PARTIALLY_SUPPORTED
    assert fact_check.label is FactCheckLabel.PARTIALLY_TRUE


def test_story_verified_means_stage_complete_not_all_claims_true() -> None:
    factory, _, story_id, claim, _, completed_outbox = _verify()
    assert claim.status is ClaimVerificationStatus.UNVERIFIED
    completed_event = envelope_from_outbox(completed_outbox)
    with factory() as session, session.begin():
        verified = _engine().mark_story_verified(session, completed_event)
    with factory() as session:
        story = session.get(Story, story_id)
        outbox = session.scalar(
            select(EventOutbox).where(EventOutbox.event_type == EventType.STORY_VERIFIED.value)
        )
        assert story is not None
        assert story.status == "VERIFIED"
        assert outbox is not None
        assert verified.story_id == story_id
        assert outbox.causation_id == completed_event.event_id
        verified_event = envelope_from_outbox(outbox)
        assert set(verified_event.payload) == {
            "story_id",
            "fact_check_ids",
            "confidence_score",
            "risk_level",
            "review_required",
        }
        assert verified_event.payload["fact_check_ids"] == [
            str(completed_event.payload["fact_check_id"])
        ]


def test_fact_check_persists_evidence_counts_and_canonical_event() -> None:
    _, _, _, _, fact_check, outbox = _verify(
        evidence=(
            (EvidenceRelation.DIRECT_SUPPORT, "0.90", "source-a", 1),
            (EvidenceRelation.CONTRADICTS, "0.70", "source-b", 2),
        )
    )
    event = envelope_from_outbox(outbox)

    assert fact_check.primary_evidence_count == 1
    assert fact_check.supporting_count == 1
    assert fact_check.contradicting_count == 1
    assert fact_check.ai_run_id is None
    assert set(event.payload) == {
        "story_id",
        "fact_check_id",
        "label",
        "confidence_score",
        "review_required",
    }
    assert event.payload["fact_check_id"] == str(fact_check.id)


def test_fact_check_semantic_replay_with_new_event_id_is_safe() -> None:
    factory = _factory()
    event, _, _ = _seed(
        factory,
        evidence=((EvidenceRelation.DIRECT_SUPPORT, "0.90", "source-a", 2),),
    )
    with factory() as session, session.begin():
        first = _engine().verify_evidence_collection(session, event)
    replay_data = event.model_dump()
    replay_data["event_id"] = uuid4()
    replay_data["idempotency_key"] = f"evidence.collected:{uuid4()}"
    replay = EventEnvelope.model_validate(replay_data)
    with factory() as session, session.begin():
        second = _engine().verify_evidence_collection(session, replay)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(FactCheck)) == 1
        assert session.scalar(select(func.count()).select_from(EventOutbox)) == 1
    assert second.created_count == 0
    assert second.fact_check_ids == first.fact_check_ids


def test_fact_check_material_evidence_change_creates_new_result() -> None:
    factory = _factory()
    event, _, claim_id = _seed(
        factory,
        evidence=((EvidenceRelation.DIRECT_SUPPORT, "0.90", "source-a", 2),),
    )
    with factory() as session, session.begin():
        first = _engine().verify_evidence_collection(session, event)
    with factory() as session, session.begin():
        relation = session.scalar(select(ClaimEvidence).where(ClaimEvidence.claim_id == claim_id))
        assert relation is not None
        relation.strength_score = Decimal("0.75")
    changed_data = event.model_dump()
    changed_data["event_id"] = uuid4()
    changed_data["idempotency_key"] = f"evidence.collected:{uuid4()}"
    changed = EventEnvelope.model_validate(changed_data)
    with factory() as session, session.begin():
        second = _engine().verify_evidence_collection(session, changed)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(FactCheck)) == 2
        assert session.scalar(select(func.count()).select_from(EventOutbox)) == 2
    assert second.created_count == 1
    assert second.fact_check_ids != first.fact_check_ids


def test_story_verification_semantic_replay_with_new_event_id_is_safe() -> None:
    factory, _, _, _, _, completed_outbox = _verify()
    completed = envelope_from_outbox(completed_outbox)
    with factory() as session, session.begin():
        first = _engine().mark_story_verified(session, completed)
    replay_data = completed.model_dump()
    replay_data["event_id"] = uuid4()
    replay_data["idempotency_key"] = f"fact_check.completed:{uuid4()}"
    replay = EventEnvelope.model_validate(replay_data)
    with factory() as session, session.begin():
        second = _engine().mark_story_verified(session, replay)

    with factory() as session:
        verified_count = session.scalar(
            select(func.count())
            .select_from(EventOutbox)
            .where(EventOutbox.event_type == EventType.STORY_VERIFIED.value)
        )
        assert verified_count == 1
    assert first.created is True
    assert second.created is False
    assert second.event_id == first.event_id


def test_story_verification_waits_for_all_durable_claim_assessments() -> None:
    factory = _factory()
    with factory() as session, session.begin():
        story = Story(
            canonical_headline="Out-of-order verification",
            status="DISCOVERED",
            risk_level=RiskLevel.LOW,
            story_metadata={"sensitive_topics": ["RELIGIOUS_VIOLENCE"]},
        )
        session.add(story)
        session.flush()
        first_claim = Claim(
            story_id=story.id,
            claim_text="First claim",
            status=ClaimVerificationStatus.SUPPORTED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
        )
        second_claim = Claim(
            story_id=story.id,
            claim_text="Second claim",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
        )
        session.add_all([first_claim, second_claim])
        session.flush()
        first_check = FactCheck(
            story_id=story.id,
            claim_id=first_claim.id,
            label=FactCheckLabel.TRUE,
            review_required=False,
            review_state=ReviewState.NOT_READY,
        )
        session.add(first_check)
        session.flush()
        story_id = story.id
        second_claim_id = second_claim.id

    def completed_event(check: FactCheck) -> EventEnvelope:
        return EventEnvelope(
            event_type=EventType.FACT_CHECK_COMPLETED,
            producer="research-worker",
            producer_version="0.1.0",
            aggregate_type="story",
            aggregate_id=story_id,
            idempotency_key=f"fact_check.completed:{check.id}",
            payload={
                "story_id": str(story_id),
                "fact_check_id": str(check.id),
                "label": check.label.value,
                "confidence_score": None,
                "review_required": check.review_required,
            },
        )

    with factory() as session, session.begin():
        waiting = _engine().mark_story_verified(session, completed_event(first_check))
    assert waiting.ready is False
    assert waiting.event_id is None

    with factory() as session, session.begin():
        second_claim = session.get(Claim, second_claim_id)
        assert second_claim is not None
        second_claim.status = ClaimVerificationStatus.UNVERIFIED
        second_check = FactCheck(
            story_id=story_id,
            claim_id=second_claim_id,
            label=FactCheckLabel.UNVERIFIED,
            review_required=False,
            review_state=ReviewState.NOT_READY,
        )
        session.add(second_check)
        session.flush()
        second_event = completed_event(second_check)
    with factory() as session, session.begin():
        completed = _engine().mark_story_verified(session, second_event)

    assert completed.ready is True
    assert completed.created is True
    assert len(completed.fact_check_ids) == 2
    with factory() as session:
        outbox = session.scalar(
            select(EventOutbox).where(EventOutbox.event_type == EventType.STORY_VERIFIED.value)
        )
        assert outbox is not None
        assert envelope_from_outbox(outbox).payload["review_required"] is True
