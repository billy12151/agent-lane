from __future__ import annotations

import pytest

from agentlane.core.engine import FlowEngine, dump_flow, parse_flow
from agentlane.core.errors import FlowValidationError


def test_parse_complete_flow(linear_flow):
    assert linear_flow.name == "linear"
    assert linear_flow.defaults_timeout == 10
    assert linear_flow.defaults_retry == 1
    assert linear_flow.step("second").depends_on == ["first"]


def test_yaml_block_prompt_preserves_lines():
    flow = parse_flow("""name: block
steps:
  - id: a
    agent: x
    prompt: |
      first line
      second: line
""")
    assert flow.step("a").prompt == "first line\nsecond: line\n"


def test_v31_gate_options_do_not_require_id():
    flow = parse_flow("""name: gate
steps:
  - id: approval
    type: human_gate
    options:
      - label: approve
        action: next_step
      - label: stop
        action: terminate
""")
    assert [option.label for option in flow.step("approval").options] == ["approve", "stop"]


@pytest.mark.parametrize(
    "yaml_text,message",
    [
        ("[]", "flow root"),
        ("name: x\nsteps: {}", "steps must be a list"),
        ("name: x\nsteps:\n  - x", "must be a mapping"),
        ("name: x\ndefaults: []\nsteps: []", "defaults must be a mapping"),
        ("name: x\nsecrets:\n  required: x\nsteps: []", "secrets.required"),
        ("name: x\ndefaults:\n  retry: no\nsteps: []", "defaults.retry"),
        ("name: x\ndefaults:\n  fail_fast: 'false'\nsteps: []", "fail_fast"),
        ("name: x\nmemory: []\nsteps: []", "memory must be a mapping"),
        ("name: x\nversion: true\nsteps: []", "version must be an integer"),
        ("name: x\ndefaults:\n  timeout: slow\nsteps: []", "defaults.timeout"),
        ("name: x\nsteps:\n  - id: a\n    agent: x\n    output: []", "output must be a mapping"),
        (
            "name: x\nsteps:\n  - id: a\n    agent: x\n    output:\n      schema: []",
            "output.schema",
        ),
        (
            "name: x\nsteps:\n  - id: a\n    agent: x\n    depends_on: a",
            "depends_on must be a list",
        ),
        (
            "name: x\nsteps:\n  - id: a\n    type: human_gate\n    options: {}",
            "options must be a list",
        ),
        (
            "name: x\nsteps:\n  - id: a\n    type: human_gate\n    options: [bad]",
            r"options\[0\] must be a mapping",
        ),
        ("name: x\nunknown: true\nsteps: []", "flow has unknown fields"),
        ("name: x\ndefaults: {retries: 2}\nsteps: []", "defaults has unknown fields"),
        ("name: x\nsteps:\n  - {id: a, agent: x, depend_on: []}", "unknown fields"),
        ("name: x\nsteps:\n  - {id: 1, agent: x}", "id must be a string"),
        ("name: x\nsteps:\n  - {id: a, agent: x, terminal: 'false'}", "terminal must be a boolean"),
        ("name: x\nsteps:\n  - {id: a, agent: x, depends_on: [1]}", "entries must be strings"),
        ("name: x\nsteps:\n  - {id: a, agent: x, output: {format: 1}}", "format must be a string"),
        ("name: [\n", "invalid YAML"),
    ],
)
def test_parse_shape_errors(yaml_text, message):
    with pytest.raises(FlowValidationError, match=message):
        FlowEngine().parse(yaml_text)


@pytest.mark.parametrize(
    "yaml_text,message",
    [
        ("name: ''\nsteps:\n  - id: a\n    agent: x", "flow name"),
        ("name: x\nsteps: []", "at least one"),
        ("name: x\nsteps:\n  - id: a\n    agent: x\n  - id: a\n    agent: x", "unique"),
        ("name: x\nsteps:\n  - id: 'bad id'\n    agent: x", "invalid step id"),
        (
            "name: x\nsteps:\n  - id: a\n    agent: x\n    depends_on: [missing]",
            "unknown dependencies",
        ),
        ("name: x\nsteps:\n  - id: a\n    agent: x\n    depends_on: [a]", "depend on itself"),
        ("name: x\nsteps:\n  - id: a\n    prompt: x", "agent is required"),
        ("name: x\nsteps:\n  - id: a\n    type: other", "unsupported step type"),
        ("name: x\nsteps:\n  - id: a\n    type: human_gate", "requires options"),
        ("name: x\nsteps:\n  - id: a\n    agent: x\n    retry: -1", "retry must"),
        ("name: x\nsteps:\n  - id: a\n    agent: x\n    timeout: 0", "timeout must"),
        ("name: x\nsteps:\n  - id: a\n    agent: x\n    max_visits: 0", "max_visits"),
        ("name: x\ndefaults:\n  retry: -1\nsteps:\n  - id: a\n    agent: x", "defaults.retry"),
        ("name: x\ndefaults:\n  timeout: 0\nsteps:\n  - id: a\n    agent: x", "defaults.timeout"),
        (
            "name: x\ndefaults:\n  max_visits: 0\nsteps:\n  - id: a\n    agent: x",
            "defaults.max_visits",
        ),
        ("name: x\nsteps:\n  - agent: x", "requires id"),
        (
            "name: x\nsteps:\n  - id: gate\n    type: human_gate\n"
            "    options:\n      - {label: '', action: next_step}",
            "option label",
        ),
        (
            "name: x\nsteps:\n  - id: gate\n    type: human_gate\n"
            "    options:\n      - {label: 'yes', action: next_step}\n"
            "      - {label: 'yes', action: terminate}",
            "labels must be unique",
        ),
        (
            "name: x\nsteps:\n  - id: gate\n    type: human_gate\n"
            "    options:\n      - {label: 'yes', action: explode}",
            "unsupported gate action",
        ),
        (
            "name: x\nsteps:\n  - id: gate\n    type: human_gate\n"
            "    options:\n      - {label: 'yes', action: goto_step, target: missing}",
            "unknown gate target",
        ),
        (
            "name: x\nsteps:\n  - id: a\n    agent: x\n    output: {format: xml}",
            "unsupported output format",
        ),
        (
            "name: x\nsteps:\n  - id: a\n    agent: x\n"
            "    output: {format: text, schema: {value: string}}",
            "schema is only valid",
        ),
        (
            "name: x\nsteps:\n  - id: a\n    agent: x\n"
            "    output: {format: json, schema: {value: decimal}}",
            "unsupported schema type",
        ),
        ("name: x\nversion: 0\nsteps:\n  - {id: a, agent: x}", "version must be at least"),
        (
            "name: x\nsecrets: {required: [TOKEN, TOKEN]}\nsteps:\n  - {id: a, agent: x}",
            "must be unique",
        ),
        (
            "name: x\nsteps:\n  - {id: a, agent: x, depends_on: [b, b]}\n  - {id: b, agent: x}",
            "dependencies must be unique",
        ),
        ("name: x\nsteps:\n  - {id: a, agent: x, group: ''}", "group cannot be empty"),
    ],
)
def test_definition_validation_errors(yaml_text, message):
    with pytest.raises(FlowValidationError, match=message):
        parse_flow(yaml_text)


