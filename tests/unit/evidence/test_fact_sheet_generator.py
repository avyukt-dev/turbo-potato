from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from news_ai_database import (
    Article,
    Base,
    Claim,
    ClaimEvidence,
    EventOutbox,
    EvidenceItem,
    FactCheck,
    FactSheet,
    Source,
    Story,
    StorySource,
)
from news_ai_domain import ClaimVerificationStatus, FactCheckLabel, ReviewState, RiskLevel
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import envelope_from_outbox
from news_ai_evidence import EvidenceRelation, FactSheetGenerator
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


_STATUS_LABELS = (
    (ClaimVerificationStatus.SUPPORTED, FactCheckLabel.TRUE),
    (ClaimVerificationStatus.PARTIALLY_SUPPORTED, FactCheckLabel.PARTIALLY_TRUE),
    (ClaimVerificationStatus.DISPUTED, FactCheckLabel.UNVERIFIED),
    (ClaimVerificationStatus.UNVERIFIED, FactCheckLabel.UNVERIFIED),
    (ClaimVerificationStatus.REFUTED, FactCheckLabel.FALSE),
)


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _seed_verified_story(
    factory: sessionmaker[Session],
) -> tuple[EventEnvelope, UUID, tuple[UUID, ...], tuple[UUID, ...]]:
    with factory() as session, session.begin():
        source = Source(
            name="Example News",
            domain="example.com",
            base_url="https://example.com",
            source_type="NEWS",
            authority_level=2,
            language="en",
            source_metadata={},
        )
        session.add(source)
        session.flush()
        article = Article(
            source_id=source.id,
            canonical_url="https://example.com/story",
            title="Verified story",
            language="en",
        )
        story = Story(
            canonical_headline="Verified story",
            summary="Verified story summary.",
            status="VERIFIED",
            language="en",
            risk_level=RiskLevel.MEDIUM,
            confidence_score=Decimal("0.82"),
            story_metadata={
                "sensitive_topics": ["WAR"],
                "locations": ["New Delhi"],
                "context": ["Context recorded before Fact Sheet generation."],
                "unresolved_questions": ["One material detail remains unresolved."],
            },
        )
        session.add_all([article, story])
        session.flush()
        session.add(
            StorySource(
                story_id=story.id,
                article_id=article.id,
                relationship_type="PRIMARY",
            )
        )

        claims: list[Claim] = []
        checks: list[FactCheck] = []
        for index, (status, label) in enumerate(_STATUS_LABELS):
            claim = Claim(
                story_id=story.id,
                claim_text=f"Verified claim {index}.",
                claim_type="EVENT",
                status=status,
                confidence_score=Decimal("0.80"),
                importance_score=Decimal("0.70"),
                risk_level=RiskLevel.HIGH if index == 4 else RiskLevel.MEDIUM,
                claim_metadata={
                    "sensitive_topics": ["COMMUNAL_VIOLENCE"] if index == 4 else [],
                },
            )
            session.add(claim)
            session.flush()
            check = FactCheck(
                story_id=story.id,
                claim_id=claim.id,
                label=label,
                confidence_score=Decimal("0.80"),
                summary=f"Fact check {index}",
                reasoning_summary="Deterministic evidence evaluation.",
                review_required=True,
                review_state=ReviewState.NOT_READY,
            )
            session.add(check)
            claims.append(claim)
            checks.append(check)
        session.flush()

        support = EvidenceItem(
            source_id=source.id,
            title="Supporting evidence",
            url="https://example.com/support",
            evidence_type="ARTICLE",
            excerpt="Supporting excerpt",
            evidence_metadata={
                "relationship_assessments": [
                    {
                        "claim_id": str(claims[0].id),
                        "relation": EvidenceRelation.DIRECT_SUPPORT.value,
                        "notes": "Directly supports the claim.",
                    }
                ]
            },
        )
        contradiction = EvidenceItem(
            source_id=source.id,
            title="Contradicting evidence",
            url="https://example.com/contradiction",
            evidence_type="OFFICIAL_DOCUMENT",
            excerpt="Contradicting excerpt",
            evidence_metadata={},
        )
        session.add_all([support, contradiction])
        session.flush()
        session.add_all(
            [
                ClaimEvidence(
                    claim_id=claims[0].id,
                    evidence_id=support.id,
                    relation=EvidenceRelation.DIRECT_SUPPORT.value,
                    strength_score=Decimal("0.90"),
                ),
                ClaimEvidence(
                    claim_id=claims[-1].id,
                    evidence_id=contradiction.id,
                    relation=EvidenceRelation.CONTRADICTS.value,
                    strength_score=Decimal("0.95"),
                ),
            ]
        )
        story.story_metadata = {
            **story.story_metadata,
            "counterclaim_ids": [str(claims[2].id)],
        }
        story_id = story.id
        claim_ids = tuple(claim.id for claim in claims)
        fact_check_ids = tuple(check.id for check in checks)

    event = EventEnvelope(
        event_type=EventType.STORY_VERIFIED,
        producer="research-worker",
        producer_version="0.1.0",
        aggregate_type="story",
        aggregate_id=story_id,
        idempotency_key=f"story.verified:{uuid4()}",
        payload={
            "story_id": str(story_id),
            "claim_ids": [str(item) for item in claim_ids],
            "fact_check_ids": [str(item) for item in fact_check_ids],
            "verification_stage_complete": True,
        },
    )
    return event, story_id, claim_ids, fact_check_ids


