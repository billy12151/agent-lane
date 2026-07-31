"""Click/Rich command line interface."""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.table import Table

from .. import __version__
from ..agents import agent_commands, load_agent_specs
from ..config import AgentLaneConfig, load_config
from ..core.adapters import ShellAgentAdapter
from ..core.engine import FlowEngine, dump_flow, load_flow, parse_flow
from ..core.errors import AgentLaneError, FlowValidationError
from ..core.human_gate import GateDriver, PauseGateDriver, PresetGateDriver
from ..core.memory_client import build_memory_client
from ..core.observability import CompositeSink, JsonlSink, SummarySink
from ..core.resolvers import default_registry
from ..core.runner import StepRunner
from ..core.secret_provider import EnvSecretProvider
from ..core.state import FlowStatus, GateOption, StepDefinition
from ..core.state_store import JsonFileStateStore
from ..core.state_store.base import run_to_dict
from ..core.visualize import ascii_graph, mermaid

console = Console()
_FLOW_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
_BUILTIN_FLOW_DIR = Path(__file__).resolve().parents[1] / "builtin_flows"
_FLOW_TEMPLATES = {
    "blank": _BUILTIN_FLOW_DIR / "blank.yml",
    "cross-review": _BUILTIN_FLOW_DIR / "cross-review.yml",
    "cross-review-trio": _BUILTIN_FLOW_DIR / "cross-review-trio.yml",
    "codegen-test": _BUILTIN_FLOW_DIR / "codegen-test.yml",
}


class AgentLaneGroup(click.Group):
    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except AgentLaneError as exc:
            raise click.ClickException(str(exc)) from exc


class InteractiveGateDriver(GateDriver):
    """Prompt the operator on the terminal at a human gate.

    ``click.prompt`` is a blocking call, so it stalls the event loop while it
    waits. That is acceptable here because a gate is a flow-level pause point:
    nothing in the same layer runs concurrently with it, and a human decision
    cannot be awaited cooperatively anyway.
    """

    async def ask(self, step: StepDefinition) -> GateOption | None:
        console.print(f"[yellow]{step.message or step.id}[/yellow]")
        labels = [option.label for option in step.options]
        selected = click.prompt("Choose", type=click.Choice(labels), show_choices=True)
        return next(option for option in step.options if option.label == selected)


def _config(ctx: click.Context) -> AgentLaneConfig:
    value = ctx.ensure_object(dict)
    if "config" not in value:
        value["config"] = load_config(value.get("config_path"))
    return value["config"]


def _store(config: AgentLaneConfig) -> JsonFileStateStore:
    return JsonFileStateStore(config.state_file)


def _adapter(config: AgentLaneConfig) -> ShellAgentAdapter:
    return ShellAgentAdapter(agent_commands(config))


