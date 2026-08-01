from __future__ import annotations

import json
import re
import sys

import yaml
from click.testing import CliRunner

from agentlane.cli.main import main
from agentlane.core.state import FlowStatus
from agentlane.core.state_store import JsonFileStateStore


def write_config(home, command=None):
    home.mkdir(parents=True, exist_ok=True)
    agents = {}
    if command is not None:
        agents["worker"] = {"command": command}
    (home / "config.yml").write_text(yaml.safe_dump({"agents": agents}), encoding="utf-8")


def write_flow(path, body=None):
    path.write_text(
        body
        or """name: cli-flow
defaults: {retry: 0}
steps:
  - id: work
    agent: worker
    prompt: hello
""",
        encoding="utf-8",
    )


def run_id_from(output):
    match = re.search(r"run_id=([0-9a-f]+)", output)
    assert match, output
    return match.group(1)


def test_cli_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0a1" in result.output


def test_flow_validate_and_visualize_file(tmp_path):
    flow = tmp_path / "flow.yml"
    write_flow(flow)
    runner = CliRunner()
    valid = runner.invoke(main, ["flow", "validate", str(flow)])
    assert valid.exit_code == 0 and "valid" in valid.output
    visual = runner.invoke(main, ["flow", "visualize", str(flow)])
    assert visual.exit_code == 0 and "Layer 1" in visual.output
    graph = runner.invoke(main, ["flow", "visualize", str(flow), "--mermaid"])
    assert "graph TD" in graph.output


def test_invalid_flow_has_clean_click_error(tmp_path):
    path = tmp_path / "bad.yml"
    path.write_text("name: bad\nsteps: []\n")
    result = CliRunner().invoke(main, ["flow", "validate", str(path)])
    assert result.exit_code != 0
    assert "Error:" in result.output and "at least one step" in result.output


