"""Executable production publisher: python -m news_ai_publisher."""

import asyncio
import logging
import signal
from contextlib import suppress

from news_ai_common.config import AppSettings

from .composition import build_production_publisher_stack
from .runner import run


async def _main():
    stack = build_production_publisher_stack(AppSettings())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    with suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGTERM, stop.set)
    try:
        await run(stack, should_stop=stop.is_set)
    finally:
        with suppress(NotImplementedError):
            loop.remove_signal_handler(signal.SIGTERM)


def main():
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.getLogger(__name__).error("publisher startup or process unavailable")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
