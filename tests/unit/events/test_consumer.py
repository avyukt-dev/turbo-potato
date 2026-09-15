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
        self.claim_calls: list[tuple[str, str, str, int, str, int]] = []

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

    async def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str = "0-0",
        **kwargs: Any,
    ) -> list[Any]:
        count = int(kwargs.get("count", 10))
        self.claim_calls.append((name, groupname, consumername, min_idle_time, start_id, count))
        return [
            b"0-0",
            [(b"2-0", {b"event": self.event.model_dump_json().encode("utf-8")})],
            [],
        ]

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        self.acks.append((name, groupname, ids))
        return len(ids)


def _event() -> EventEnvelope:
    article_id = uuid4()
    return EventEnvelope(
        event_type=EventType.ARTICLE_DISCOVERED,
        producer="collector",
        producer_version="0.1.0",
        aggregate_type="article",
        aggregate_id=article_id,
        idempotency_key="article:test",
        payload={
            "article_id": str(article_id),
            "source_id": str(uuid4()),
            "source_feed_id": str(uuid4()),
            "canonical_url": "https://example.com/article",
            "title": "Example",
            "published_at": None,
        },
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


def test_stale_pending_messages_can_be_claimed() -> None:
    client = FakeConsumerRedis(_event())
    consumer = RedisStreamConsumer(
        client,
        stream="news:articles",
        group="processor",
        consumer="worker-1",
        count=5,
    )

    next_start, messages = asyncio.run(consumer.claim_stale(min_idle_ms=60_000))

    assert next_start == "0-0"
    assert len(messages) == 1
    assert messages[0].message_id == "2-0"
    assert messages[0].event.event_id == client.event.event_id
    assert client.claim_calls == [("news:articles", "processor", "worker-1", 60_000, "0-0", 5)]


def test_exact_retry_claim_requires_current_owner_and_uses_idle_as_race_fence() -> None:
    event = _event()
    pending_calls = []
    claim_calls = []

    class Client:
        async def xpending_range(self, *args, **kwargs):
            pending_calls.append((args, kwargs))
            return [
                {
                    "message_id": "7-0",
                    "consumer": "worker-1",
                    "time_since_delivered": 37,
                    "times_delivered": 1,
                }
            ]

        async def xclaim(self, *args, **kwargs):
            claim_calls.append((args, kwargs))
            return [(b"7-0", {b"event": event.model_dump_json().encode()})]

    consumer = RedisStreamConsumer(
        Client(), stream="news:articles", group="processor", consumer="worker-1", count=5
    )
    messages = asyncio.run(consumer.claim_owned_pending(["7-0", "7-0"]))

    assert [message.message_id for message in messages] == ["7-0"]
    assert pending_calls == [
        (("news:articles", "processor", "7-0", "7-0", 1), {"consumername": "worker-1"})
    ]
    assert claim_calls == [
        (("news:articles", "processor", "worker-1", 37, ["7-0"]), {"force": False})
    ]


@pytest.mark.parametrize(
    ("owner", "idle_ms"),
    [("worker-2", 50), ("worker-1", 0)],
)
def test_exact_retry_claim_never_steals_peer_or_zero_idle_work(owner, idle_ms) -> None:
    claim_calls = []

    class Client:
        async def xpending_range(self, *args, **kwargs):
            return [
                {
                    "message_id": "8-0",
                    "consumer": owner,
                    "time_since_delivered": idle_ms,
                    "times_delivered": 1,
                }
            ]

        async def xclaim(self, *args, **kwargs):
            claim_calls.append((args, kwargs))
            return []

    consumer = RedisStreamConsumer(
        Client(), stream="news:articles", group="processor", consumer="worker-1"
    )
    assert asyncio.run(consumer.claim_owned_pending(["8-0"])) == []
    assert claim_calls == []


def test_exact_retry_claim_bounds_after_current_owner_matches() -> None:
    event = _event()
    pending_ids = []
    claim_ids = []

    class Client:
        async def xpending_range(self, _stream, _group, minimum, _maximum, _count, **_kwargs):
            pending_ids.append(minimum)
            if minimum == "1-0":
                return []
            return [
                {
                    "message_id": minimum,
                    "consumer": "worker-1",
                    "time_since_delivered": 50,
                    "times_delivered": 1,
                }
            ]

        async def xclaim(self, _stream, _group, _consumer, _idle, message_ids, **_kwargs):
            message_id = message_ids[0]
            claim_ids.append(message_id)
            return [(message_id.encode(), {b"event": event.model_dump_json().encode()})]

    consumer = RedisStreamConsumer(
        Client(), stream="news:articles", group="processor", consumer="worker-1", count=1
    )
    messages = asyncio.run(consumer.claim_owned_pending(["1-0", "2-0", "3-0"]))

    assert [message.message_id for message in messages] == ["2-0"]
    assert pending_ids == ["1-0", "2-0"]
    assert claim_ids == ["2-0"]


@pytest.mark.parametrize("payload", [_event().model_dump_json(), "invalid", None])
def test_own_pending_read_is_scoped_bounded_nonblocking_and_uses_decoder(payload):
    calls = []

    class Client:
        async def xreadgroup(self, group, consumer, streams, **kwargs):
            calls.append((group, consumer, streams, kwargs))
            if payload is None:
                return None
            return [(b"news:articles", [(b"3-0", {b"event": payload.encode()})])]

    consumer = RedisStreamConsumer(
        Client(), stream="news:articles", group="processor", consumer="worker-1", count=5
    )
    messages = asyncio.run(consumer.read_own_pending())
    assert calls == [("processor", "worker-1", {"news:articles": "0"}, {"count": 5})]
    if payload is None:
        assert messages == []
    else:
        assert messages[0].message_id == "3-0"
        assert messages[0].stream == "news:articles"
        if payload == "invalid":
            assert messages[0].event is None and messages[0].decode_error
        else:
            assert messages[0].event == EventEnvelope.model_validate_json(payload)
