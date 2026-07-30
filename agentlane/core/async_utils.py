"""Async boundaries for synchronous extension APIs."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any, TypeVar

T = TypeVar("T")


async def run_sync_with_timeout(function: Callable[..., T], *args: Any, timeout: float) -> T:
    """Run a synchronous extension on a daemon thread with a hard wait limit.

    Python cannot forcibly stop a running thread. On timeout the work is
    abandoned, but unlike the default asyncio executor it cannot hold loop
    shutdown open indefinitely.
    """

    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def finish() -> None:
        try:
            result = function(*args)
        except Exception as exc:
            error = exc

            def callback() -> None:
                if not future.done():
                    future.set_exception(error)

        else:
            value = result

            def callback() -> None:
                if not future.done():
                    future.set_result(value)

        with suppress(RuntimeError):
            loop.call_soon_threadsafe(callback)

    threading.Thread(target=finish, daemon=True).start()
    return await asyncio.wait_for(future, timeout=timeout)
