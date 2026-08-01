# Changelog

All notable changes to AgentLane will be documented here. The format follows Keep a Changelog and
the project uses semantic versioning once the public API stabilizes.

## [Unreleased]

### Added

- Built-in `cross-review-trio` flow template: one harness drafts, two *different* harnesses review
  the same draft in parallel without seeing each other, and a third pass synthesizes consensus,
  divergence, and blind spots — the core "complementary viewpoints" pattern.
- Autonomous-execution flags for the built-in codex, claude-code, and gemini-cli agent specs, so
  each harness can actually read files and run commands inside a non-interactive flow instead of
  collapsing into plain prompt routing.

### Fixed

- `shell.py`: `os.getpgid` / `os.killpg` are POSIX-only and raised `AttributeError` on Windows in
  the timeout / cancel path. Replaced with module-level capability detection; `start_new_session`
  and signal logic are now gated on it.
- `jsonfile.py`: `run_lease` created a per-run lock file per resume but never removed it. Lock files
  are now unlinked on release. Also fixed a missing `suppress` import that surfaced when the lease
  cleanup ran.
- `async_utils.py`: the bounded daemon-thread pool was hidden module-level global state, contradicting
  the dependency-injection principle in the architecture doc. Extracted into an injectable `WorkerPool`;
  `StepRunner` and `ResolverRegistry` accept one and fall back to a process-wide default.
- `runner.py`: contract-violation retries previously resent the identical prompt, so the agent would
  most likely reproduce the same broken answer. The violation text is now appended to the next
  attempt's prompt so the agent has the information needed to fix its output.

### Changed

- User-facing documentation (README, the two `docs/` references, and built-in flow/agent template
  descriptions) rewritten to Chinese-first for the project's first user. Developer-facing files
  (this changelog, CONTRIBUTING) remain in English.

## [0.1.0a1] - 2026-07-30

### Added

- Strict V3.1 YAML flow model, graph validation, stable concurrent layers, and output contracts.
- Shell, static-test, and injected ACP adapters behind one routing boundary.
- Step, group-step, environment, secret, and memory resolvers with bounded execution.
- Human gates, bounded jumps, immutable decisions, resume, retry, prompt edit, and cancellation.
- Atomic JSON, in-memory, and injected TaskFlow state stores.
- CLI creation, execution, inspection, visualization, recovery, logging, and cleanup commands.
- JSONL observability, run summaries, lifecycle hooks, duration metrics, and token accounting.
- Built-in agent specifications and blank, cross-review, and code/test flow templates.
- Python 3.10-3.12 CI, strict lint/type checks, package validation, and a 90% coverage gate.
