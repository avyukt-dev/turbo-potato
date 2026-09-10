import asyncio
from typing import Any
from uuid import uuid4

import pytest
from news_ai_events import EventEnvelope, EventType
from news_ai_events.consumer import RedisStreamConsumer


class FakeConsumerRedis:
    def __init__(self, event: EventEnvelope, *, group_exists: bool = False) -> None:
        self.event = event
        self.group_exists = group_exists
        self.acks: list[tuple[str, str, tuple[str, ...]]] = []
        self.created_groups: list[tuple[str, str]] = []

    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> bool:
        if self.group_exists:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.created_groups.append((name, groupname))
        return True

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        **kwargs: Any,
    ) -> list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]]:
        return [
            (
                b"news:articles",
                [(b"1-0", {b"event": self.event.model_dump_json().encode("utf-8")})],
            )
        ]

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        self.acks.append((name, groupname, ids))
        return len(ids)


def _event() -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.ARTICLE_DISCOVERED,
        producer="collector",
        producer_version="0.1.0",
        aggregate_type="article",
        aggregate_id=uuid4(),
        idempotency_key="article:test",
    )


def test_existing_group_is_not_an_error() -> None:
    consumer = RedisStreamConsumer(
        FakeConsumerRedis(_event(), group_exists=True),
        stream="news:articles",
        group="processor",
        consumer="worker-1",
    )

    asyncio.run(consumer.ensure_group())


def test_ack_happens_only_after_handler_success() -> None:
    client = FakeConsumerRedis(_event())
    consumer = RedisStreamConsumer(
        client,
        stream="news:articles",
        group="processor",
        consumer="worker-1",
    )
    handled: list[EventEnvelope] = []

    async def handler(event: EventEnvelope) -> None:
        handled.append(event)

    count = asyncio.run(consumer.consume_once(handler))

    assert count == 1
    assert handled[0].event_id == client.event.event_id
    assert client.acks == [("news:articles", "processor", ("1-0",))]


def test_handler_failure_leaves_message_unacked() -> None:
    client = FakeConsumerRedis(_event())
    consumer = RedisStreamConsumer(
        client,
        stream="news:articles",
        group="processor",
        consumer="worker-1",
    )

    async def handler(_event: EventEnvelope) -> None:
        raise RuntimeError("processing failed")

    with pytest.raises(RuntimeError, match="processing failed"):
        asyncio.run(consumer.consume_once(handler))

    assert client.acks == []
