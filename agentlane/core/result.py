"""Normalized results returned by every AgentAdapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class AgentExitCode(IntEnum):
    SUCCESS = 0
    ERROR = 1
    TIMEOUT = 124
    CANCELLED = 130


@dataclass(slots=True)
class AgentResult:
    ok: bool
    output: Any = ""
    error: str | None = None
    exit_code: int = AgentExitCode.SUCCESS
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    parsed: Any = None
    raw: Any = None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    @classmethod
    def success(cls, output: Any = "", **kwargs: Any) -> AgentResult:
        return cls(ok=True, output=output, exit_code=AgentExitCode.SUCCESS, **kwargs)

    @classmethod
    def failure(
        cls,
        error: str,
        *,
        exit_code: int = AgentExitCode.ERROR,
        output: Any = "",
        **kwargs: Any,
    ) -> AgentResult:
        return cls(ok=False, output=output, error=error, exit_code=exit_code, **kwargs)
