from __future__ import annotations

import pytest
import yaml

from agentlane.agents import agent_commands, load_agent_specs
from agentlane.config import load_config


def test_builtin_agent_specs_are_loadable(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLANE_HOME", str(tmp_path))
    config = load_config()
    specs = load_agent_specs(config)
    assert {"codex", "claude-code", "gemini-cli"} <= set(specs)
    assert specs["codex"].executable == "codex"
    assert "code_review" in specs["codex"].capabilities
    # Autonomous-execution flags are load-bearing: without them the agent can
    # only react to literal prompt text and cannot read files / run commands,
    # collapsing the flow into plain prompt routing. Guard against removal.
    assert "--dangerously-bypass-approvals-and-sandbox" in specs["codex"].command
    assert "--dangerously-skip-permissions" in specs["claude-code"].command


def test_user_spec_overrides_builtin_and_bad_spec_is_skipped(tmp_path, monkeypatch, caplog):
    home = tmp_path / "home"
    agents_dir = home / "agents"
    agents_dir.mkdir(parents=True)
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    agents_dir.joinpath("codex.agent.yml").write_text(
        yaml.safe_dump(
            {
                "id": "codex",
                "display_name": "Custom Codex",
                "runtime": {"command": ["custom-codex", "run"]},
                "capabilities": ["review"],
            }
        ),
        encoding="utf-8",
    )
    agents_dir.joinpath("broken.agent.yml").write_text("- bad\n", encoding="utf-8")
    specs = load_agent_specs(load_config())
    assert specs["codex"].display_name == "Custom Codex"
    assert specs["codex"].command == ["custom-codex", "run"]
    assert "skipping agent spec" in caplog.text


def test_inline_commands_override_specs(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLANE_HOME", str(tmp_path))
    tmp_path.joinpath("config.yml").write_text(
        yaml.safe_dump(
            {"agents": {"commands": {"codex": {"command": ["override"]}, "worker": "work"}}}
        ),
        encoding="utf-8",
    )
    commands = agent_commands(load_config())
    assert commands["codex"] == ["override"]
    assert commands["worker"] == "work"


def test_agent_installed_uses_executable_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLANE_HOME", str(tmp_path))
    spec = load_agent_specs(load_config())["codex"]
    monkeypatch.setattr("agentlane.agents.shutil.which", lambda executable: "/bin/codex")
    assert spec.installed


@pytest.mark.parametrize(
    "change",
    [
        {"descrption": "typo"},
        {"description": ["not", "text"]},
        {"capabilities": {}},
        {"detect": []},
        {"runtime": {"command": ["broken"], "shell": True}},
    ],
)
def test_malformed_user_agent_specs_are_visible_and_skipped(tmp_path, monkeypatch, caplog, change):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    monkeypatch.setenv("AGENTLANE_HOME", str(tmp_path))
    value = {
        "id": "broken",
        "display_name": "Broken",
        "runtime": {"command": ["broken"]},
    }
    value.update(change)
    agents_dir.joinpath("broken.agent.yml").write_text(yaml.safe_dump(value), encoding="utf-8")

    assert "broken" not in load_agent_specs(load_config())
    assert "skipping agent spec" in caplog.text


def test_empty_string_command_element_is_allowed(tmp_path, monkeypatch):
    # gemini-cli needs `-p ""` to read the prompt from stdin in headless mode;
    # an empty-string command element is a legitimate argv value, not malformed.
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    monkeypatch.setenv("AGENTLANE_HOME", str(tmp_path))
    agents_dir.joinpath("gemini-cli.agent.yml").write_text(
        yaml.safe_dump(
            {
                "id": "gemini-cli",
                "display_name": "Gemini CLI",
                "runtime": {"command": ["gemini", "--yolo", "-p", ""]},
            }
        ),
        encoding="utf-8",
    )
    spec = load_agent_specs(load_config())["gemini-cli"]
    assert spec.command == ["gemini", "--yolo", "-p", ""]
    # and the same leniency must hold for inline config commands
    tmp_path.joinpath("config.yml").write_text(
        yaml.safe_dump({"agents": {"commands": {"custom": ["bin", ""]}}}), encoding="utf-8"
    )
    assert agent_commands(load_config())["custom"] == ["bin", ""]


def test_builtin_gemini_spec_reads_prompt_from_stdin(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLANE_HOME", str(tmp_path))
    spec = load_agent_specs(load_config())["gemini-cli"]
    # The empty `-p ""` arg is what makes gemini take the prompt from stdin
    # (agentlane feeds prompts via stdin). Guard it against accidental removal.
    assert "-p" in spec.command
    p_index = spec.command.index("-p")
    assert spec.command[p_index + 1] == ""
    # autonomous-execution flag must be present or the agent cannot act
    assert "--yolo" in spec.command
