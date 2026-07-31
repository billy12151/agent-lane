"""Subprocess adapter for autonomous CLI harnesses."""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..errors import UnknownAgentError
from ..result import AgentExitCode, AgentResult
from .base import AgentAdapter

# POSIX-only APIs used to kill the agent's whole process group. On Windows
# these attributes are absent, so we fall back to terminating just the child.
# Typed as Optional callables so mypy understands the None-guarded call sites.
_GETPGID: Callable[[int], int] | None = getattr(os, "getpgid", None)
_KILLPG: Callable[[int, int], None] | None = getattr(os, "killpg", None)


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
        # start_new_session puts the child in its own process group on POSIX so
        # we can signal the whole group (and the agent's own subprocesses) on
        # timeout. It is a no-op keyword on Windows, where we terminate only the
        # direct child via process.kill().
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=_GETPGID is not None,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")), timeout=timeout
            )
        except asyncio.TimeoutError:
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
    def _process_group(process: asyncio.subprocess.Process) -> int | None:
        """Resolve the child's process group, if it is alive and the OS supports it."""

        if _GETPGID is None:  # Windows
            return None
        try:
            return _GETPGID(process.pid)
        except (ProcessLookupError, PermissionError):
            return None

    @classmethod
    async def _terminate(cls, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        # On POSIX the child started a new session (start_new_session=True), so
        # it leads its own process group. Signal the whole group so grandchildren
        # the agent spawned die with it instead of being orphaned. On Windows we
        # can only terminate the direct child.
        pgid = cls._process_group(process)
        if pgid is not None and _KILLPG is not None:
            with suppress(ProcessLookupError, PermissionError):
                _KILLPG(pgid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            if pgid is not None and _KILLPG is not None:
                with suppress(ProcessLookupError, PermissionError):
                    _KILLPG(pgid, signal.SIGKILL)
            else:
                process.kill()
            await process.wait()
