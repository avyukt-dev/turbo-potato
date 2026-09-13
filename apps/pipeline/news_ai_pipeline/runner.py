"""Bounded concurrent lifecycle supervision, never domain processing or ACK ownership."""

import asyncio
import logging
from contextlib import suppress

logger = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class PipelineRunner:
    def __init__(self, stack, *, stop_event=None):
        self.stack = stack
        self.config = stack.config
        self.stop_event = stop_event if stop_event is not None else asyncio.Event()
        self.ready_event = asyncio.Event()
        self.tasks = ()

    async def _wait(self, seconds):
        with suppress(TimeoutError):
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)

    async def _component_loop(self, name, tick, interval):
        failures = 0
        while not self.stop_event.is_set():
            try:
                await tick()
                failures = 0
            except Exception:
                failures += 1
                logger.warning("pipeline component unavailable component=%s", name)
                if failures >= self.config.max_consecutive_component_failures:
                    raise PipelineError("COMPONENT_UNAVAILABLE") from None
                await self._wait(self.config.component_error_backoff_seconds)
                continue
            await self._wait(interval)

    async def _collector_tick(self):
        result = await self.stack.collector.run_cycle()
        logger.info(
            "pipeline collection feeds_due=%d processed=%d failures=%d",
            result.due_feed_count,
            result.processed_article_count,
            len(result.failures),
        )

    async def _worker_loop(self, name, worker):
        cursor = "0-0"
        next_recovery = 0.0

        async def tick():
            nonlocal cursor, next_recovery
            now = asyncio.get_running_loop().time()
            if now >= next_recovery:
                cursor, _ = await worker.recover_once(
                    min_idle_ms=self.config.pending_min_idle_ms, start_id=cursor
                )
                next_recovery = now + self.config.pending_recovery_interval_seconds
            if not self.stop_event.is_set():
                await worker.run_once()

        await self._component_loop(name, tick, self.config.worker_idle_interval_seconds)

    async def run(self):
        stop_task = None
        try:
            try:
                await asyncio.wait_for(
                    self.stack.ensure_ready(), timeout=self.config.startup_timeout_seconds
                )
            except Exception:
                raise PipelineError("STARTUP_UNAVAILABLE") from None
            if self.stop_event.is_set():
                return
            self.tasks = tuple(
                [
                    asyncio.create_task(
                        self._component_loop(
                            "collector",
                            self._collector_tick,
                            self.config.collector_wake_interval_seconds,
                        ),
                        name="pipeline-collector",
                    ),
                    asyncio.create_task(
                        self._component_loop(
                            "outbox",
                            self.stack.dispatcher.dispatch_once,
                            self.config.dispatcher_interval_seconds,
                        ),
                        name="pipeline-outbox",
                    ),
                ]
                + [
                    asyncio.create_task(self._worker_loop(name, worker), name=f"pipeline-{name}")
                    for name, worker in self.stack.workers.items()
                ]
            )
            self.ready_event.set()
            logger.info("pipeline ready")
            stop_task = asyncio.create_task(self.stop_event.wait(), name="pipeline-stop")
            done, _ = await asyncio.wait(
                (*self.tasks, stop_task), return_when=asyncio.FIRST_COMPLETED
            )
            if any(task in done for task in self.tasks) and not self.stop_event.is_set():
                raise PipelineError("COMPONENT_EXITED")
        finally:
            self.ready_event.clear()
            self.stop_event.set()
            if stop_task is not None:
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)
            if self.tasks:
                _, pending = await asyncio.wait(
                    self.tasks, timeout=self.config.shutdown_timeout_seconds
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*self.tasks, return_exceptions=True)
            await self.stack.close()
