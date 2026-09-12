"""Publisher owns only publication.scheduled; output events are ACK-only no-ops."""

from news_ai_events import EventType, ReliableMessageProcessor, WorkerRetryPolicy
from news_ai_events.reliability import PermanentEventError
from news_ai_publishing import PublicationError


class PublisherWorker:
    def __init__(self, consumer, service):
        if consumer.stream != "news:publishing" or consumer.group != "publisher":
            raise ValueError("publisher requires canonical publishing stream/group")
        self.consumer = consumer
        self.service = service
        self.reliability = ReliableMessageProcessor(
            consumer,
            service.factory,
            consumer_group="publisher",
            handled_event_types=frozenset({EventType.PUBLICATION_SCHEDULED}),
            retry_policy=WorkerRetryPolicy(),
            clock=service.service.clock,
        )

    async def ensure_ready(self):
        await self.consumer.ensure_group()

    async def run_once(self):
        return await self.reliability.process(await self.consumer.read(), self._handle)

    async def run_batch(self):
        """One bounded operational tick: durable retries, stale pending, new work."""
        retried = await self.service.retry_due()
        _, recovered = await self.recover_once()
        received = await self.run_once()
        return retried, recovered, received

    async def recover_once(self, *, min_idle_ms=None, start_id="0-0"):
        cursor, messages = await self.consumer.claim_stale(
            min_idle_ms=min_idle_ms or self.service.config.pending_reclaim_idle_ms,
            start_id=start_id,
        )
        return cursor, await self.reliability.process(messages, self._handle)

    async def _handle(self, event):
        try:
            return await self.service.execute(event)
        except PublicationError as exc:
            raise PermanentEventError(exc.code) from exc
