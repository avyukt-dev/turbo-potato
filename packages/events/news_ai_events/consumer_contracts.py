"""Closed transport ownership for currently implemented event consumers."""

from dataclasses import dataclass

from .streams import stream_for_event
from .types import EventType

NORMALIZER_CONSUMER_GROUP = "normalizer"
PROCESSOR_CONSUMER_GROUP = "processor"
CLAIM_CONSUMER_GROUP = "claim-worker"
RESEARCH_PLANNING_CONSUMER_GROUP = "research-planner"
EVIDENCE_COLLECTION_CONSUMER_GROUP = "evidence-collector"
FACT_CHECK_CONSUMER_GROUP = "fact-checker"
STORY_VERIFICATION_CONSUMER_GROUP = "story-verifier"
FACT_SHEET_CONSUMER_GROUP = "fact-sheet-builder"
CONTENT_CONSUMER_GROUP = "content-worker"
QUALITY_CONSUMER_GROUP = "quality-worker"
PUBLISHER_CONSUMER_GROUP = "publisher"


@dataclass(frozen=True, slots=True)
class EventConsumerContract:
    event_type: EventType
    stream: str
    consumer_group: str


def _contract(event_type: EventType, consumer_group: str) -> EventConsumerContract:
    return EventConsumerContract(event_type, stream_for_event(event_type), consumer_group)


EVENT_CONSUMER_CONTRACTS = (
    _contract(EventType.ARTICLE_DISCOVERED, NORMALIZER_CONSUMER_GROUP),
    _contract(EventType.ARTICLE_NORMALIZED, PROCESSOR_CONSUMER_GROUP),
    _contract(EventType.STORY_CREATED, CLAIM_CONSUMER_GROUP),
    _contract(EventType.STORY_CLUSTERED, CLAIM_CONSUMER_GROUP),
    _contract(EventType.CLAIMS_EXTRACTED, RESEARCH_PLANNING_CONSUMER_GROUP),
    _contract(EventType.EVIDENCE_REQUESTED, EVIDENCE_COLLECTION_CONSUMER_GROUP),
    _contract(EventType.EVIDENCE_COLLECTED, FACT_CHECK_CONSUMER_GROUP),
    _contract(EventType.FACT_CHECK_COMPLETED, STORY_VERIFICATION_CONSUMER_GROUP),
    _contract(EventType.STORY_VERIFIED, FACT_SHEET_CONSUMER_GROUP),
    _contract(EventType.CONTENT_REQUESTED, CONTENT_CONSUMER_GROUP),
    _contract(EventType.CONTENT_GENERATED, QUALITY_CONSUMER_GROUP),
    _contract(EventType.PUBLICATION_SCHEDULED, PUBLISHER_CONSUMER_GROUP),
)

CONSUMER_CONTRACT_BY_EVENT = {item.event_type: item for item in EVENT_CONSUMER_CONTRACTS}

if len(CONSUMER_CONTRACT_BY_EVENT) != len(EVENT_CONSUMER_CONTRACTS):  # pragma: no cover
    raise RuntimeError("event consumer contracts contain duplicate ownership")
