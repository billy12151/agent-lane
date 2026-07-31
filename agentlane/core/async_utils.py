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

_DEFAULT_MAX_WORKERS = max(4, os.cpu_count() or 4)


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


class WorkerPool:
    """A bounded pool of daemon threads that drains a shared work queue.

    A prompt can reference many tokens at once and ``render()`` dispatches them
    concurrently. Serving each on its own thread would let a single prompt spawn
    an unbounded number of threads, so a fixed pool of daemon workers drains a
    shared queue instead: the thread count stays bounded, and because they are
    daemon threads a timed-out or hung extension cannot hold loop shutdown open
    the way a default asyncio executor can.

    The pool is an injectable object rather than hidden module state, so a host
    can size/replace it and tests can isolate it. ``WorkerPool.default()`` returns
    a process-wide lazily-created pool for callers that do not inject one.
    """

    def __init__(
        self,
        *,
        max_workers: int = _DEFAULT_MAX_WORKERS,
        name: str = "agentlane-resolver",
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._queue: queue.Queue[Any] = queue.Queue()
        self._max_workers = max_workers
        self._name = name
        self._started = False
        self._start_lock = threading.Lock()

    def _ensure_workers(self) -> None:
        with self._start_lock:
            if self._started:
                return
            for index in range(self._max_workers):
                threading.Thread(
                    target=self._worker, daemon=True, name=f"{self._name}-{index}"
                ).start()
            self._started = True

    def _worker(self) -> None:
        while True:
            function, args, loop, future = self._queue.get()
            try:
                result = function(*args)
            except Exception as exc:
                _deliver(future, loop, error=exc)
            else:
                _deliver(future, loop, result=result)
            self._queue.task_done()

    def submit(
        self, function: Callable[..., T], *args: Any, loop: asyncio.AbstractEventLoop
    ) -> asyncio.Future[T]:
        self._ensure_workers()
        future: asyncio.Future[T] = loop.create_future()
        self._queue.put((function, args, loop, future))
        return future


_default_pool: WorkerPool | None = None
_default_pool_lock = threading.Lock()


def default_pool() -> WorkerPool:
    """Return the process-wide default worker pool, creating it lazily."""

    global _default_pool
    with _default_pool_lock:
        if _default_pool is None:
            _default_pool = WorkerPool()
        return _default_pool


async def run_sync_with_timeout(
    function: Callable[..., T],
    *args: Any,
    timeout: float,
    pool: WorkerPool | None = None,
) -> T:
    """Run a synchronous extension on a bounded pool of daemon threads.

    Python cannot forcibly stop a running thread. On timeout the wait is
    abandoned, but the work stays queued and a worker still runs it when free;
    the bounded pool keeps that resource usage capped. Callers must catch
    ``asyncio.TimeoutError`` — on Python 3.11+ it is ``builtins.TimeoutError``
    and on 3.10 a distinct type, so always name ``asyncio.TimeoutError``
    explicitly.
    """

    resolved_pool = pool or default_pool()
    loop = asyncio.get_running_loop()
    future = resolved_pool.submit(function, *args, loop=loop)
    return await asyncio.wait_for(future, timeout=timeout)
