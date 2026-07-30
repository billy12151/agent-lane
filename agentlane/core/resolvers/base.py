"""Resolver contracts and runtime context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..state import FlowDefinition, StepDefinition


@dataclass(slots=True)
class ResolveResult:
    value: str
    source: str
    missing: bool = False
    cached: bool = False
    timed_out: bool = False
    error: str | None = None


@dataclass(slots=True)
class ResolveContext:
    run_context: dict[str, Any] = field(default_factory=dict)
    step_outputs: dict[str, Any] = field(default_factory=dict)
    step_variables: dict[str, Any] = field(default_factory=dict)
    step_groups: dict[str, str | None] = field(default_factory=dict)
    flow: FlowDefinition | None = None
    current_step: StepDefinition | None = None
    memory_client: Any = None
    secret_provider: Any = None
    workspace: str = "default"


class ContextResolver(ABC):
    prefix: str
    description: str

    @abstractmethod
    def resolve(self, key: str, context: ResolveContext) -> ResolveResult:
        """Resolve one token without braces."""

    def validate(self, key: str) -> tuple[bool, str]:
        return bool(key), "" if key else "reference key cannot be empty"
