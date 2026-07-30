"""Subprocess adapter for autonomous CLI harnesses."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..errors import UnknownAgentError
from ..result import AgentExitCode, AgentResult
from .base import AgentAdapter


class ShellAgentAdapter(AgentAdapter):
    def __init__(
        self,
        commands: Mapping[str, str | Sequence[str]],
        *,
        environment: Mapping[str, str] | None = None,
    ):
        self.commands = dict(commands)
        self.environment = dict(environment or {})

    def command_for(self, agent: str) -> list[str]:
        if agent not in self.commands:
            raise UnknownAgentError(f"agent is not configured: {agent}")
        command = self.commands[agent]
        return shlex.split(command) if isinstance(command, str) else list(command)

    async def execute(
        self,
        agent: str,
        prompt: str,
        *,
        timeout: int = 300,
        cwd: str | Path | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        command = self.command_for(agent)
        started = time.monotonic()
        env = os.environ.copy()
        env.update(self.environment)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")), timeout=timeout
            )
        except TimeoutError:
            await self._terminate(process)
            return AgentResult.failure(
                f"agent {agent} timed out after {timeout}s",
                exit_code=AgentExitCode.TIMEOUT,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except asyncio.CancelledError:
            await self._terminate(process)
            raise

        output = stdout.decode("utf-8", errors="replace")
        error = stderr.decode("utf-8", errors="replace").strip() or None
        duration_ms = int((time.monotonic() - started) * 1000)
        if process.returncode == 0:
            return AgentResult.success(output, duration_ms=duration_ms, raw={"stderr": error})
        return AgentResult.failure(
            error or f"agent exited with code {process.returncode}",
            output=output,
            exit_code=process.returncode or AgentExitCode.ERROR,
            duration_ms=duration_ms,
        )

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()
