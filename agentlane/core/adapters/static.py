"""Explicit deterministic adapter for tests and dry-run fixtures."""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..errors import UnknownAgentError
from ..result import AgentResult
from .base import AgentAdapter


class StaticAgentAdapter(AgentAdapter):
    def __init__(self, outputs: Mapping[str, Any] | None = None):
        self.outputs = dict(outputs or {})
        self.calls: list[tuple[str, str]] = []
        self._indices: defaultdict[str, int] = defaultdict(int)

    async def execute(
        self,
        agent: str,
        prompt: str,
        *,
        timeout: int = 300,
        cwd: str | Path | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        self.calls.append((agent, prompt))
        if agent not in self.outputs and "*" not in self.outputs:
            raise UnknownAgentError(f"no static output registered for agent: {agent}")
        value = self.outputs.get(agent, self.outputs.get("*"))
        if isinstance(value, list):
            index = self._indices[agent]
            self._indices[agent] += 1
            value = value[min(index, len(value) - 1)]
        if callable(value):
            value = value(agent, prompt)
            if inspect.isawaitable(value):
                value = await value
        if isinstance(value, AgentResult):
            return value
        if isinstance(value, Exception):
            raise value
        return AgentResult.success(value)
