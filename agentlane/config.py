"""Standalone CLI configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .core.errors import FlowValidationError


@dataclass(slots=True)
class AgentLaneConfig:
    home: Path
    state_file: Path
    logs_dir: Path
    flows_dir: Path
    agents_dir: Path
    agents: dict[str, str | list[str]] = field(default_factory=dict)
    memory_enabled: bool = False
    memory_workspace: str = "default"
    auto_prune: bool = False
    keep_days: int = 7
    keep_failed: bool = True


def default_home() -> Path:
    return Path(os.environ.get("AGENTLANE_HOME", "~/.agentlane")).expanduser()


def load_config(path: str | Path | None = None) -> AgentLaneConfig:
    home = default_home()
    config_path = Path(path).expanduser() if path is not None else home / "config.yml"
    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise FlowValidationError(f"invalid config {config_path}: {exc}") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise FlowValidationError(f"config root must be a mapping: {config_path}")
        data = loaded

    def reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
        unknown = sorted(str(key) for key in value if key not in allowed)
        if unknown:
            raise FlowValidationError(f"config {label} has unknown fields: {', '.join(unknown)}")

    def mapping(key: str) -> dict[str, Any]:
        value = data.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise FlowValidationError(f"config {key} must be a mapping")
        return value

    paths = mapping("paths")
    state = mapping("state")
    logs = mapping("logs")
    flows = mapping("flows")
    memory = mapping("memory")
    agents = mapping("agents")
    cleanup = mapping("cleanup")

    reject_unknown(
        data,
        {"paths", "state", "logs", "flows", "memory", "agents", "cleanup"},
        "root",
    )
    reject_unknown(paths, {"state", "logs", "flows"}, "paths")
    reject_unknown(state, {"file", "auto_prune", "keep_days", "keep_failed"}, "state")
    reject_unknown(logs, {"dir"}, "logs")
    reject_unknown(flows, {"dir"}, "flows")
    reject_unknown(memory, {"enabled", "workspace"}, "memory")
    reject_unknown(cleanup, {"auto_prune", "keep_days", "keep_failed"}, "cleanup")
    if "commands" in agents:
        reject_unknown(agents, {"dir", "commands"}, "agents")

    commands = agents.get("commands")
    if commands is None:
        commands = {key: value for key, value in agents.items() if key != "dir"}
    if not isinstance(commands, dict):
        raise FlowValidationError("config agents.commands must be a mapping")
    normalized_agents: dict[str, str | list[str]] = {}
    for name, value in commands.items():
        if not isinstance(name, str) or not name:
            raise FlowValidationError("config agent names must be non-empty strings")
        if isinstance(value, dict):
            reject_unknown(value, {"command"}, f"agents.commands.{name}")
            command = value.get("command")
        else:
            command = value
        if not isinstance(command, (str, list)):
            raise FlowValidationError(f"agent {name} command must be string or list")
        if isinstance(command, str) and not command.strip():
            raise FlowValidationError(f"agent {name} command cannot be empty")
        if isinstance(command, list) and (
            not command or any(not isinstance(item, str) for item in command)
        ):
            raise FlowValidationError(f"agent {name} command list must contain strings")
        normalized_agents[name] = command

    def configured(value: Any, fallback: Path, label: str) -> Path:
        if value is not None and not isinstance(value, (str, Path)):
            raise FlowValidationError(f"config {label} must be a path string")
        return Path(value).expanduser() if value else fallback

    memory_enabled = memory.get("enabled", False)
    if not isinstance(memory_enabled, bool):
        raise FlowValidationError("config memory.enabled must be a boolean")
    memory_workspace = memory.get("workspace", "default")
    if not isinstance(memory_workspace, str) or not memory_workspace:
        raise FlowValidationError("config memory.workspace must be a non-empty string")
    auto_prune = cleanup.get("auto_prune", state.get("auto_prune", False))
    keep_failed = cleanup.get("keep_failed", state.get("keep_failed", True))
    keep_days = cleanup.get("keep_days", state.get("keep_days", 7))
    if not isinstance(auto_prune, bool) or not isinstance(keep_failed, bool):
        raise FlowValidationError("config cleanup flags must be booleans")
    if isinstance(keep_days, bool) or not isinstance(keep_days, int) or keep_days < 0:
        raise FlowValidationError("config cleanup.keep_days must be a non-negative integer")

    return AgentLaneConfig(
        home=home,
        state_file=configured(
            paths.get("state", state.get("file")), home / "runs.json", "state.file"
        ),
        logs_dir=configured(paths.get("logs", logs.get("dir")), home / "logs", "logs.dir"),
        flows_dir=configured(paths.get("flows", flows.get("dir")), home / "flows", "flows.dir"),
        agents_dir=configured(agents.get("dir"), home / "agents", "agents.dir"),
        agents=normalized_agents,
        memory_enabled=memory_enabled,
        memory_workspace=memory_workspace,
        auto_prune=auto_prune,
        keep_days=keep_days,
        keep_failed=keep_failed,
    )
