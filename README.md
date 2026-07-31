# AgentLane

AgentLane is a declarative workflow runner for coordinating autonomous CLI agents. A YAML file
defines a dependency graph; AgentLane validates it, runs independent steps concurrently, resolves
upstream context, enforces output contracts, pauses at human gates, and persists enough state to
recover without silently changing the original flow.

> Status: `0.1.0a1` is an alpha release. The flow model and standalone execution path are usable;
> compatibility can still change before `1.0`.

## What is implemented

- Strict YAML flow validation with unknown-field, type, graph, reference, and cycle checks.
- Real layer-level concurrency, configurable retries and timeouts, and optional fail-fast cleanup.
- One adapter boundary for agent routing, with shell, static-test, and injected ACP adapters.
- Step, environment, secret, and optional memory resolvers.
- Text, Markdown, and structured JSON output contracts.
- Human gates with `next_step`, bounded `goto_step`, and `terminate` decisions.
- Atomic JSON run persistence, immutable flow snapshots, resume, retry, prompt edit, and pruning.
- JSONL events, run summaries, duration/token metrics, hooks, and ASCII/Mermaid visualization.
- Injected TaskFlow and ACP seams for an OpenClaw host integration.

## Install from this repository

AgentLane requires Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
agentlane --version
```

For development and release checks:

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy agentlane
pytest --cov=agentlane
```

## Quick start

Create the user directories and a validated example flow:

```bash
agentlane quickstart
agentlane agent detect
```

AgentLane includes specifications for `codex`, `claude-code`, and `gemini-cli`. Detection reports
whether each executable is available; it does not install or authenticate third-party tools.

Create or validate a flow:

```bash
agentlane flow create --template cross-review --name my-review
agentlane flow validate ~/.agentlane/flows/my-review.agentlane.yml
agentlane flow visualize ~/.agentlane/flows/my-review.agentlane.yml
```

