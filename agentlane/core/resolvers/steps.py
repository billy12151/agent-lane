"""Step output resolvers, including group-aware references."""

from __future__ import annotations

from typing import Any

from .base import ContextResolver, ResolveContext, ResolveResult


def _read_path(value: Any, path: list[str]) -> tuple[bool, Any]:
    current = value
    for segment in path:
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list):
            try:
                index = int(segment)
            except ValueError:
                return False, None
            if 0 <= index < len(current):
                current = current[index]
            else:
                return False, None
        else:
            return False, None
    return True, current


class StepsResolver(ContextResolver):
    prefix = "steps"
    description = "Read a completed upstream step output"

    def __init__(self, group: str | None = None):
        self.group = group

    def resolve(self, key: str, context: ResolveContext) -> ResolveResult:
        parts = key.split(".")
        step_id = parts[0]
        actual_group = context.step_groups.get(step_id)
        # Visibility is checked before existence so a resolver cannot probe
        # whether a step in another group exists by telling "hidden" from "absent".
        if actual_group != self.group:
            expected = self.group or "global"
            return ResolveResult(
                "",
                self._source(key),
                True,
                error=f"step {step_id} is not visible in {expected} namespace",
            )
        if step_id not in context.step_outputs:
            return ResolveResult(
                "", self._source(key), True, error=f"step has no output: {step_id}"
            )
        if parts[1:]:
            value_root = context.step_variables.get(step_id)
            if value_root is None:
                return ResolveResult(
                    "",
                    self._source(key),
                    True,
                    error=f"step has no structured output: {step_id}",
                )
            found, value = _read_path(value_root, parts[1:])
        else:
            found, value = True, context.step_outputs[step_id]
        if not found:
            return ResolveResult("", self._source(key), True, error=f"unknown output field: {key}")
        return ResolveResult(str(value), self._source(key))

    def _source(self, key: str) -> str:
        prefix = f"{self.group}.steps" if self.group else "steps"
        return f"{prefix}:{key}"
