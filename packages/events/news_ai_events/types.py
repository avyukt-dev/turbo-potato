"""Canonical event names from docs/EVENTS.md."""

from enum import StrEnum


class EventType(StrEnum):
    ARTICLE_DISCOVERED = "article.discovered"
    ARTICLE_NORMALIZED = "article.normalized"
    STORY_CREATED = "story.created"
    STORY_CLUSTERED = "story.clustered"
    CLAIMS_EXTRACTED = "claims.extracted"
    EVIDENCE_REQUESTED = "evidence.requested"
    EVIDENCE_COLLECTED = "evidence.collected"
    FACT_CHECK_COMPLETED = "fact_check.completed"
    STORY_VERIFIED = "story.verified"
    CONTENT_REQUESTED = "content.requested"
    CONTENT_GENERATED = "content.generated"
    CONTENT_QUALITY_CHECKED = "content.quality_checked"
    PUBLICATION_SCHEDULED = "publication.scheduled"
    PUBLICATION_EXECUTED = "publication.executed"
    PUBLICATION_FAILED = "publication.failed"
    ANALYTICS_REQUESTED = "analytics.requested"
    ANALYTICS_COLLECTED = "analytics.collected"