Built-in templates: `blank`, `cross-review` (extract → review), `cross-review-trio`
(one harness drafts, **two different harnesses review the same draft in parallel**, a
third pass synthesizes consensus / divergence / blind spots — the core "complementary
viewpoints" pattern), and `codegen-test` (implement → review tests).

Run it and inspect the durable record:

```bash
agentlane flow run ~/.agentlane/flows/my-review.agentlane.yml
agentlane flow list
agentlane flow status
```

## Flow example

```yaml
name: implementation-review
version: 1
defaults:
  timeout: 300
  retry: 1
  max_visits: 3
  fail_fast: false

steps:
  - id: draft
    agent: codex
    prompt: Produce a JSON implementation plan.
    output:
      format: json
      schema:
        plan: string

  - id: architecture-review
    agent: claude-code
    prompt: |
      Review this plan from an architecture perspective:
      {steps:draft.plan}
    depends_on: [draft]
    output:
      format: markdown

  - id: risk-review
    agent: gemini-cli
    prompt: |
      Review this plan for operational risks:
      {steps:draft.plan}
    depends_on: [draft]
    output:
      format: markdown

  - id: approval
    type: human_gate
    message: Continue after both independent reviews?
    depends_on: [architecture-review, risk-review]
    options:
      - label: approve
        action: next_step
      - label: stop
        action: terminate

  - id: implement
    agent: codex
    prompt: Implement the approved plan.
    depends_on: [approval]
```

`architecture-review` and `risk-review` run concurrently because they are in the same dependency
layer. A step may only reference completed upstream dependencies. Structured field access such as
`{steps:draft.plan}` requires a JSON output contract.

The complete format and resolver rules are in [the flow reference](docs/flow-format.md). A JSON
Schema is packaged at `agentlane/schema/flow-schema.json` for editor integration.

## Agent configuration

The default configuration file is `~/.agentlane/config.yml`; set `AGENTLANE_HOME` to relocate all
default AgentLane data or pass `--config PATH` for one command. A full example is available at
[`examples/config.example.yml`](examples/config.example.yml).

```yaml
agents:
  commands:
    codex: [codex, exec, -]
    local-reviewer: [python, /absolute/path/reviewer.py]
```

Prompts are sent to the configured process on standard input. Commands use direct process
execution, not a shell; shell expansion and pipelines are therefore not implicit. User agent specs
can also be placed in `~/.agentlane/agents/*.agent.yml` and override built-in IDs.

### Autonomous execution flags

The built-in agent specs ship with the autonomous-execution flags each harness needs to be useful
inside a non-interactive flow:

| harness | flags | why |
| --- | --- | --- |
| `codex` | `--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check` | skip approval prompts; allow running outside a git repo; prompt via stdin (`-`) |
| `claude-code` | `--dangerously-skip-permissions` | bypass per-tool approval so the agent can read files / run Bash autonomously |
| `gemini-cli` | `--yolo --skip-trust -p ""` | auto-approve all tool calls; skip the workspace-trust prompt; read prompt from stdin |

Without these flags the agent can only react to the literal prompt text — it cannot read your files
or run commands on its own, which collapses the flow into plain prompt routing. **These flags grant
the agent full, unsandboxed control over the workspace.** Only run flows against directories you
trust, and never pass untrusted input directly into a flow prompt. If a flag is rejected by your
installed CLI version, override the command in `~/.agentlane/config.yml` (see
`examples/config.example.yml`).

## Gates and recovery

Interactive execution prompts at a human gate. In automation, `--non-interactive` persists the run
as `paused` instead of guessing a choice:

```bash
agentlane flow run flow.yml --non-interactive
agentlane flow resume RUN_ID --gate-option approval=approve
```

Recovery commands operate on the persisted flow snapshot:

```bash
agentlane flow retry-step RUN_ID STEP_ID
agentlane flow resume RUN_ID --edit-step STEP_ID --prompt "replacement prompt"
agentlane flow cancel RUN_ID
agentlane flow log RUN_ID
agentlane flow delete RUN_ID --yes
```

Retrying or editing a step resets that step and all of its descendants, while preserving completed
upstream evidence. `goto_step` can intentionally revisit work, but `max_visits` bounds every step
and gate so a flow cannot loop forever. `terminate` records an operator decision and ends the run as
`cancelled`.

By default, state is stored in `~/.agentlane/runs.json` using an inter-process lock and atomic file
replacement. Event logs are appended to `~/.agentlane/logs/events.jsonl`. Agent output is durable
data and may contain sensitive information; protect those files accordingly.

## Resolver behavior

- `{steps:step-id}` reads a completed, ungrouped upstream output.
- `{steps:step-id.field}` reads a field from an upstream JSON result.
- `{group.steps:step-id}` is required for a step assigned to `group: group`.
- `{env:NAME}` reads an environment variable. Missing values become empty text and emit an event.
- `{secret:NAME}` reads the configured secret provider. Missing secrets fail the step closed.
- `{memory:query}`, `{memory:ID}`, and `{memory:get:step-id}` use an explicitly enabled
  memory-arbiter client. Missing memory is observable but non-fatal.

`memory.workspace` is metadata forwarded to memory-arbiter; with memory-arbiter `0.7.4+` it is not
a search isolation boundary. Do not use it as an authorization or tenancy control.

## Python API

```python
import asyncio

from agentlane import StepRunner, parse_flow
from agentlane.adapters import StaticAgentAdapter

flow = parse_flow("""
name: example
steps:
  - id: draft
    agent: demo
    prompt: Write a draft.
""")

runner = StepRunner(adapter=StaticAgentAdapter({"demo": "done"}))
run = asyncio.run(runner.run(flow))
assert run.steps["draft"].output == "done"
```

Production hosts inject an `AgentAdapter`, `StateStore`, resolver registry, gate driver, and optional
hooks/sinks. AgentLane deliberately has no global adapter registry: routing ownership stays in the
single adapter selected by the host. See [the architecture notes](docs/architecture.md).

## Explicit alpha boundaries

The current release does not include a remote template marketplace, a full-screen monitoring UI,
or a standalone generic ACP daemon. `ACPAgentAdapter` and `TaskFlowStateStore` are dependency-
injection seams for an owning runtime such as OpenClaw. The built-in cross-review flow uses
standalone-capable harnesses; `zcode` is not advertised as one.

## License

Apache License 2.0. See [LICENSE](LICENSE).
