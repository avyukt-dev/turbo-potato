import asyncio
import logging
from types import SimpleNamespace

import pytest
from news_ai_pipeline import PipelineConfig, PipelineError, PipelineRunner
from pydantic import ValidationError

SENTINEL = "SUPER_SECRET_PIPELINE_TOKEN_123"


@pytest.fixture(autouse=True)
def restore_pipeline_logger_after_in_process_migration_tests(monkeypatch):
    # Alembic fileConfig disables pre-existing loggers when run in this pytest process.
    # Production migrations and the pipeline executable are separate processes.
    monkeypatch.setattr(logging.getLogger("news_ai_pipeline.runner"), "disabled", False)


def fast_config(**updates):
    return PipelineConfig(
        collector_wake_interval_seconds=0.01,
        dispatcher_interval_seconds=0.01,
        worker_idle_interval_seconds=0.01,
        pending_recovery_interval_seconds=0.01,
        pending_min_idle_ms=1,
        component_error_backoff_seconds=0.02,
        max_consecutive_component_failures=3,
        consumer_block_ms=1,
        startup_timeout_seconds=1,
        shutdown_timeout_seconds=0.05,
    ).model_copy(update=updates)


class Worker:
    def __init__(self):
        self.ticks = 0
        self.recoveries = []
        self.ready = False

    async def ensure_ready(self):
        self.ready = True

    async def run_once(self):
        self.ticks += 1

    async def recover_once(self, **arguments):
        self.recoveries.append(arguments)
        return "0-0", None


class Stack:
    config = fast_config()

    def __init__(self):
        self.collector = SimpleNamespace(run_cycle=self.collect)
        self.dispatcher = SimpleNamespace(dispatch_once=self.dispatch)
        self.workers = {"quality": Worker()}
        self.collections = self.dispatches = self.closes = 0

    async def collect(self):
        self.collections += 1
        return SimpleNamespace(due_feed_count=0, processed_article_count=0, failures=[])

    async def dispatch(self):
        self.dispatches += 1

    async def ensure_ready(self):
        for worker in self.workers.values():
            await worker.ensure_ready()

    async def close(self):
        self.closes += 1


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


def test_concurrent_empty_feed_runtime_recovery_and_bounded_shutdown():
    async def scenario():
        stack = Stack()
        runner = PipelineRunner(stack)
        task = asyncio.create_task(runner.run())
        await asyncio.wait_for(runner.ready_event.wait(), 1)
        await wait_until(lambda: stack.dispatches >= 3)
        worker = stack.workers["quality"]
        assert worker.ready and worker.ticks > 0 and worker.recoveries
        assert all(item["min_idle_ms"] == 1 for item in worker.recoveries)
        runner.stop_event.set()
        await asyncio.wait_for(task, 1)
        counts = stack.dispatches, stack.collections, worker.ticks
        await asyncio.sleep(0.03)
        assert counts == (stack.dispatches, stack.collections, worker.ticks)
        assert stack.closes == 1 and not runner.ready_event.is_set()
        assert all(task.done() for task in runner.tasks)
        assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("pipeline-")]

    asyncio.run(scenario())


def test_slow_ai_worker_does_not_block_outbox_or_collector():
    async def scenario():
        stack = Stack()
        blocked = asyncio.Event()

        async def slow():
            await blocked.wait()

        stack.workers["quality"].run_once = slow
        runner = PipelineRunner(stack)
        task = asyncio.create_task(runner.run())
        await wait_until(lambda: stack.dispatches >= 3 and stack.collections >= 3)
        assert not blocked.is_set()
        runner.stop_event.set()
        await asyncio.wait_for(task, 1)
        assert stack.closes == 1 and all(task.done() for task in runner.tasks)

    asyncio.run(scenario())


def test_readiness_waits_for_dependency_and_groups():
    async def scenario():
        stack = Stack()
        gate = asyncio.Event()
        original = stack.ensure_ready

        async def startup():
            await gate.wait()
            await original()

        stack.ensure_ready = startup
        runner = PipelineRunner(stack)
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.02)
        assert not runner.ready_event.is_set() and not stack.dispatches
        gate.set()
        await asyncio.wait_for(runner.ready_event.wait(), 1)
        assert stack.workers["quality"].ready
        runner.stop_event.set()
        await task

    asyncio.run(scenario())


def test_startup_dependency_failure_closes_and_never_declares_ready():
    async def scenario():
        stack = Stack()

        async def broken():
            raise RuntimeError(SENTINEL)

        stack.ensure_ready = broken
        runner = PipelineRunner(stack)
        with pytest.raises(PipelineError, match="STARTUP_UNAVAILABLE"):
            await runner.run()
        assert not runner.ready_event.is_set() and not stack.dispatches and stack.closes == 1

    asyncio.run(scenario())


def test_collector_feed_failures_are_aggregate_only_and_do_not_stop_siblings(caplog):
    async def scenario():
        stack = Stack()

        async def collect():
            stack.collections += 1
            return SimpleNamespace(
                due_feed_count=1,
                processed_article_count=0,
                failures=[SimpleNamespace(message=SENTINEL)],
            )

        stack.collector.run_cycle = collect
        runner = PipelineRunner(stack)
        task = asyncio.create_task(runner.run())
        with caplog.at_level(logging.INFO):
            await wait_until(lambda: stack.collections >= 3 and stack.dispatches >= 3)
            runner.stop_event.set()
            await task
        assert "failures=1" in caplog.text and SENTINEL not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize("raises", [False, True])
def test_unexpected_component_exit_stops_siblings_and_closes(raises):
    class ExitingRunner(PipelineRunner):
        async def _worker_loop(self, name, worker):
            if raises:
                raise RuntimeError(SENTINEL)

    async def scenario():
        stack = Stack()
        runner = ExitingRunner(stack)
        with pytest.raises(PipelineError, match="COMPONENT_EXITED"):
            await runner.run()
        assert stack.closes == 1 and all(task.done() for task in runner.tasks)

    asyncio.run(scenario())


def test_transient_tick_failure_backoff_retries_without_secret_logging(caplog):
    async def scenario():
        stack = Stack()
        attempts = []

        async def flaky():
            attempts.append(asyncio.get_running_loop().time())
            if len(attempts) == 1:
                raise RuntimeError(SENTINEL)
            await stack.dispatch()

        stack.dispatcher.dispatch_once = flaky
        runner = PipelineRunner(stack)
        task = asyncio.create_task(runner.run())
        await wait_until(lambda: stack.dispatches >= 2)
        assert attempts[1] - attempts[0] >= 0.018
        runner.stop_event.set()
        await task

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())
    assert SENTINEL not in caplog.text
    assert "component=outbox" in caplog.text


def test_repeated_component_failure_escalates_not_silent_zombie(caplog):
    async def scenario():
        stack = Stack()

        async def broken():
            raise RuntimeError(SENTINEL)

        stack.dispatcher.dispatch_once = broken
        runner = PipelineRunner(stack)
        with pytest.raises(PipelineError, match="COMPONENT_EXITED"):
            await asyncio.wait_for(runner.run(), 1)
        assert stack.closes == 1

    asyncio.run(scenario())
    assert SENTINEL not in caplog.text
    assert len(caplog.records) == 3


@pytest.mark.parametrize(
    "updates",
    [
        {"worker_idle_interval_seconds": 0},
        {"pending_min_idle_ms": -1},
        {"schema_version": 2},
        {"command": "arbitrary"},
        {"consumer_block_ms": "10"},
        {"max_consecutive_component_failures": 0},
    ],
)
def test_runner_config_is_versioned_closed_and_bounded(updates):
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate(updates)
