# Contributing

AgentLane is currently alpha software. Keep changes small, preserve strict validation and durable
recovery behavior, and add tests for both the successful path and the persisted failure state.

## Local setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Required checks

```bash
ruff format --check .
ruff check .
mypy agentlane
pytest --cov=agentlane --cov-report=term-missing
python -m build
python -m twine check dist/*
```

Coverage must remain at or above 90%, with branch coverage enabled. Tests should verify observable
state rather than only return values: step status, error text, retry/visit counts, snapshots, and
cleanup behavior matter to users recovering a failed run.

## Architecture rules

- Keep canonical implementation under `agentlane/core`; root modules are compatibility exports.
- Keep one `AgentAdapter` routing owner. Do not introduce a second adapter registry.
- Keep standalone persistence JSON-based unless a separately approved migration changes the design.
- Treat resolver, hook, sink, memory, ACP, and TaskFlow integrations as bounded injected seams.
- Do not weaken unknown-field, graph, secret, snapshot, or visit-limit validation for convenience.
- Update the flow schema, parser tests, reference docs, and changelog together when syntax changes.
