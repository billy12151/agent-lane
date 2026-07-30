"""Adapter contract shared by ACP, shell and test implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..result import AgentResult


class AgentAdapter(ABC):
    @abstractmethod
    async def execute(
        self,
        agent: str,
        prompt: str,
        *,
        timeout: int = 300,
        cwd: str | Path | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Execute one autonomous agent invocation."""