def test_run_uses_real_shell_and_persists_status_list_log_and_delete(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    write_config(
        home,
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
    )
    flow = tmp_path / "flow.yml"
    write_flow(flow)
    cli = CliRunner()
    run = cli.invoke(main, ["flow", "run", str(flow), "--non-interactive"])
    assert run.exit_code == 0, run.output
    assert "status=completed" in run.output
    run_id = run_id_from(run.output)

    listed = cli.invoke(main, ["flow", "list", "--status", "completed"])
    assert listed.exit_code == 0 and run_id in listed.output and "cli-flow" in listed.output
    status = cli.invoke(main, ["flow", "status", run_id])
    assert status.exit_code == 0 and "completed" in status.output and "work" in status.output
    visual = cli.invoke(main, ["flow", "visualize", run_id])
    assert visual.exit_code == 0 and "✓ work" in visual.output
    logs = cli.invoke(main, ["flow", "log", run_id])
    assert logs.exit_code == 0 and "flow_start" in logs.output
    deleted = cli.invoke(main, ["flow", "delete", run_id, "--yes"])
    assert deleted.exit_code == 0
    missing = cli.invoke(main, ["flow", "status", run_id])
    assert missing.exit_code != 0 and "run not found" in missing.output


def test_run_without_agent_configuration_fails_closed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    write_config(home)
    flow = tmp_path / "flow.yml"
    write_flow(flow)
    result = CliRunner().invoke(main, ["flow", "run", str(flow), "--non-interactive"])
    assert result.exit_code == 1
    assert "status=failed" in result.output
    state = json.loads((home / "runs.json").read_text())
    run = next(iter(state["runs"].values()))
    assert run["steps"]["work"]["status"] == "failed"
    assert "not configured" in run["steps"]["work"]["error"]


def test_gate_pause_resume_and_cancel(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    write_config(home, [sys.executable, "-c", "import sys; print(sys.stdin.read())"])
    path = tmp_path / "gate.yml"
    write_flow(
        path,
        """name: gate-flow
steps:
  - id: gate
    type: human_gate
    options:
      - {label: approve, action: next_step}
  - id: after
    agent: worker
    prompt: after
    depends_on: [gate]
""",
    )
    cli = CliRunner()
    paused = cli.invoke(main, ["flow", "run", str(path), "--non-interactive"])
    assert paused.exit_code == 0 and "status=paused" in paused.output
    run_id = run_id_from(paused.output)
    resumed = cli.invoke(main, ["flow", "resume", run_id, "--gate-option", "gate=approve"])
    assert resumed.exit_code == 0 and "status=completed" in resumed.output

    paused_again = cli.invoke(main, ["flow", "run", str(path), "--non-interactive"])
    second_id = run_id_from(paused_again.output)
    cancelled = cli.invoke(main, ["flow", "cancel", second_id])
    assert cancelled.exit_code == 0 and "cancelled" in cancelled.output


def test_gate_notify_writes_notification_file_then_resumes(tmp_path, monkeypatch):
    # --gate-notify is the host-integration path: agentlane pauses at the gate
    # AND writes a JSON file a driving host reads to ask its user. After the
    # host gets an answer it resumes with --gate-option, completing the loop.
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    write_config(home, [sys.executable, "-c", "print(sys.stdin.read())"])
    path = tmp_path / "gate.yml"
    write_flow(
        path,
        """name: notify-flow
steps:
  - id: approval
    type: human_gate
    message: Ship it?
    options:
      - {label: approve, action: next_step}
      - {label: stop, action: terminate}
""",
    )
    cli = CliRunner()
    paused = cli.invoke(main, ["flow", "run", str(path), "--gate-notify"])
    assert paused.exit_code == 0
    assert "status=paused" in paused.output
    run_id = run_id_from(paused.output)

    notify = home / "logs" / f"gate-{run_id}-approval.json"
    assert notify.exists(), "gate-notify must write the notification file"
    import json

    payload = json.loads(notify.read_text())
    assert payload["step_id"] == "approval"
    assert payload["message"] == "Ship it?"
    assert {o["label"] for o in payload["options"]} == {"approve", "stop"}

    # Host "asks its user", gets "approve", and resumes — the loop closes.
    resumed = cli.invoke(main, ["flow", "resume", run_id, "--gate-option", "approval=approve"])
    assert resumed.exit_code == 0 and "status=completed" in resumed.output


def test_retry_step_command_recovers_failed_run(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    write_config(
        home,
        [sys.executable, "-c", "import sys; print('bad', file=sys.stderr); raise SystemExit(2)"],
    )
    path = tmp_path / "retry.yml"
    write_flow(path)
    cli = CliRunner()
    failed = cli.invoke(main, ["flow", "run", str(path), "--non-interactive"])
    assert failed.exit_code == 1
    run_id = run_id_from(failed.output)

    write_config(home, [sys.executable, "-c", "print('recovered')"])
    retried = cli.invoke(main, ["flow", "retry-step", run_id, "work"])
    assert retried.exit_code == 0, retried.output
    status = cli.invoke(main, ["flow", "status", run_id])
    assert "completed" in status.output


def test_delete_requires_confirmation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    write_config(home)
    result = CliRunner().invoke(main, ["flow", "delete", "x"])
    assert result.exit_code != 0 and "--yes" in result.output


def test_auxiliary_commands(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    write_config(home, ["worker"])
    cli = CliRunner()
    assert "worker" in cli.invoke(main, ["agent", "list"]).output
    assert cli.invoke(main, ["agent", "detect"]).exit_code == 0
    resolver_output = cli.invoke(main, ["resolvers", "list"]).output
    assert "memory" in resolver_output and "<group>.steps" in resolver_output
    quickstart = cli.invoke(main, ["quickstart"])
    assert quickstart.exit_code == 0 and "AgentLane home" in quickstart.output
    assert home.joinpath("flows").is_dir()
    assert home.joinpath("flows", "cross-review.agentlane.yml").is_file()


def test_flow_create_from_template_and_refuses_accidental_overwrite(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    target = tmp_path / "created.yml"
    cli = CliRunner()
    created = cli.invoke(
        main,
        [
            "flow",
            "create",
            "--template",
            "codegen-test",
            "--name",
            "review-build",
            "--output",
            str(target),
        ],
    )
    assert created.exit_code == 0, created.output
    assert yaml.safe_load(target.read_text())["name"] == "review-build"
    assert cli.invoke(main, ["flow", "validate", str(target)]).exit_code == 0
    duplicate = cli.invoke(
        main,
        ["flow", "create", "--template", "blank", "--name", "x", "--output", str(target)],
    )
    assert duplicate.exit_code != 0 and "already exists" in duplicate.output


def test_flow_create_interactive_and_invalid_name(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    cli = CliRunner()
    interactive = cli.invoke(main, ["flow", "create"], input="blank\ninteractive-flow\n")
    assert interactive.exit_code == 0, interactive.output
    assert home.joinpath("flows", "interactive-flow.agentlane.yml").exists()
    invalid = cli.invoke(main, ["flow", "create", "--template", "blank", "--name", "bad name"])
    assert invalid.exit_code != 0 and "flow name" in invalid.output


def test_json_output_ephemeral_run_leaves_no_state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    write_config(home, [sys.executable, "-c", "print('ok')"])
    path = tmp_path / "flow.yml"
    write_flow(path)
    result = CliRunner().invoke(
        main,
        ["flow", "run", str(path), "--non-interactive", "--output", "json", "--ephemeral"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "completed"
    assert json.loads(home.joinpath("runs.json").read_text())["runs"] == {}


def test_status_defaults_to_latest_and_prune_is_confirmed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    write_config(home)
    store = JsonFileStateStore(home / "runs.json")
    first = store.create_run("one", ["a"], "name: one\nsteps:\n  - {id: a, agent: x}\n")
    second = store.create_run("two", ["b"], "name: two\nsteps:\n  - {id: b, agent: x}\n")
    store.update_flow_status(first, FlowStatus.COMPLETED)
    store.update_flow_status(second, FlowStatus.COMPLETED)
    cli = CliRunner()
    latest = cli.invoke(main, ["flow", "status"])
    assert latest.exit_code == 0 and second in latest.output
    refused = cli.invoke(main, ["flow", "prune", "--status", "completed"])
    assert refused.exit_code != 0 and "--yes" in refused.output
    pruned = cli.invoke(main, ["flow", "prune", "--status", "completed", "--keep", "1", "--yes"])
    assert pruned.exit_code == 0 and "pruned 1" in pruned.output
    assert len(JsonFileStateStore(home / "runs.json").list_runs()) == 1


def test_bad_gate_choice_and_missing_logs_have_clean_errors(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTLANE_HOME", str(home))
    write_config(home)
    path = tmp_path / "flow.yml"
    write_flow(path)
    cli = CliRunner()
    bad_choice = cli.invoke(main, ["flow", "run", str(path), "--gate-option", "bad"])
    assert bad_choice.exit_code != 0 and "STEP=LABEL" in bad_choice.output
    no_logs = cli.invoke(main, ["flow", "log", "missing"])
    assert no_logs.exit_code != 0 and "no logs" in no_logs.output
