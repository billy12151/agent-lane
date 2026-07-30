# Flow format reference

AgentLane reads YAML with `yaml.safe_load` and rejects unknown fields. The packaged JSON Schema is
`agentlane/schema/flow-schema.json`; runtime validation additionally checks graph and reference
semantics that JSON Schema cannot express conveniently.

## Top-level fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Non-empty flow name. |
| `version` | no | Positive integer; defaults to `1`. |
| `description` | no | Human-readable text. |
| `defaults` | no | `timeout`, `retry`, `max_visits`, and `fail_fast`. |
| `memory.workspace` | no | Memory metadata; defaults to `default`. It is not an isolation boundary. |
| `secrets.required` | no | Unique environment/provider secret names checked before run creation. |
| `steps` | yes | Non-empty list of agent or human-gate steps. |

Defaults are `timeout: 300`, `retry: 1`, `max_visits: 3`, and `fail_fast: false`.

## Agent steps

```yaml
- id: review
  type: agent
  agent: claude-code
  prompt: "Review {steps:draft}"
  depends_on: [draft]
  timeout: 120
  retry: 2
  max_visits: 3
  group: reviewers
  output:
    format: json
    schema:
      verdict: string
      score: number
  terminal: false
```

`id` must match `^[A-Za-z0-9][A-Za-z0-9._-]*$` and be unique. `agent` is required for an agent step.
Dependencies must exist, be unique, and form an acyclic graph. `group` creates a resolver namespace;
it does not change scheduling. `terminal` is retained as flow metadata in this alpha and does not
override dependency execution.

`output.format` accepts `text`, `markdown`, or `json`. Markdown must be non-empty. JSON must decode
to an object. A schema is a required-field map whose types may be `string`, `integer`, `number`,
`boolean`, `object`, `array`, or `null`; additional fields remain allowed.

## Human gates

```yaml
- id: approval
  type: human_gate
  message: Ship this result?
  depends_on: [review]
  max_visits: 2
  options:
    - label: approve
      action: next_step
    - label: revise
      action: goto_step
      target: draft
    - label: stop
      action: terminate
```

Option labels must be non-empty and unique. The option contract is exactly `label`, `action`, and
optional `target`; there is no option `id`. `goto_step` requires an existing target. A jump resets
the target and all descendants before topology execution restarts. `max_visits` applies to gates as
well as agent steps.

If multiple gates share one parallel layer, multiple pause decisions are allowed. A non-pause
decision must be the only control decision in that layer; conflicting jump/terminate decisions fail
visibly.

## References

References use `{prefix:key}` syntax and are resolved concurrently before the adapter is called.

| Syntax | Behavior when missing |
| --- | --- |
| `{steps:draft}` | Empty text plus `resolver_missing` event. Target must be an ungrouped ancestor. |
| `{steps:draft.field}` | Same; requires parsed JSON and follows nested object keys. |
| `{team.steps:draft}` | Same; target must have `group: team`. |
| `{env:NAME}` | Empty text plus event. |
| `{secret:NAME}` | Step fails closed before agent execution. |
| `{memory:query}` | Empty text plus event if memory is disabled/missing. |
| `{memory:123}` | Reads an exact memory ID and follows bounded supersession links. |
| `{memory:get:draft}` | Reads the memory ID recorded after an upstream step write. |

A referenced step must be an upstream dependency, directly or transitively. Unknown resolver
prefixes and malformed keys are rejected before run creation. Resolver calls have a bounded timeout.

## Execution and recovery semantics

The graph is partitioned into stable dependency layers. Steps in one layer execute concurrently.
With `fail_fast: false`, AgentLane waits for all siblings and then fails the run if any failed. With
`fail_fast: true`, the first raised step failure cancels unfinished siblings and awaits their cleanup.

`retry` counts retries after the first attempt. `max_visits` counts topology visits across jumps and
recovery. A flow snapshot, step statuses, outputs, errors, timings, token counts, gate decisions, and
runtime context are persisted after each transition.

Non-interactive gates pause. Resuming reloads the persisted YAML snapshot; supplying a semantically
different flow is rejected. Explicit retry or prompt edit resets the selected step and descendants.
