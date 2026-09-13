import asyncio
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from news_ai_pipeline import __main__ as entrypoint


@pytest.mark.parametrize("fallback", [False, True])
def test_signal_shutdown_and_portable_handler_restoration(monkeypatch, fallback):
    async def scenario():
        loop = asyncio.get_running_loop()
        installed, restored = {}, []

        def add(signum, callback):
            if fallback:
                raise NotImplementedError
            installed[signum] = callback

        monkeypatch.setattr(loop, "add_signal_handler", add)
        monkeypatch.setattr(loop, "remove_signal_handler", lambda s: restored.append(s))
        monkeypatch.setattr(signal, "getsignal", lambda s: "previous")

        def set_signal(signum, handler):
            if callable(handler):
                installed[signum] = lambda: handler(signum, None)
            else:
                restored.append(signum)

        monkeypatch.setattr(signal, "signal", set_signal)
        stack = SimpleNamespace(close=AsyncMock())

        async def build(settings):
            return stack

        class Runner:
            def __init__(self, stack, *, stop_event):
                self.stop = stop_event

            async def run(self):
                installed[signal.SIGTERM]()
                await asyncio.wait_for(self.stop.wait(), 1)

        await entrypoint._main(stack_builder=build, runner_type=Runner)
        assert set(restored) == {signal.SIGTERM, signal.SIGINT}
        stack.close.assert_awaited_once()

    asyncio.run(scenario())


def test_console_failure_is_nonzero_without_raw_error(monkeypatch, caplog):
    async def fail():
        raise RuntimeError("SUPER_SECRET_PIPELINE_TOKEN_123")

    monkeypatch.setattr(entrypoint, "_main", fail)
    with pytest.raises(SystemExit) as caught:
        entrypoint.main()
    assert caught.value.code == 1
    assert "SUPER_SECRET_PIPELINE_TOKEN_123" not in caplog.text


def test_installed_console_entrypoint_uses_module_main():
    from importlib.metadata import distribution

    entries = distribution("news-ai-social-manager").entry_points
    entry = next(item for item in entries if item.name == "news-pipeline")
    assert entry.load() is entrypoint.main


def test_python_module_execution_reaches_production_composition_and_runner(monkeypatch):
    import runpy
    import sys

    stack = SimpleNamespace(close=AsyncMock())
    build = AsyncMock(return_value=stack)
    calls = []

    class Runner:
        def __init__(self, received, *, stop_event):
            assert received is stack
            self.stop = stop_event

        async def run(self):
            calls.append("run")
            self.stop.set()

    monkeypatch.setattr("news_ai_pipeline.composition.build_production_pipeline_stack", build)
    monkeypatch.setattr("news_ai_pipeline.runner.PipelineRunner", Runner)
    monkeypatch.delitem(sys.modules, "news_ai_pipeline.__main__", raising=False)
    runpy.run_module("news_ai_pipeline", run_name="__main__")
    build.assert_awaited_once()
    stack.close.assert_awaited_once()
    assert calls == ["run"]
