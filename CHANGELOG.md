# Changelog

All notable changes to AgentLane will be documented here. The format follows Keep a Changelog and
the project uses semantic versioning once the public API stabilizes.

## [0.1.0a1] - 2026-07-30

### Added

- Strict V3.1 YAML flow model, graph validation, stable concurrent layers, and output contracts.
- Shell, static-test, and injected ACP adapters behind one routing boundary.
- Step, group-step, environment, secret, and memory resolvers with bounded execution.
- Human gates, bounded jumps, immutable decisions, resume, retry, prompt edit, and cancellation.
- Atomic JSON, in-memory, and injected TaskFlow state stores.
- CLI creation, execution, inspection, visualization, recovery, logging, and cleanup commands.
- JSONL observability, summaries, lifecycle hooks, duration metrics, and token accounting.
- Built-in agent specifications and blank, cross-review, and code/test flow templates.
- Python 3.10-3.12 CI, strict lint/type checks, package validation, and a 90% coverage gate.
