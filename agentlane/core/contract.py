"""Definition-time and runtime output contract validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .result import AgentResult

_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "null": lambda value: value is None,
}


@dataclass(slots=True)
class StepOutputContract:
    format: str = "text"
    schema: dict[str, str] = field(default_factory=dict)

    def validate_definition(self) -> list[str]:
        errors: list[str] = []
        if self.format not in {"text", "json", "markdown"}:
            errors.append(f"unsupported output format: {self.format}")
        if self.schema and self.format != "json":
            errors.append("output schema is only valid for json format")
        for field_name, field_type in self.schema.items():
            if not field_name:
                errors.append("output schema field name cannot be empty")
            if field_type not in _TYPE_CHECKS:
                errors.append(f"unsupported schema type for {field_name}: {field_type}")
        return errors

    def validate(self, result: AgentResult) -> list[str]:
        if self.format == "text":
            return [] if isinstance(result.output, str) else ["output must be text"]
        if self.format == "markdown":
            if not isinstance(result.output, str):
                return ["markdown output must be text"]
            return [] if result.output.strip() else ["markdown output is empty"]

        try:
            value = (
                result.parsed
                if result.parsed is not None
                else result.output
                if isinstance(result.output, (dict, list))
                else json.loads(result.output)
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return ["output is not valid JSON"]
        if not isinstance(value, dict):
            return ["JSON output must be an object"]

        errors: list[str] = []
        for field_name, field_type in self.schema.items():
            if field_name not in value:
                errors.append(f"missing field: {field_name}")
                continue
            check = _TYPE_CHECKS.get(field_type)
            if check is not None and not check(value[field_name]):
                errors.append(f"{field_name} must be {field_type}")
        return errors