def test_cycle_detection():
    with pytest.raises(FlowValidationError, match="cycle"):
        parse_flow("""name: cycle
steps:
  - id: a
    agent: x
    depends_on: [b]
  - id: b
    agent: x
    depends_on: [a]
""")


def test_layered_order_is_stable():
    flow = parse_flow("""name: dag
steps:
  - id: a
    agent: x
  - id: b
    agent: x
    depends_on: [a]
  - id: c
    agent: x
    depends_on: [a]
  - id: d
    agent: x
    depends_on: [b, c]
""")
    assert FlowEngine().layered_order(flow) == [["a"], ["b", "c"], ["d"]]


def test_ancestor_and_descendant_analysis():
    flow = parse_flow("""name: dag
steps:
  - id: a
    agent: x
  - id: b
    agent: x
    depends_on: [a]
  - id: c
    agent: x
    depends_on: [b]
""")
    engine = FlowEngine()
    assert engine.ancestors(flow)["c"] == {"a", "b"}
    assert engine.descendants(flow, "a") == {"b", "c"}
    with pytest.raises(KeyError):
        engine.descendants(flow, "missing")


def test_reference_must_target_upstream_step():
    with pytest.raises(FlowValidationError, match="not an upstream"):
        parse_flow("""name: refs
steps:
  - id: a
    agent: x
    prompt: "{steps:b}"
  - id: b
    agent: x
""")


def test_grouped_reference_requires_group_prefix():
    with pytest.raises(FlowValidationError, match="requires review.steps"):
        parse_flow("""name: groups
steps:
  - id: a
    agent: x
    group: review
  - id: b
    agent: x
    prompt: "{steps:a}"
    depends_on: [a]
""")


def test_grouped_reference_with_prefix_is_valid():
    flow = parse_flow("""name: groups
steps:
  - id: a
    agent: x
    group: review
  - id: b
    agent: x
    prompt: "{review.steps:a}"
    depends_on: [a]
""")
    assert flow.step("a").group == "review"


def test_grouped_reference_must_name_the_actual_group():
    with pytest.raises(FlowValidationError, match="not in group other"):
        parse_flow("""name: groups
steps:
  - id: a
    agent: x
    group: review
  - id: b
    agent: x
    prompt: "{other.steps:a}"
    depends_on: [a]
""")


def test_reference_to_unknown_step_is_rejected():
    with pytest.raises(FlowValidationError, match="targets unknown step"):
        parse_flow("""name: refs
steps:
  - id: a
    agent: x
    prompt: "{steps:missing}"
""")


def test_memory_step_alias_is_validated_as_upstream():
    with pytest.raises(FlowValidationError, match="not an upstream"):
        parse_flow("""name: refs
steps:
  - id: a
    agent: x
    prompt: "{memory:get:b}"
  - id: b
    agent: x
""")


@pytest.mark.parametrize(
    "prompt,message",
    [
        ("{unknown:value}", "unknown resolver prefix"),
        ("{malformed}", "no resolver prefix"),
        ("{env:}", "reference key cannot be empty"),
    ],
)
def test_resolver_registry_validation(prompt, message):
    from agentlane.core.resolvers import default_registry

    flow = parse_flow(f'name: refs\nsteps:\n  - id: a\n    agent: x\n    prompt: "{prompt}"\n')
    errors = FlowEngine().validate_resolvers(flow, default_registry())
    assert any(message in error for error in errors)


def test_dump_round_trip(linear_flow):
    restored = parse_flow(dump_flow(linear_flow))
    assert restored.name == linear_flow.name
    assert [step.id for step in restored.steps] == ["first", "second"]
    assert restored.step("second").prompt == linear_flow.step("second").prompt
