from __future__ import annotations

from datetime import timedelta
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
    ResearchRunClaim,
    Story,
)
from news_ai_domain import ClaimVerificationStatus, FactCheckLabel, ReviewState, RiskLevel
from news_ai_editorial import EditorialConfigLoader
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import envelope_from_outbox
from news_ai_evidence import (
    FACT_CHECK_METHODOLOGY_VERSION,
    EvidenceRelation,
    FactCheckEngine,
    FactCheckPolicyLoader,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _engine(
    *,
    methodology_version: str = FACT_CHECK_METHODOLOGY_VERSION,
    policy=None,
) -> FactCheckEngine:
    loader = ConfigLoader(Path("config"))
    return FactCheckEngine(
        policy or FactCheckPolicyLoader(loader).load(),
        EditorialConfigLoader(loader).load().risk_policy,
        methodology_version=methodology_version,
    )


def _seed(
    factory: sessionmaker[Session],
    *,
    risk: RiskLevel = RiskLevel.LOW,
    evidence: tuple[tuple[EvidenceRelation, str, str | None, int | None], ...] = (),
    research_generation: int = 1,
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
            research_generation=research_generation,
            current_research_run_id=research_run_id,
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
        session.add(
            ResearchRunClaim(
                research_run_id=research_run_id,
                claim_id=claim.id,
                research_generation=research_generation,
            )
        )
        for index, (relation, strength, group, source_level) in enumerate(evidence):
            url = f"https://example.com/evidence-{index}"
            item = EvidenceItem(
                title=f"Evidence {index}",
                url=url,
                evidence_type="ARTICLE",
                evidence_metadata={
                    "source_level": source_level,
                    "independence_group": group,
                    "relationship_assessments": [
                        {
                            "claim_id": str(claim.id),
                            "candidate_url": url,
                            "relation": relation.value,
                            "strength_score": strength,
                        }
                    ],
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


def test_many_unresolved_sources_do_not_satisfy_high_risk_corroboration() -> None:
    _, result, _, claim, fact_check, _ = _verify(
        risk=RiskLevel.HIGH,
        evidence=tuple((EvidenceRelation.DIRECT_SUPPORT, "0.90", None, 4) for _index in range(20)),
    )

    assert result.claim_results[0].independent_support_groups == 0
    assert claim.status is ClaimVerificationStatus.PARTIALLY_SUPPORTED
    assert fact_check.label is FactCheckLabel.PARTIALLY_TRUE


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
    assert fact_check.research_run_id is not None
    assert fact_check.research_generation == 1
    assert fact_check.methodology_version == FACT_CHECK_METHODOLOGY_VERSION
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


def test_fact_check_methodology_version_invalidates_semantic_operation() -> None:
    factory = _factory()
    event, _, _ = _seed(
        factory,
        evidence=((EvidenceRelation.DIRECT_SUPPORT, "0.90", "source-a", 2),),
    )
    with factory() as session, session.begin():
        first = _engine().verify_evidence_collection(session, event)
    with factory() as session, session.begin():
        second = _engine(
            methodology_version="fact-check-methodology-v2"
        ).verify_evidence_collection(session, event)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(FactCheck)) == 2
        assert session.scalar(select(func.count()).select_from(EventOutbox)) == 2
    assert second.created_count == 1
    assert second.fact_check_ids != first.fact_check_ids


def test_fact_check_policy_change_invalidates_semantic_operation_independently() -> None:
    factory = _factory()
    event, _, _ = _seed(
        factory,
        evidence=((EvidenceRelation.DIRECT_SUPPORT, "0.90", "source-a", 2),),
    )
    loader = ConfigLoader(Path("config"))
    policy = FactCheckPolicyLoader(loader).load()
    changed_policy = policy.model_copy(update={"schema_version": policy.schema_version + 1})
    with factory() as session, session.begin():
        first = _engine(policy=policy).verify_evidence_collection(session, event)
    with factory() as session, session.begin():
        second = _engine(policy=changed_policy).verify_evidence_collection(session, event)

    assert second.created_count == 1
    assert second.fact_check_ids != first.fact_check_ids


def test_superseded_research_run_cannot_replace_current_fact_check() -> None:
    factory = _factory()
    current_event, story_id, claim_id = _seed(
        factory,
        evidence=((EvidenceRelation.DIRECT_SUPPORT, "0.90", "current-source", 1),),
        research_generation=2,
    )
    superseded_run_id = uuid4()
    with factory() as session, session.begin():
        current_job = session.get(Job, current_event.aggregate_id)
        assert current_job is not None
        item = EvidenceItem(
            title="Superseded contradictory evidence",
            url="https://example.com/superseded-evidence",
            evidence_type="ARTICLE",
            evidence_metadata={
                "source_level": 1,
                "independence_group": "superseded-source",
                "relationship_assessments": [
                    {
                        "claim_id": str(claim_id),
                        "candidate_url": "https://example.com/superseded-evidence",
                        "relation": EvidenceRelation.CONTRADICTS.value,
                        "strength_score": "0.90",
                    }
                ],
            },
        )
        session.add_all(
            [
                Job(
                    id=superseded_run_id,
                    job_type="RESEARCH",
                    status="COMPLETED",
                    priority=2,
                    payload={},
                    result={},
                    created_at=current_job.created_at - timedelta(minutes=5),
                ),
                item,
            ]
        )
        session.flush()
        session.add(
            ClaimEvidence(
                claim_id=claim_id,
                evidence_id=item.id,
                relation=EvidenceRelation.CONTRADICTS.value,
                strength_score=Decimal("0.90"),
            )
        )
        session.add(
            ResearchRunClaim(
                research_run_id=superseded_run_id,
                claim_id=claim_id,
                research_generation=1,
            )
        )
        superseded_evidence_id = item.id

    superseded_event = EventEnvelope(
        event_type=EventType.EVIDENCE_COLLECTED,
        producer="research-worker",
        producer_version="0.1.0",
        aggregate_type="research_run",
        aggregate_id=superseded_run_id,
        idempotency_key=f"evidence.collected:{superseded_run_id}",
        payload={
            "story_id": str(story_id),
            "claim_ids": [str(claim_id)],
            "evidence_ids": [str(superseded_evidence_id)],
            "research_run_id": str(superseded_run_id),
        },
    )

    with factory() as session, session.begin():
        current = _engine().verify_evidence_collection(session, current_event)
    with factory() as session, session.begin():
        superseded = _engine().verify_evidence_collection(session, superseded_event)

    with factory() as session:
        claim = session.get(Claim, claim_id)
        assert claim is not None
        assert claim.status is ClaimVerificationStatus.SUPPORTED
        assert session.scalar(select(func.count()).select_from(FactCheck)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventOutbox)
                .where(EventOutbox.event_type == EventType.FACT_CHECK_COMPLETED)
            )
            == 1
        )
    assert superseded.created_count == 0
    assert superseded.fact_check_ids == ()
    assert superseded.stale_claim_ids == (claim_id,)
    assert current.created_count == 1


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


def test_newer_research_generation_legitimately_replaces_current_fact_check() -> None:
    factory = _factory()
    old_event, story_id, claim_id = _seed(
        factory,
        evidence=((EvidenceRelation.DIRECT_SUPPORT, "0.90", "source-a", 1),),
    )
    with factory() as session, session.begin():
        old = _engine().verify_evidence_collection(session, old_event)

    new_run_id = uuid4()
    with factory() as session, session.begin():
        claim = session.get(Claim, claim_id)
        assert claim is not None
        claim.research_generation = 2
        claim.current_research_run_id = new_run_id
        claim.current_fact_check_id = None
        claim.status = ClaimVerificationStatus.UNASSESSED
        session.add(
            Job(
                id=new_run_id,
                job_type="RESEARCH",
                status="COMPLETED",
                priority=2,
                payload={},
                result={},
            )
        )
        session.add(
            ResearchRunClaim(
                research_run_id=new_run_id,
                claim_id=claim_id,
                research_generation=2,
            )
        )
        evidence = EvidenceItem(
            title="Authoritative contradiction",
            url="https://example.com/new-contradiction",
            evidence_type="OFFICIAL_DOCUMENT",
            evidence_metadata={
                "source_level": 1,
                "independence_group": "official-new",
                "relationship_assessments": [
                    {
                        "claim_id": str(claim_id),
                        "candidate_url": "https://example.com/new-contradiction",
                        "relation": EvidenceRelation.CONTRADICTS.value,
                        "strength_score": "0.95",
                    }
                ],
            },
        )
        session.add(evidence)
        session.flush()
        session.add(
            ClaimEvidence(
                claim_id=claim_id,
                evidence_id=evidence.id,
                relation=EvidenceRelation.CONTRADICTS.value,
                strength_score=Decimal("0.95"),
            )
        )
        evidence_id = evidence.id

    new_event = old_event.model_copy(
        update={
            "event_id": uuid4(),
            "aggregate_id": new_run_id,
            "idempotency_key": f"evidence.collected:{new_run_id}",
            "payload": {
                "story_id": str(story_id),
                "claim_ids": [str(claim_id)],
                "evidence_ids": [str(evidence_id)],
                "research_run_id": str(new_run_id),
            },
        }
    )
    with factory() as session, session.begin():
        new = _engine().verify_evidence_collection(session, new_event)

    with factory() as session:
        claim = session.get(Claim, claim_id)
        current = session.get(FactCheck, new.fact_check_ids[0])
        assert claim is not None and claim.status is ClaimVerificationStatus.REFUTED
        assert claim.current_fact_check_id == current.id
        assert current is not None and current.research_generation == 2
        assert current.research_run_id == new_run_id
        assert session.scalar(select(func.count()).select_from(FactCheck)) == 2
    assert new.fact_check_ids != old.fact_check_ids


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


def test_story_verification_uses_explicit_current_fact_check_not_created_at() -> None:
    factory, _, _, _, current_check, completed_outbox = _verify()
    completed = envelope_from_outbox(completed_outbox)
    with factory() as session, session.begin():
        session.add(
            FactCheck(
                story_id=current_check.story_id,
                claim_id=current_check.claim_id,
                label=FactCheckLabel.FALSE,
                review_required=True,
                review_state=ReviewState.NOT_READY,
                research_run_id=current_check.research_run_id,
                research_generation=current_check.research_generation,
                methodology_version="unselected-future-methodology",
                created_at=current_check.created_at + timedelta(days=1),
            )
        )
    with factory() as session, session.begin():
        result = _engine().mark_story_verified(session, completed)

    assert result.ready is True
    assert result.fact_check_ids == (current_check.id,)


def test_story_verification_waits_for_all_durable_claim_assessments() -> None:
    factory = _factory()
    with factory() as session, session.begin():
        research_run_id = uuid4()
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
            research_generation=1,
            current_research_run_id=research_run_id,
        )
        second_claim = Claim(
            story_id=story.id,
            claim_text="Second claim",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
            research_generation=1,
            current_research_run_id=research_run_id,
        )
        session.add_all([first_claim, second_claim])
        session.flush()
        first_check = FactCheck(
            story_id=story.id,
            claim_id=first_claim.id,
            label=FactCheckLabel.TRUE,
            review_required=False,
            review_state=ReviewState.NOT_READY,
            research_run_id=research_run_id,
            research_generation=1,
            methodology_version=FACT_CHECK_METHODOLOGY_VERSION,
        )
        session.add(first_check)
        session.flush()
        first_claim.current_fact_check_id = first_check.id
        session.add_all(
            [
                ResearchRunClaim(
                    research_run_id=research_run_id,
                    claim_id=first_claim.id,
                    research_generation=1,
                ),
                ResearchRunClaim(
                    research_run_id=research_run_id,
                    claim_id=second_claim.id,
                    research_generation=1,
                ),
            ]
        )
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
            research_run_id=research_run_id,
            research_generation=1,
            methodology_version=FACT_CHECK_METHODOLOGY_VERSION,
        )
        session.add(second_check)
        session.flush()
        second_claim.current_fact_check_id = second_check.id
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
