"""Console and python -m entrypoint for the single upstream production owner."""

import asyncio
import logging
import signal

from news_ai_common.config import AppSettings

from .composition import build_production_pipeline_stack
from .runner import PipelineRunner


async def _main(*, stack_builder=build_production_pipeline_stack, runner_type=PipelineRunner):
    stack = await stack_builder(AppSettings())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = []
    fallback = []
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
                installed.append(signum)
            except (NotImplementedError, RuntimeError):
                try:
                    previous = signal.getsignal(signum)
                    signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
                    fallback.append((signum, previous))
                except ValueError:
                    pass  # A non-main thread may request shutdown through cancellation.
        await runner_type(stack, stop_event=stop).run()
    finally:
        try:
            for signum in installed:
                loop.remove_signal_handler(signum)
            for signum, previous in fallback:
                signal.signal(signum, previous)
        finally:
            await stack.close()  # Idempotent even if startup or signal installation failed.


def main():
    logging.basicConfig(level=logging.INFO)
    # HTTP client INFO request traces contain URLs; operational output is aggregate-only.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.getLogger(__name__).error("pipeline startup or process unavailable")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
