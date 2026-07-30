"""Declarative agent harness specifications and discovery."""

from __future__ import annotations

import logging
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import AgentLaneConfig
from .core.errors import FlowValidationError

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class AgentSpec:
    id: str
    display_name: str
    command: str | list[str]
    description: str = ""
    category: str = "generic"
    install_hint: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    source: str = ""

    @property
    def executable(self) -> str:
        parts = shlex.split(self.command) if isinstance(self.command, str) else self.command
        return parts[0]

    @property
    def installed(self) -> bool:
        return shutil.which(self.executable) is not None


def _parse_spec(path: Path) -> AgentSpec:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FlowValidationError(f"invalid agent spec {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FlowValidationError(f"agent spec root must be a mapping: {path}")
    unknown = sorted(
        str(key)
        for key in value
        if key
        not in {
            "id",
            "display_name",
            "description",
            "category",
            "runtime",
            "detect",
            "capabilities",
        }
    )
    if unknown:
        raise FlowValidationError(f"agent spec has unknown fields {', '.join(unknown)}: {path}")
    agent_id = value.get("id")
    display_name = value.get("display_name")
    runtime = value.get("runtime")
    if not isinstance(agent_id, str) or not agent_id:
        raise FlowValidationError(f"agent spec id is required: {path}")
    if not isinstance(display_name, str) or not display_name:
        raise FlowValidationError(f"agent spec display_name is required: {path}")
    if not isinstance(runtime, dict):
        raise FlowValidationError(f"agent spec runtime must be a mapping: {path}")
    runtime_unknown = sorted(str(key) for key in runtime if key != "command")
    if runtime_unknown:
        raise FlowValidationError(
            f"agent spec runtime has unknown fields {', '.join(runtime_unknown)}: {path}"
        )
    command = runtime.get("command")
    if not isinstance(command, (str, list)):
        raise FlowValidationError(f"agent spec runtime.command is required: {path}")
    if isinstance(command, str) and not command.strip():
        raise FlowValidationError(f"agent spec command cannot be empty: {path}")
    if isinstance(command, list) and (
        not command or any(not isinstance(item, str) for item in command)
    ):
        raise FlowValidationError(f"agent spec command must contain strings: {path}")
    capabilities = value.get("capabilities")
    if capabilities is None:
        capabilities = []
    if not isinstance(capabilities, list) or any(
        not isinstance(item, str) for item in capabilities
    ):
        raise FlowValidationError(f"agent spec capabilities must be a string list: {path}")
    detect = value.get("detect")
    if detect is None:
        detect = {}
    if not isinstance(detect, dict):
        raise FlowValidationError(f"agent spec detect must be a mapping: {path}")
    detect_unknown = sorted(str(key) for key in detect if key != "install_hint")
    if detect_unknown:
        raise FlowValidationError(
            f"agent spec detect has unknown fields {', '.join(detect_unknown)}: {path}"
        )
    install_hint = detect.get("install_hint", "")
    if not isinstance(install_hint, str):
        raise FlowValidationError(f"agent spec install_hint must be a string: {path}")
    description = value.get("description", "")
    if not isinstance(description, str):
        raise FlowValidationError(f"agent spec description must be a string: {path}")
    category = value.get("category", "generic")
    if not isinstance(category, str) or not category:
        raise FlowValidationError(f"agent spec category must be a non-empty string: {path}")
    return AgentSpec(
        id=agent_id,
        display_name=display_name,
        command=command,
        description=description,
        category=category,
        install_hint=install_hint,
        capabilities=tuple(capabilities),
        source=str(path),
    )


def load_agent_specs(config: AgentLaneConfig) -> dict[str, AgentSpec]:
    """Load built-ins, then let valid user specs override matching ids."""

    directories = [Path(__file__).parent / "builtin_agents", config.agents_dir]
    result: dict[str, AgentSpec] = {}
    for directory in directories:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.agent.yml")):
            try:
                spec = _parse_spec(path)
            except FlowValidationError as exc:
                logger.warning("skipping agent spec: %s", exc)
                continue
            result[spec.id] = spec
    return result


def agent_commands(config: AgentLaneConfig) -> dict[str, str | list[str]]:
    commands = {spec.id: spec.command for spec in load_agent_specs(config).values()}
    commands.update(config.agents)
    return commands
