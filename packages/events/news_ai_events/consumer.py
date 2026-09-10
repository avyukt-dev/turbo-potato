"""Redis Streams consumer-group transport mechanics.

This layer deliberately does not own domain transactions. A worker handler must perform its durable
state change idempotently; the transport ACK occurs only after the handler returns successfully.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from .envelope import EventEnvelope


class AsyncConsumerClient(Protocol):
    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> Any: ...

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        **kwargs: Any,
    ) -> Any: ...

    async def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str = "0-0",
        **kwargs: Any,
    ) -> Any: ...

    async def xack(self, name: str, groupname: str, *ids: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class StreamMessage:
    stream: str
    message_id: str
    event: EventEnvelope


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def decode_stream_message(stream: Any, message_id: Any, fields: dict[Any, Any]) -> StreamMessage:
    normalized = {_text(key): value for key, value in fields.items()}
    raw_event = normalized.get("event")
    if raw_event is None:
        raise ValueError("Redis stream message is missing 'event' field")
    payload = _text(raw_event)
    try:
        event = EventEnvelope.model_validate_json(payload)
    except ValidationError as exc:
        raise ValueError("Redis stream event envelope is invalid") from exc
    return StreamMessage(stream=_text(stream), message_id=_text(message_id), event=event)


class RedisStreamConsumer:
    def __init__(
        self,
        client: AsyncConsumerClient,
        *,
        stream: str,
        group: str,
        consumer: str,
        block_ms: int = 5_000,
        count: int = 10,
    ) -> None:
        if not stream or not group or not consumer:
            raise ValueError("stream, group, and consumer must not be empty")
        if block_ms < 0 or count < 1:
            raise ValueError("invalid Redis consumer polling options")
        self.client = client
        self.stream = stream
        self.group = group
        self.consumer = consumer
        self.block_ms = block_ms
        self.count = count

    async def ensure_group(self) -> None:
        try:
            await self.client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as exc:  # redis-py exposes BUSYGROUP through ResponseError
            if "BUSYGROUP" not in str(exc).upper():
                raise

    async def read(self) -> list[StreamMessage]:
        response = await self.client.xreadgroup(
            self.group,
            self.consumer,
            {self.stream: ">"},
            count=self.count,
            block=self.block_ms,
        )
        messages: list[StreamMessage] = []
        for stream, entries in response or []:
            for message_id, fields in entries:
                messages.append(decode_stream_message(stream, message_id, fields))
        return messages

    async def claim_stale(
        self,
        *,
        min_idle_ms: int,
        start_id: str = "0-0",
    ) -> tuple[str, list[StreamMessage]]:
        if min_idle_ms < 1:
            raise ValueError("min_idle_ms must be >= 1")
        response = await self.client.xautoclaim(
            self.stream,
            self.group,
            self.consumer,
            min_idle_ms,
            start_id=start_id,
            count=self.count,
        )
        if not response:
            return "0-0", []
        next_start = _text(response[0])
        entries = response[1] if len(response) > 1 else []
        messages = [
            decode_stream_message(self.stream, message_id, fields) for message_id, fields in entries
        ]
        return next_start, messages

    async def ack(self, message: StreamMessage) -> None:
        await self.client.xack(message.stream, self.group, message.message_id)

    async def consume_once(
        self,
        handler: Callable[[EventEnvelope], Awaitable[None]],
    ) -> int:
        processed = 0
        for message in await self.read():
            await handler(message.event)
            await self.ack(message)
            processed += 1
        return processed
