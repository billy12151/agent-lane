from __future__ import annotations

import pytest
import yaml

from agentlane.config import load_config
from agentlane.core.errors import FlowValidationError
from agentlane.core.state import StepStatus
from agentlane.core.state_store import InMemoryStateStore
from agentlane.core.visualize import ascii_graph, mermaid


def test_ascii_graph_layers(linear_flow):
    rendered = ascii_graph(linear_flow)
    assert "Layer 1: ○ first" in rendered
    assert "Layer 2: ○ second" in rendered


def test_ascii_and_mermaid_include_run_status(linear_flow):
    store = InMemoryStateStore()
    run_id = store.create_run("linear", ["first", "second"], linear_flow.raw_yaml)
    store.update_step(run_id, "first", StepStatus.COMPLETED, output="done")
    run = store.load_run(run_id)
    assert "✓ first" in ascii_graph(linear_flow, run)
    source = mermaid(linear_flow, run)
    assert "first --> second" in source
    assert "class first completed" in source


def test_load_default_config_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLANE_HOME", str(tmp_path))
    config = load_config()
    assert config.home == tmp_path
    assert config.state_file == tmp_path / "runs.json"
    assert config.agents_dir == tmp_path / "agents"
    assert config.agents == {}
    assert not config.memory_enabled


def test_load_config_normalizes_agent_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLANE_HOME", str(tmp_path))
    path = tmp_path / "custom.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "paths": {"state": str(tmp_path / "state.json")},
                "agents": {
                    "codex": {"command": ["codex", "exec", "-"]},
                    "simple": "simple-cli --prompt",
                },
            }
        )
    )
    config = load_config(path)
    assert config.agents["codex"] == ["codex", "exec", "-"]
    assert config.agents["simple"] == "simple-cli --prompt"


def test_load_nested_config_and_cleanup_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLANE_HOME", str(tmp_path / "home"))
    path = tmp_path / "nested.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "state": {"file": str(tmp_path / "state.json")},
                "logs": {"dir": str(tmp_path / "events")},
                "flows": {"dir": str(tmp_path / "flows")},
                "agents": {
                    "dir": str(tmp_path / "agents"),
                    "commands": {"worker": {"command": ["worker", "run"]}},
                },
                "memory": {"enabled": True, "workspace": "project-label"},
                "cleanup": {"auto_prune": True, "keep_days": 14, "keep_failed": False},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.state_file == tmp_path / "state.json"
    assert config.logs_dir == tmp_path / "events"
    assert config.flows_dir == tmp_path / "flows"
    assert config.agents_dir == tmp_path / "agents"
    assert config.agents == {"worker": ["worker", "run"]}
    assert config.memory_enabled and config.memory_workspace == "project-label"
    assert config.auto_prune and config.keep_days == 14 and not config.keep_failed


@pytest.mark.parametrize("value", [[], {"agents": []}, {"paths": []}])
def test_invalid_config_shapes(tmp_path, value):
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(value))
    with pytest.raises(FlowValidationError):
        load_config(path)


@pytest.mark.parametrize(
    "value",
    [
        {"agents": {"commands": {"x": []}}},
        {"agents": {"commands": {"x": ""}}},
        {"memory": {"enabled": "yes"}},
        {"memory": {"workspace": ""}},
        {"cleanup": {"keep_days": -1}},
        {"logs": {"dir": []}},
    ],
)
def test_invalid_config_values_are_rejected(tmp_path, value):
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    with pytest.raises(FlowValidationError):
        load_config(path)


@pytest.mark.parametrize(
    "value,message",
    [
        ({"statee": {}}, "root has unknown fields"),
        ({"state": {"fle": "runs.json"}}, "state has unknown fields"),
        (
            {"agents": {"commands": {}, "worker": "run"}},
            "agents has unknown fields",
        ),
        ({"agents": {"commands": {1: "run"}}}, "agent names"),
        (
            {"agents": {"commands": {"worker": {"commnad": "run"}}}},
            "has unknown fields",
        ),
    ],
)
def test_unknown_or_ambiguous_config_fields_are_rejected(tmp_path, value, message):
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    with pytest.raises(FlowValidationError, match=message):
        load_config(path)
