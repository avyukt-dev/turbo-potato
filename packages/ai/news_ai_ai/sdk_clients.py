"""Lifecycle helpers for official SDK clients."""

from __future__ import annotations

import inspect
from contextlib import suppress
from typing import Any


async def close_sdk_client(client: Any, *, asynchronous_namespace: bool = False) -> None:
    """Close a per-request SDK client without masking the request outcome."""

    target = getattr(client, "aio", None) if asynchronous_namespace else client
    if target is None:
        return
    close = getattr(target, "aclose", None) or getattr(target, "close", None)
    if close is None:
        return
    with suppress(Exception):
        result = close()
        if inspect.isawaitable(result):
            await result