def _choices(values: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise click.BadParameter("gate choices must use STEP=LABEL")
        step_id, label = value.split("=", 1)
        result[step_id] = label
    return result


def _runner(
    config: AgentLaneConfig,
    store: JsonFileStateStore,
    *,
    gate_choices: tuple[str, ...] = (),
    non_interactive: bool = False,
) -> tuple[StepRunner, SummarySink]:
    summary = SummarySink()
    log_sink = JsonlSink(config.logs_dir / "events.jsonl")
    if gate_choices:
        gate_driver: GateDriver = PresetGateDriver(_choices(gate_choices))
    elif non_interactive:
        gate_driver = PauseGateDriver()
    else:
        gate_driver = InteractiveGateDriver()
    runner = StepRunner(
        adapter=_adapter(config),
        store=store,
        sink=CompositeSink([summary, log_sink]),
        memory_client=build_memory_client(config.memory_enabled),
        secret_provider=EnvSecretProvider(),
        gate_driver=gate_driver,
    )
    return runner, summary


def _apply_flow_defaults(definition: Any, config: AgentLaneConfig) -> None:
    try:
        raw = yaml.safe_load(definition.raw_yaml) or {}
    except yaml.YAMLError:
        return
    memory = raw.get("memory") if isinstance(raw, dict) else None
    if not isinstance(memory, dict) or "workspace" not in memory:
        definition.memory_workspace = config.memory_workspace


def _render_result(result: Any, summary: SummarySink, output_format: str) -> None:
    if output_format == "json":
        click.echo(json.dumps(run_to_dict(result), ensure_ascii=False, default=str))
    else:
        rendered = summary.render_tree(result.run_id)
        if rendered:
            console.print(rendered)
        console.print(f"run_id={result.run_id} status={result.status.value}")


def _automatic_cleanup(config: AgentLaneConfig, store: JsonFileStateStore) -> None:
    if not config.auto_prune:
        return
    store.prune(status=FlowStatus.COMPLETED, older_than_days=config.keep_days)
    if not config.keep_failed:
        store.prune(status=FlowStatus.FAILED, older_than_days=config.keep_days)


@click.group(cls=AgentLaneGroup)
@click.option("--config", "config_path", type=click.Path(path_type=Path))
@click.version_option(__version__)
@click.pass_context
def main(ctx: click.Context, config_path: Path | None) -> None:
    """Declarative multi-agent workflow orchestration."""
    ctx.ensure_object(dict)["config_path"] = config_path


@main.group()
def flow() -> None:
    """Create, run and inspect flows."""


@flow.command("validate")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def validate_command(ctx: click.Context, path: Path) -> None:
    definition = load_flow(path)
    _apply_flow_defaults(definition, _config(ctx))
    resolver_errors = FlowEngine().validate_resolvers(definition, default_registry())
    if resolver_errors:
        raise FlowValidationError("; ".join(resolver_errors))
    console.print(f"[green]✓ valid[/green]: {definition.name}")


@flow.command("visualize")
@click.argument("target")
@click.option("--mermaid", "as_mermaid", is_flag=True)
@click.pass_context
def visualize_command(ctx: click.Context, target: str, as_mermaid: bool) -> None:
    path = Path(target)
    run = None
    if path.exists():
        definition = load_flow(path)
    else:
        stored = _store(_config(ctx)).load_run(target)
        if stored is None:
            raise click.ClickException(f"flow file/run not found: {target}")
        run = stored
        definition = parse_flow(stored.flow_yaml_snapshot)
    click.echo(mermaid(definition, run) if as_mermaid else ascii_graph(definition, run))


@flow.command("create")
@click.option("--template", "template_name", type=click.Choice(list(_FLOW_TEMPLATES)))
@click.option("--name", "flow_name")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--force", is_flag=True, help="Overwrite an existing output file")
@click.pass_context
def create_command(
    ctx: click.Context,
    template_name: str | None,
    flow_name: str | None,
    output_path: Path | None,
    force: bool,
) -> None:
    """Create a validated flow from a built-in template."""

    config = _config(ctx)
    selected = template_name or click.prompt(
        "Template", type=click.Choice(list(_FLOW_TEMPLATES)), default="blank"
    )
    name = flow_name or click.prompt("Flow name", default="my-flow")
    if not _FLOW_NAME.fullmatch(name):
        raise click.ClickException(
            "flow name must start with an alphanumeric character and contain only "
            "letters, numbers, or hyphens"
        )
    definition = load_flow(_FLOW_TEMPLATES[selected])
    definition.name = name
    rendered = dump_flow(definition)
    parse_flow(rendered)
    target = output_path or config.flows_dir / f"{name}.agentlane.yml"
    if target.exists() and not force:
        raise click.ClickException(f"flow already exists: {target}; pass --force to overwrite")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    console.print(f"[green]created[/green] {target}")
    console.print(ascii_graph(parse_flow(rendered)))


@flow.command("run")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--non-interactive", is_flag=True)
@click.option("--gate-option", "gate_choices", multiple=True)
@click.option("--ephemeral", is_flag=True, help="Delete terminal run state after output")
@click.option("--output", "output_format", type=click.Choice(["text", "json"]), default="text")
@click.pass_context
def run_command(
    ctx: click.Context,
    path: Path,
    non_interactive: bool,
    gate_choices: tuple[str, ...],
    ephemeral: bool,
    output_format: str,
) -> None:
    config = _config(ctx)
    definition = load_flow(path)
    _apply_flow_defaults(definition, config)
    store = _store(config)
    runner, summary = _runner(
        config, store, gate_choices=gate_choices, non_interactive=non_interactive
    )
    result = asyncio.run(runner.run(definition, original_yaml=path.read_text(encoding="utf-8")))
    _render_result(result, summary, output_format)
    if ephemeral and result.status in {FlowStatus.COMPLETED, FlowStatus.FAILED}:
        store.delete_run(result.run_id)
    _automatic_cleanup(config, store)
    if result.status == FlowStatus.FAILED:
        raise click.exceptions.Exit(1)


@flow.command("list")
@click.option("--status", "status_value", type=click.Choice([item.value for item in FlowStatus]))
@click.option("--flow", "flow_name")
@click.pass_context
def list_command(ctx: click.Context, status_value: str | None, flow_name: str | None) -> None:
    status = FlowStatus(status_value) if status_value else None
    runs = _store(_config(ctx)).list_runs(status=status, flow_name=flow_name)
    table = Table("RUN_ID", "FLOW", "STATUS", "CURRENT", "CREATED")
    for run in runs:
        table.add_row(
            run.run_id,
            run.flow_name,
            run.status.value,
            run.current_step or "-",
            run.created_at.isoformat(timespec="seconds"),
        )
    console.print(table)


@flow.command("status")
@click.argument("run_id", required=False)
@click.pass_context
def status_command(ctx: click.Context, run_id: str | None) -> None:
    store = _store(_config(ctx))
    if run_id is None:
        recent = store.list_runs()
        if not recent:
            raise click.ClickException("no runs found")
        run_id = recent[0].run_id
    run = store.load_run(run_id)
    if run is None:
        raise click.ClickException(f"run not found: {run_id}")
    table = Table("STEP", "STATUS", "RETRIES", "VISITS", "DURATION", "TOKENS", "ERROR")
    for snapshot in run.steps.values():
        table.add_row(
            snapshot.step_id,
            snapshot.status.value,
            str(snapshot.retry_count),
            str(snapshot.visit_count),
            f"{snapshot.duration_ms / 1000:.2f}s",
            str(snapshot.total_tokens) if snapshot.total_tokens is not None else "-",
            snapshot.error or "-",
        )
    console.print(f"{run.flow_name} ({run.run_id}) [{run.status.value}]")
    console.print(table)


@flow.command("prune")
@click.option("--status", "status_value", type=click.Choice([item.value for item in FlowStatus]))
@click.option("--keep", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--older-than-days", type=click.IntRange(min=0))
@click.option("--yes", is_flag=True, help="Confirm deletion")
@click.pass_context
def prune_command(
    ctx: click.Context,
    status_value: str | None,
    keep: int,
    older_than_days: int | None,
    yes: bool,
) -> None:
    if not yes:
        raise click.ClickException("pass --yes to prune run history")
    status = FlowStatus(status_value) if status_value else None
    deleted = _store(_config(ctx)).prune(status=status, keep=keep, older_than_days=older_than_days)
    console.print(f"pruned {deleted} run(s)")


@flow.command("resume")
@click.argument("run_id")
@click.option("--edit-step")
@click.option("--prompt")
@click.option("--non-interactive", is_flag=True)
@click.option("--gate-option", "gate_choices", multiple=True)
@click.option("--output", "output_format", type=click.Choice(["text", "json"]), default="text")
@click.pass_context
def resume_command(
    ctx: click.Context,
    run_id: str,
    edit_step: str | None,
    prompt: str | None,
    non_interactive: bool,
    gate_choices: tuple[str, ...],
    output_format: str,
) -> None:
    config = _config(ctx)
    store = _store(config)
    runner, summary = _runner(
        config, store, gate_choices=gate_choices, non_interactive=non_interactive
    )
    if edit_step:
        if prompt is None:
            raise click.ClickException("--edit-step requires --prompt")
        runner.edit_step(run_id, edit_step, prompt)
    result = asyncio.run(runner.run(run_id=run_id))
    _render_result(result, summary, output_format)
    _automatic_cleanup(config, store)
    if result.status == FlowStatus.FAILED:
        raise click.exceptions.Exit(1)


@flow.command("retry-step")
@click.argument("run_id")
@click.argument("step_id")
@click.pass_context
def retry_step_command(ctx: click.Context, run_id: str, step_id: str) -> None:
    config = _config(ctx)
    store = _store(config)
    runner, summary = _runner(config, store, non_interactive=True)
    runner.retry_step(run_id, step_id)
    result = asyncio.run(runner.run(run_id=run_id, resume_from=step_id))
    _render_result(result, summary, "text")
    _automatic_cleanup(config, store)
    if result.status == FlowStatus.FAILED:
        raise click.exceptions.Exit(1)


@flow.command("cancel")
@click.argument("run_id")
@click.pass_context
def cancel_command(ctx: click.Context, run_id: str) -> None:
    config = _config(ctx)
    store = _store(config)
    runner, _ = _runner(config, store, non_interactive=True)
    result = runner.cancel(run_id)
    console.print(f"run_id={result.run_id} status={result.status.value}")


@flow.command("delete")
@click.argument("run_id")
@click.option("--yes", is_flag=True, help="Confirm deletion")
@click.pass_context
def delete_command(ctx: click.Context, run_id: str, yes: bool) -> None:
    if not yes:
        raise click.ClickException("pass --yes to delete a run")
    store = _store(_config(ctx))
    if store.load_run(run_id) is None:
        raise click.ClickException(f"run not found: {run_id}")
    store.delete_run(run_id)
    console.print(f"deleted {run_id}")


@flow.command("log")
@click.argument("run_id")
@click.pass_context
def log_command(ctx: click.Context, run_id: str) -> None:
    path = _config(ctx).logs_dir / "events.jsonl"
    if not path.exists():
        raise click.ClickException("no logs found")
    found = False
    # Stream line by line instead of loading the whole file, so a long-lived
    # events log does not have to fit in memory.
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            if record.get("run_id") == run_id:
                click.echo(json.dumps(record, ensure_ascii=False))
                found = True
    if not found:
        raise click.ClickException(f"no logs found for run: {run_id}")


@main.group()
def agent() -> None:
    """Inspect configured agent harnesses."""


@agent.command("list")
@click.pass_context
def agent_list(ctx: click.Context) -> None:
    config = _config(ctx)
    specs = load_agent_specs(config)
    commands = agent_commands(config)
    for name, command in sorted(commands.items()):
        spec = specs.get(name)
        display = spec.display_name if spec else "custom"
        click.echo(f"{name}: {display} | {command}")


@agent.command("detect")
@click.pass_context
def agent_detect(ctx: click.Context) -> None:
    config = _config(ctx)
    specs = load_agent_specs(config)
    for name, command in sorted(agent_commands(config).items()):
        parts = shlex.split(command) if isinstance(command, str) else command
        location = shutil.which(parts[0]) if parts else None
        hint = specs[name].install_hint if name in specs and not location else ""
        suffix = f" | {hint}" if hint else ""
        click.echo(f"{name}: {location or 'not found'}{suffix}")


@main.group()
def resolvers() -> None:
    """Inspect built-in prompt resolvers."""


@resolvers.command("list")
def resolvers_list() -> None:
    for prefix in default_registry().prefixes:
        click.echo(prefix)
    click.echo("<group>.steps")


@main.command("quickstart")
@click.pass_context
def quickstart(ctx: click.Context) -> None:
    config = _config(ctx)
    config.flows_dir.mkdir(parents=True, exist_ok=True)
    example = config.flows_dir / "cross-review.agentlane.yml"
    if not example.exists():
        example.write_text(
            _FLOW_TEMPLATES["cross-review"].read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    load_flow(example)
    console.print(f"AgentLane home: {config.home}")
    console.print(f"Example flow: {example}")
    console.print("1. Check harnesses: agentlane agent detect")
    console.print(f"2. Validate: agentlane flow validate {example}")
    console.print(f"3. Run: agentlane flow run {example}")


def entrypoint() -> None:
    main(standalone_mode=True)
