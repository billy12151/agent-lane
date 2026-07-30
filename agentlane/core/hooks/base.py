"""Execution lifecycle hooks, separate from passive observability sinks."""

from __future__ import annotations

import logging
from typing import Any

from ..result import AgentResult
from ..state import FlowRun, GateDecision, StepDefinition

logger = logging.getLogger(__name__)


class FlowHook:
    async def before_step(self, run_id: str, step: StepDefinition, run: FlowRun) -> None:
        pass

    async def after_step(self, run_id: str, step: StepDefinition, result: AgentResult) -> None:
        pass

    async def on_error(self, run_id: str, step: StepDefinition, error: str) -> None:
        pass

    async def on_gate_decision(
        self, run_id: str, step: StepDefinition, decision: GateDecision
    ) -> None:
        pass


class NoOpHook(FlowHook):
    pass


class CompositeHook(FlowHook):
    def __init__(self, hooks: list[FlowHook] | None = None):
        self.hooks = list(hooks or [])

    async def _dispatch(self, method: str, *args: Any) -> None:
        for hook in self.hooks:
            try:
                await getattr(hook, method)(*args)
            except Exception:
                logger.exception("flow hook failed: %s.%s", type(hook).__name__, method)

    async def before_step(self, *args: Any) -> None:
        await self._dispatch("before_step", *args)

    async def after_step(self, *args: Any) -> None:
        await self._dispatch("after_step", *args)

    async def on_error(self, *args: Any) -> None:
        await self._dispatch("on_error", *args)

    async def on_gate_decision(self, *args: Any) -> None:
        await self._dispatch("on_gate_decision", *args)
