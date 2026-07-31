"""ACP adapter seam used by the OpenClaw runtime integration."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..result import AgentExitCode, AgentResult
from .base import AgentAdapter

ACPTransport = Callable[..., Awaitable[Any] | Any]


class ACPAgentAdapter(AgentAdapter):
    def __init__(self, transport: ACPTransport):
        if transport is None:
            raise ValueError("ACP transport is required")
        self.transport = transport

    async def execute(
        self,
        agent: str,
        prompt: str,
        *,
        timeout: int = 300,
        cwd: str | Path | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        started = time.monotonic()

        async def invoke() -> Any:
            value = self.transport(
                agent=agent,
                prompt=prompt,
                timeout=timeout,
                cwd=str(cwd) if cwd is not None else None,
                **kwargs,
            )
            return await value if inspect.isawaitable(value) else value

        try:
            value = await asyncio.wait_for(invoke(), timeout=timeout)
        except asyncio.TimeoutError:
            return AgentResult.failure(
                f"ACP agent {agent} timed out after {timeout}s",
                exit_code=AgentExitCode.TIMEOUT,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        if isinstance(value, AgentResult):
            return value
        if isinstance(value, dict) and "ok" in value:
            return AgentResult(**value)
        return AgentResult.success(value, duration_ms=int((time.monotonic() - started) * 1000))
