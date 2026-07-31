"""YAML parsing, validation, graph analysis and the public run helper."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .contract import StepOutputContract
from .errors import FlowValidationError
from .state import FlowDefinition, FlowRun, GateOption, StepDefinition

_STEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REFERENCE = re.compile(r"\{([^{}]+)\}")
_GATE_ACTIONS = {"next_step", "goto_step", "terminate"}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FlowValidationError(f"{label} must be a mapping")
    return value


def _integer(value: Any, label: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise FlowValidationError(f"{label} must be an integer")
    return value


def _string(value: Any, label: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise FlowValidationError(f"{label} must be a string")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise FlowValidationError(f"{label} must be a list")
    if any(not isinstance(item, str) for item in value):
        raise FlowValidationError(f"{label} entries must be strings")
    return list(value)


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise FlowValidationError(f"{label} has unknown fields: {', '.join(unknown)}")


class FlowEngine:
    def parse(self, text: str) -> FlowDefinition:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise FlowValidationError(f"invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise FlowValidationError("flow root must be a mapping")
        _reject_unknown(
            data,
            {"name", "version", "description", "defaults", "memory", "secrets", "steps"},
            "flow",
        )

        defaults = _mapping(data.get("defaults"), "defaults")
        memory = _mapping(data.get("memory"), "memory")
        secrets = _mapping(data.get("secrets"), "secrets")
        _reject_unknown(defaults, {"timeout", "retry", "max_visits", "fail_fast"}, "defaults")
        _reject_unknown(memory, {"workspace"}, "memory")
        _reject_unknown(secrets, {"required"}, "secrets")
        raw_steps = data.get("steps")
        if raw_steps is None:
            raw_steps = []
        if not isinstance(raw_steps, list):
            raise FlowValidationError("steps must be a list")

        steps: list[StepDefinition] = []
        for index, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                raise FlowValidationError(f"steps[{index}] must be a mapping")
            _reject_unknown(
                raw,
                {
                    "id",
                    "agent",
                    "prompt",
                    "type",
                    "depends_on",
                    "timeout",
                    "retry",
                    "max_visits",
                    "group",
                    "output",
                    "message",
                    "options",
                    "terminal",
                },
                f"steps[{index}]",
            )
            output = raw.get("output")
            contract = None
            if output is not None:
                output_map = _mapping(output, f"steps[{index}].output")
                _reject_unknown(output_map, {"format", "schema"}, f"steps[{index}].output")
                schema = output_map.get("schema")
                if schema is None:
                    schema = {}
                if not isinstance(schema, dict):
                    raise FlowValidationError(f"steps[{index}].output.schema must be a mapping")
                if any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in schema.items()
                ):
                    raise FlowValidationError(
                        f"steps[{index}].output.schema entries must be string pairs"
                    )
                contract = StepOutputContract(
                    format=_string(
                        output_map.get("format"), f"steps[{index}].output.format", "text"
                    ),
                    schema=dict(schema),
                )

            depends_on = _string_list(raw.get("depends_on"), f"steps[{index}].depends_on")
            options_raw = raw.get("options")
            if options_raw is None:
                options_raw = []
            elif not isinstance(options_raw, list):
                raise FlowValidationError(f"steps[{index}].options must be a list")
            options: list[GateOption] = []
            for option_index, option in enumerate(options_raw):
                if not isinstance(option, dict):
                    raise FlowValidationError(
                        f"steps[{index}].options[{option_index}] must be a mapping"
                    )
                _reject_unknown(
                    option,
                    {"label", "action", "target"},
                    f"steps[{index}].options[{option_index}]",
                )
                options.append(
                    GateOption(
                        label=_string(
                            option.get("label"),
                            f"steps[{index}].options[{option_index}].label",
                        ),
                        action=_string(
                            option.get("action"),
                            f"steps[{index}].options[{option_index}].action",
                        ),
                        target=_string(
                            option.get("target"),
                            f"steps[{index}].options[{option_index}].target",
                        ),
                    )
                )

            terminal = raw.get("terminal", False)
            if not isinstance(terminal, bool):
                raise FlowValidationError(f"steps[{index}].terminal must be a boolean")
            group = raw.get("group")
            if group is not None and not isinstance(group, str):
                raise FlowValidationError(f"steps[{index}].group must be a string")

            steps.append(
                StepDefinition(
                    id=_string(raw.get("id"), f"steps[{index}].id"),
                    agent=_string(raw.get("agent"), f"steps[{index}].agent"),
                    prompt=_string(raw.get("prompt"), f"steps[{index}].prompt"),
                    type=_string(raw.get("type"), f"steps[{index}].type", "agent"),
                    depends_on=depends_on,
                    timeout=raw.get("timeout"),
                    retry=raw.get("retry"),
                    max_visits=raw.get("max_visits"),
                    group=group,
                    output=contract,
                    message=_string(raw.get("message"), f"steps[{index}].message"),
                    options=options,
                    terminal=terminal,
                )
            )

        required_secrets = _string_list(secrets.get("required"), "secrets.required")

        fail_fast = defaults.get("fail_fast", False)
        if not isinstance(fail_fast, bool):
            raise FlowValidationError("defaults.fail_fast must be a boolean")
        return FlowDefinition(
            name=_string(data.get("name"), "name"),
            version=_integer(data.get("version"), "version", 1),
            description=_string(data.get("description"), "description"),
            steps=steps,
            defaults_timeout=_integer(defaults.get("timeout"), "defaults.timeout", 300),
            defaults_retry=_integer(defaults.get("retry"), "defaults.retry", 1),
            defaults_max_visits=_integer(defaults.get("max_visits"), "defaults.max_visits", 3),
            defaults_fail_fast=fail_fast,
            memory_workspace=_string(memory.get("workspace"), "memory.workspace", "default"),
            required_secrets=required_secrets,
            raw_yaml=text,
        )

    def validate(self, flow: FlowDefinition) -> list[str]:
        errors: list[str] = []
        if not flow.name.strip():
            errors.append("flow name is required")
        if flow.version < 1:
            errors.append("flow version must be at least 1")
        if not flow.steps:
            errors.append("flow must contain at least one step")
        if flow.defaults_timeout <= 0:
            errors.append("defaults.timeout must be greater than zero")
        if flow.defaults_retry < 0:
            errors.append("defaults.retry cannot be negative")
        if flow.defaults_max_visits <= 0:
            errors.append("defaults.max_visits must be greater than zero")
        if not flow.memory_workspace:
            errors.append("memory.workspace cannot be empty")
        if any(not item for item in flow.required_secrets):
            errors.append("secrets.required entries cannot be empty")
        if len(flow.required_secrets) != len(set(flow.required_secrets)):
            errors.append("secrets.required entries must be unique")

        ids = [step.id for step in flow.steps]
        known = set(ids)
        if len(ids) != len(known):
            errors.append("step ids must be unique")
        ancestors = self.ancestors(flow)

        for step in flow.steps:
            prefix = step.id or "<missing-id>"
            if not step.id:
                errors.append("each step requires id")
            elif not _STEP_ID.fullmatch(step.id):
                errors.append(f"{prefix}: invalid step id")
            unknown = [dep for dep in step.depends_on if dep not in known]
            if unknown:
                errors.append(f"{prefix}: unknown dependencies: {', '.join(unknown)}")
            if len(step.depends_on) != len(set(step.depends_on)):
                errors.append(f"{prefix}: dependencies must be unique")
            if step.id in step.depends_on:
                errors.append(f"{prefix}: step cannot depend on itself")
            if step.group is not None and not step.group:
                errors.append(f"{prefix}: group cannot be empty")
            if step.timeout is not None and (
                isinstance(step.timeout, bool)
                or not isinstance(step.timeout, int)
                or step.timeout <= 0
            ):
                errors.append(f"{prefix}: timeout must be a positive integer")
            if step.retry is not None and (
                isinstance(step.retry, bool) or not isinstance(step.retry, int) or step.retry < 0
            ):
                errors.append(f"{prefix}: retry must be a non-negative integer")
            if step.max_visits is not None and (
                isinstance(step.max_visits, bool)
                or not isinstance(step.max_visits, int)
                or step.max_visits <= 0
            ):
                errors.append(f"{prefix}: max_visits must be a positive integer")

            if step.type == "agent":
                if not step.agent.strip():
                    errors.append(f"{prefix}: agent is required")
            elif step.type == "human_gate":
                if not step.options:
                    errors.append(f"{prefix}: human_gate requires options")
                labels: set[str] = set()
                for option in step.options:
                    if not option.label:
                        errors.append(f"{prefix}: gate option label is required")
                    if option.label in labels:
                        errors.append(f"{prefix}: gate option labels must be unique")
                    labels.add(option.label)
                    if option.action not in _GATE_ACTIONS:
                        errors.append(f"{prefix}: unsupported gate action {option.action}")
                    if option.action == "goto_step" and option.target not in known:
                        errors.append(f"{prefix}: unknown gate target {option.target}")
            else:
                errors.append(f"{prefix}: unsupported step type {step.type}")

            if step.output is not None:
                errors.extend(f"{prefix}: {item}" for item in step.output.validate_definition())
            errors.extend(self._validate_references(step, flow, ancestors.get(step.id, set())))

        try:
            self.layered_order(flow)
        except FlowValidationError as exc:
            errors.append(str(exc))
        return list(dict.fromkeys(errors))

    def _validate_references(
        self,
        step: StepDefinition,
        flow: FlowDefinition,
        ancestors: set[str],
    ) -> list[str]:
        errors: list[str] = []
        by_id = {item.id: item for item in flow.steps}
        for token in _REFERENCE.findall(step.prompt):
            target: str | None = None
            expected_group: str | None = None
            if token.startswith("steps:"):
                target = token[6:].split(".", 1)[0]
                expected_group = None
            elif ".steps:" in token:
                expected_group, key = token.split(".steps:", 1)
                target = key.split(".", 1)[0]
            elif token.startswith("memory:get:"):
                alias = token[len("memory:get:") :]
                if not alias.isdigit():
                    target = alias
            if target is None:
                continue
            target_step = by_id.get(target)
            if target_step is None:
                errors.append(f"{step.id}: reference targets unknown step {target}")
                continue
            if target not in ancestors:
                errors.append(f"{step.id}: reference target {target} is not an upstream dependency")
            if (
                expected_group is None
                and token.startswith("steps:")
                and target_step.group is not None
            ):
                errors.append(
                    f"{step.id}: grouped step {target} requires {target_step.group}.steps prefix"
                )
            if expected_group is not None and target_step.group != expected_group:
                errors.append(f"{step.id}: step {target} is not in group {expected_group}")
        return errors

    def validate_resolvers(self, flow: FlowDefinition, registry: Any) -> list[str]:
        """Validate token syntax against the registry selected for execution."""

        errors: list[str] = []
        for step in flow.steps:
            for token in _REFERENCE.findall(step.prompt):
                if ":" not in token:
                    errors.append(f"{step.id}: reference has no resolver prefix: {token}")
                    continue
                prefix, key = token.split(":", 1)
                resolver = registry.resolver_for(prefix)
                if resolver is None:
                    errors.append(f"{step.id}: unknown resolver prefix: {prefix}")
                    continue
                ok, message = resolver.validate(key)
                if not ok:
                    errors.append(f"{step.id}: {message}")
        return list(dict.fromkeys(errors))

    def layered_order(self, flow: FlowDefinition) -> list[list[str]]:
        ids = [step.id for step in flow.steps]
        known = set(ids)
        indegree = {step_id: 0 for step_id in ids}
        adjacent: dict[str, list[str]] = {step_id: [] for step_id in ids}
        for step in flow.steps:
            for dependency in step.depends_on:
                if dependency not in known:
                    continue
                adjacent[dependency].append(step.id)
                indegree[step.id] += 1
        layers: list[list[str]] = []
        current = [step_id for step_id in ids if indegree[step_id] == 0]
        seen = 0
        while current:
            layers.append(current)
            seen += len(current)
            next_layer: list[str] = []
            for step_id in current:
                for child in adjacent[step_id]:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        next_layer.append(child)
            current = next_layer
        if seen != len(ids):
            raise FlowValidationError("flow contains a dependency cycle")
        return layers

    def ancestors(self, flow: FlowDefinition) -> dict[str, set[str]]:
        deps = {step.id: list(step.depends_on) for step in flow.steps}
        result: dict[str, set[str]] = {}
        for step in flow.steps:
            seen: set[str] = set()
            stack = list(deps[step.id])
            while stack:
                dep = stack.pop()
                if dep in seen:
                    continue
                seen.add(dep)
                stack.extend(deps.get(dep, []))
            result[step.id] = seen
        return result

    def descendants(self, flow: FlowDefinition, step_id: str) -> set[str]:
        if step_id not in {step.id for step in flow.steps}:
            raise KeyError(step_id)
        children: dict[str, list[str]] = {step.id: [] for step in flow.steps}
        for step in flow.steps:
            for dep in step.depends_on:
                if dep in children:
                    children[dep].append(step.id)
        result: set[str] = set()
        stack = list(children[step_id])
        while stack:
            node = stack.pop()
            if node in result:
                continue
            result.add(node)
            stack.extend(children.get(node, []))
        return result


def parse_flow(text: str, *, validate: bool = True) -> FlowDefinition:
    engine = FlowEngine()
    flow = engine.parse(text)
    if validate:
        errors = engine.validate(flow)
        if errors:
            raise FlowValidationError("; ".join(errors))
    return flow


def load_flow(path: str | Path, *, validate: bool = True) -> FlowDefinition:
    return parse_flow(Path(path).read_text(encoding="utf-8"), validate=validate)


def dump_flow(flow: FlowDefinition) -> str:
    data: dict[str, Any] = {
        "name": flow.name,
        "version": flow.version,
        "description": flow.description,
        "defaults": {
            "timeout": flow.defaults_timeout,
            "retry": flow.defaults_retry,
            "max_visits": flow.defaults_max_visits,
            "fail_fast": flow.defaults_fail_fast,
        },
        "memory": {"workspace": flow.memory_workspace},
        "secrets": {"required": flow.required_secrets},
        "steps": [],
    }
    for step in flow.steps:
        value: dict[str, Any] = {
            "id": step.id,
            "type": step.type,
            "depends_on": step.depends_on,
        }
        if step.agent:
            value["agent"] = step.agent
        if step.prompt:
            value["prompt"] = step.prompt
        if step.timeout is not None:
            value["timeout"] = step.timeout
        if step.retry is not None:
            value["retry"] = step.retry
        if step.max_visits is not None:
            value["max_visits"] = step.max_visits
        if step.group is not None:
            value["group"] = step.group
        if step.output is not None:
            value["output"] = {"format": step.output.format, "schema": step.output.schema}
        if step.message:
            value["message"] = step.message
        if step.options:
            value["options"] = [
                {"label": option.label, "action": option.action, "target": option.target}
                for option in step.options
            ]
        if step.terminal:
            value["terminal"] = True
        data["steps"].append(value)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


async def run_flow(flow: FlowDefinition, *, adapter: Any, **kwargs: Any) -> FlowRun:
    from .runner import StepRunner

    return await StepRunner(adapter=adapter, **kwargs).run(flow, original_yaml=flow.raw_yaml)
