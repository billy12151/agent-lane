"""Async boundaries for synchronous extension APIs."""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any, TypeVar

T = TypeVar("T")

# A prompt can reference many tokens at once and render() dispatches them
# concurrently. Serving each on its own thread would let a single prompt spawn
# an unbounded number of threads, so a fixed pool of daemon workers drains a
# shared queue instead: the thread count stays bounded, and because they are
# daemon threads a timed-out or hung extension cannot hold loop shutdown open
# the way a default asyncio executor can.
_MAX_WORKERS = max(4, os.cpu_count() or 4)
_queue: queue.Queue[Any] = queue.Queue()
_started = False
_start_lock = threading.Lock()


def _deliver(
    future: asyncio.Future[Any],
    loop: asyncio.AbstractEventLoop,
    *,
    result: Any = None,
    error: BaseException | None = None,
) -> None:
    """Schedule the outcome onto the loop, binding values now (not at call time)."""

    def callback() -> None:
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    with suppress(RuntimeError):
        loop.call_soon_threadsafe(callback)


def _worker() -> None:
    while True:
        function, args, loop, future = _queue.get()
        try:
            result = function(*args)
        except Exception as exc:
            _deliver(future, loop, error=exc)
        else:
            _deliver(future, loop, result=result)
        _queue.task_done()


def _ensure_workers() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        for index in range(_MAX_WORKERS):
            threading.Thread(
                target=_worker, daemon=True, name=f"agentlane-resolver-{index}"
            ).start()
        _started = True


async def run_sync_with_timeout(function: Callable[..., T], *args: Any, timeout: float) -> T:
    """Run a synchronous extension on a bounded pool of daemon threads.

    Python cannot forcibly stop a running thread. On timeout the wait is
    abandoned, but the work stays queued and a worker still runs it when free;
    the bounded pool keeps that resource usage capped. Callers must catch
    ``asyncio.TimeoutError`` — on Python 3.11+ it is ``builtins.TimeoutError``
    and on 3.10 a distinct type, so always name ``asyncio.TimeoutError``
    explicitly.
    """

    _ensure_workers()
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()
    _queue.put((function, args, loop, future))
    return await asyncio.wait_for(future, timeout=timeout)
