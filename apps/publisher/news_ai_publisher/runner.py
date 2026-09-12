"""Minimal async publisher process, independent of service managers and Stage 27."""

import asyncio
import logging


async def close_stack(stack):
    """Release the process's provider, Redis and SQLAlchemy resources."""
    transport = getattr(stack.service.adapter, "transport", None)
    try:
        if transport is not None and hasattr(transport, "close"):
            await transport.close()
    finally:
        try:
            await stack.worker.consumer.client.aclose()
        finally:
            engine = stack.service.factory.kw.get("bind")
            if engine is not None:
                engine.dispose()


async def run(stack, *, should_stop=lambda: False, wait=asyncio.sleep, shutdown=close_stack):
    """Bounded ticks with normalized diagnostics; cancellation never ACKs active I/O."""
    logger = logging.getLogger(__name__)
    ready = False
    try:
        while not should_stop():
            try:
                if not ready:
                    await stack.worker.ensure_ready()
                    ready = True
                await stack.worker.run_batch()
            except Exception:
                # Never render provider/DB exception strings, bound parameters or traceback.
                logger.error("publisher batch unavailable; durable work retained")
            if not should_stop():
                await wait(stack.service.config.loop_interval_seconds)
    finally:
        try:
            await shutdown(stack)
        except Exception:
            logger.error("publisher resource shutdown did not complete")