def test_generator_preserves_complete_verified_snapshot_and_emits_content_request() -> None:
    factory = _factory()
    event, story_id, claim_ids, fact_check_ids = _seed_verified_story(factory)
    generator = FactSheetGenerator(
        requested_platforms=("INSTAGRAM",),
        requested_formats=("CAROUSEL",),
    )

    with factory() as session, session.begin():
        result = generator.generate(session, event)

    with factory() as session:
        row = session.get(FactSheet, result.fact_sheet_id)
        assert row is not None
        artifact = generator.artifact_from_row(row)
        content_outbox = session.scalar(
            select(EventOutbox).where(EventOutbox.event_type == EventType.CONTENT_REQUESTED.value)
        )
        assert content_outbox is not None
        content_event = envelope_from_outbox(content_outbox)

    assert artifact.story_id == story_id
    assert artifact.version == 1
    assert tuple(item.claim_id for item in artifact.claims) == claim_ids
    assert tuple(item.status for item in artifact.claims) == tuple(
        status for status, _ in _STATUS_LABELS
    )
    assert tuple(item.fact_check_id for item in artifact.fact_checks) == fact_check_ids
    assert artifact.risk_level is RiskLevel.HIGH
    assert artifact.sensitive_topics == ("COMMUNAL_VIOLENCE", "WAR")
    assert artifact.locations == ("New Delhi",)
    assert artifact.unresolved_questions == ("One material detail remains unresolved.",)
    assert artifact.counterclaims[0].status is ClaimVerificationStatus.DISPUTED
    assert artifact.claims[0].evidence_ids
    assert artifact.claims[-1].contradictory_evidence_ids
    assert {item.url for item in artifact.sources} >= {
        "https://example.com/story",
        "https://example.com/support",
    }
    assert content_event.aggregate_type == "fact_sheet"
    assert content_event.aggregate_id == artifact.fact_sheet_id
    assert content_event.causation_id == event.event_id
    assert content_event.payload["fact_sheet_version"] == 1
    assert content_event.payload["requested_platforms"] == ["INSTAGRAM"]
    assert content_event.payload["requested_formats"] == ["CAROUSEL"]


def test_material_regeneration_creates_new_version_without_mutating_old_one() -> None:
    factory = _factory()
    event, story_id, _, _ = _seed_verified_story(factory)
    generator = FactSheetGenerator()
    with factory() as session, session.begin():
        first = generator.generate(session, event)
    with factory() as session, session.begin():
        story = session.get(Story, story_id)
        assert story is not None
        story.summary = "Corrected persisted summary."
    second_event = event.model_copy(update={"event_id": uuid4(), "causation_id": event.event_id})
    with factory() as session, session.begin():
        second = generator.generate(session, second_event)

    with factory() as session:
        first_row = session.get(FactSheet, first.fact_sheet_id)
        second_row = session.get(FactSheet, second.fact_sheet_id)
        assert first_row is not None
        assert second_row is not None
        assert first_row.version == 1
        assert second_row.version == 2
        assert first_row.summary == "Verified story summary."
        assert second_row.summary == "Corrected persisted summary."


def test_generator_rejects_unassessed_claim_even_when_event_claims_verified() -> None:
    factory = _factory()
    event, _, claim_ids, _ = _seed_verified_story(factory)
    with factory() as session, session.begin():
        claim = session.get(Claim, claim_ids[0])
        assert claim is not None
        claim.status = ClaimVerificationStatus.UNASSESSED

    with factory() as session, session.begin(), pytest.raises(
        ValueError, match="UNASSESSED"
    ):
        FactSheetGenerator().generate(session, event)


def test_generator_rejects_fact_check_claim_mismatch() -> None:
    factory = _factory()
    event, _, _, fact_check_ids = _seed_verified_story(factory)
    reversed_ids = list(reversed(fact_check_ids))
    bad_event = event.model_copy(
        update={
            "event_id": uuid4(),
            "payload": {**event.payload, "fact_check_ids": [str(item) for item in reversed_ids]},
        }
    )

    with factory() as session, session.begin(), pytest.raises(
        ValueError, match="mismatched fact check"
    ):
        FactSheetGenerator().generate(session, bad_event)
