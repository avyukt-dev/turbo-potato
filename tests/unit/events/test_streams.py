import asyncio
from typing import Any
from uuid import uuid4

from news_ai_events import EventEnvelope, EventType
from news_ai_events.streams import RedisStreamPublisher, stream_for_event


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def xadd(self, name: str, fields: dict[str, Any], **kwargs: Any) -> bytes:
        self.calls.append((name, fields, kwargs))
        return b"1-0"


def test_event_maps_to_canonical_stream() -> None:
    assert stream_for_event(EventType.EVIDENCE_REQUESTED) == "news:evidence"
    assert stream_for_event(EventType.PUBLICATION_SCHEDULED) == "news:publishing"


def test_publisher_serializes_envelope() -> None:
    client = FakeRedis()
    publisher = RedisStreamPublisher(client, maxlen=1000)
    article_id = uuid4()
    event = EventEnvelope(
        event_type=EventType.ARTICLE_DISCOVERED,
        producer="collector",
        producer_version="0.1.0",
        aggregate_type="article",
        aggregate_id=article_id,
        idempotency_key="article:1",
        payload={
            "article_id": str(article_id),
            "source_id": str(uuid4()),
            "source_feed_id": str(uuid4()),
            "canonical_url": "https://example.com/article",
            "title": "Example",
            "published_at": None,
        },
    )

    message_id = asyncio.run(publisher.publish(event))

    assert message_id == "1-0"
    stream, fields, kwargs = client.calls[0]
    assert stream == "news:articles"
    assert '"event_type":"article.discovered"' in fields["event"]
    assert kwargs == {"maxlen": 1000, "approximate": True}
