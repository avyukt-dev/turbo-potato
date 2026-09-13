"""Redis Streams transport boundary.

The publisher accepts any async client exposing `xadd`, which keeps transport logic testable without
requiring Redis during unit tests.
"""

from typing import Any, Protocol

from .envelope import EventEnvelope
from .types import EventType


class AsyncStreamClient(Protocol):
    async def xadd(self, name: str, fields: dict[str, Any], **kwargs: Any) -> Any: ...


STREAM_BY_EVENT: dict[EventType, str] = {
    EventType.ARTICLE_DISCOVERED: "news:articles",
    EventType.ARTICLE_NORMALIZED: "news:articles",
    EventType.STORY_CREATED: "news:stories",
    EventType.STORY_CLUSTERED: "news:stories",
    EventType.CLAIMS_EXTRACTED: "news:stories",
    EventType.EVIDENCE_REQUESTED: "news:evidence",
    EventType.EVIDENCE_COLLECTED: "news:evidence",
    EventType.FACT_CHECK_COMPLETED: "news:evidence",
    EventType.STORY_VERIFIED: "news:stories",
    EventType.CONTENT_REQUESTED: "news:content",
    EventType.CONTENT_GENERATED: "news:content",
    EventType.CONTENT_QUALITY_CHECKED: "news:content",
    EventType.PUBLICATION_SCHEDULED: "news:publishing",
    EventType.PUBLICATION_EXECUTED: "news:publishing",
    EventType.PUBLICATION_FAILED: "news:publishing",
    EventType.ANALYTICS_REQUESTED: "news:analytics",
    EventType.ANALYTICS_COLLECTED: "news:analytics",
}


def stream_for_event(event_type: EventType) -> str:
    return STREAM_BY_EVENT[event_type]


class RedisStreamPublisher:
    def __init__(self, client: AsyncStreamClient, *, maxlen: int | None = None) -> None:
        self.client = client
        self.maxlen = maxlen

    async def publish(self, event: EventEnvelope) -> str:
        kwargs: dict[str, Any] = {}
        if self.maxlen is not None:
            kwargs.update(maxlen=self.maxlen, approximate=True)
        message_id = await self.client.xadd(
            stream_for_event(event.event_type),
            {"event": event.model_dump_json()},
            **kwargs,
        )
        if isinstance(message_id, bytes):
            return message_id.decode("utf-8")
        return str(message_id)
