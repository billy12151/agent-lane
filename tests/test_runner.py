from __future__ import annotations

import asyncio
import time

import pytest

from agentlane.core.adapters import StaticAgentAdapter
from agentlane.core.engine import parse_flow
from agentlane.core.errors import FlowExecutionError, FlowValidationError, InvalidResumeError
from agentlane.core.hooks import FlowHook
from agentlane.core.human_gate import GateDriver, PresetGateDriver
from agentlane.core.observability import ObservabilitySink
from agentlane.core.result import AgentResult
from agentlane.core.runner import StepRunner
from agentlane.core.state import FlowStatus, GateOption, StepStatus
from agentlane.core.state_store import InMemoryStateStore


class RecordingSink(ObservabilitySink):
    def __init__(self):
        self.events = []

    def on_flow_start(self, *args):
        self.events.append(("flow_start", *args))

    def on_flow_end(self, *args):
        self.events.append(("flow_end", *args))

    def on_step_start(self, *args):
        self.events.append(("step_start", *args))

    def on_step_end(self, *args):
        self.events.append(("step_end", *args))

    def on_error(self, *args):
        self.events.append(("error", *args))

    def on_resolver_missing(self, *args):
        self.events.append(("resolver_missing", *args))

    def on_memory_write_failed(self, *args):
        self.events.append(("memory_failed", *args))


class RecordingHook(FlowHook):
    def __init__(self):
        self.events = []

    async def before_step(self, run_id, step, run):
        self.events.append(("before", step.id))

    async def after_step(self, run_id, step, result):
        self.events.append(("after", step.id))

    async def on_error(self, run_id, step, error):
        self.events.append(("error", step.id))

    async def on_gate_decision(self, run_id, step, decision):
        self.events.append(("gate", step.id, decision.action))


def test_runner_requires_explicit_adapter():
    with pytest.raises(ValueError, match="AgentAdapter"):
        StepRunner(adapter=None)


@pytest.mark.asyncio
async def test_linear_happy_path_resolves_previous_output(linear_flow, linear_yaml):
    adapter = StaticAgentAdapter({"alpha": "draft", "beta": "reviewed"})
    run = await StepRunner(adapter=adapter).run(linear_flow, original_yaml=linear_yaml)
    assert run.status == FlowStatus.COMPLETED
    assert adapter.calls == [("alpha", "create"), ("beta", "review draft")]
    assert run.flow_yaml_snapshot == linear_yaml


@pytest.mark.asyncio
async def test_retry_succeeds_without_incrementing_visit_count():
    flow = parse_flow("""name: retry
defaults: {retry: 1}
steps:
  - id: a
    agent: x
    prompt: go
""")
    adapter = StaticAgentAdapter(
        {"x": [AgentResult.failure("temporary"), AgentResult.success("ok")]}
    )
    run = await StepRunner(adapter=adapter).run(flow)
    snapshot = run.steps["a"]
    assert run.status == FlowStatus.COMPLETED
    assert snapshot.retry_count == 1 and snapshot.visit_count == 1
    assert len(adapter.calls) == 2


@pytest.mark.asyncio
async def test_retry_exhaustion_persists_failure():
    flow = parse_flow("""name: retry
defaults: {retry: 1}
steps:
  - id: a
    agent: x
    prompt: go
""")
    adapter = StaticAgentAdapter({"x": AgentResult.failure("offline")})
    run = await StepRunner(adapter=adapter).run(flow)
    assert run.status == FlowStatus.FAILED
    assert run.steps["a"].status == StepStatus.FAILED
    assert run.steps["a"].retry_count == 1
    assert run.context["__flow_error"] == "offline"


@pytest.mark.asyncio
async def test_contract_failure_does_not_retry():
    flow = parse_flow("""name: contract
defaults: {retry: 3}
steps:
  - id: a
    agent: x
    prompt: go
    output:
      format: json
      schema: {name: string}
""")
    adapter = StaticAgentAdapter({"x": "not-json"})
    run = await StepRunner(adapter=adapter).run(flow)
    assert run.status == FlowStatus.FAILED
    assert len(adapter.calls) == 1
    assert "valid JSON" in run.steps["a"].error


@pytest.mark.asyncio
async def test_adapter_exception_is_normalized_and_step_not_left_running():
    async def explode(agent, prompt):
        raise RuntimeError("transport exploded")

    flow = parse_flow("""name: failure
defaults: {retry: 0}
steps:
  - id: a
    agent: x
    prompt: go
""")
    run = await StepRunner(adapter=StaticAgentAdapter({"x": explode})).run(flow)
    assert run.status == FlowStatus.FAILED
    assert run.steps["a"].status == StepStatus.FAILED
    assert "transport exploded" in run.steps["a"].error


@pytest.mark.asyncio
async def test_non_fail_fast_waits_for_all_tasks_before_returning():
    finished = asyncio.Event()

    async def fail(agent, prompt):
        await asyncio.sleep(0.01)
        return AgentResult.failure("bad")

    async def slow(agent, prompt):
        await asyncio.sleep(0.05)
        finished.set()
        return "done"

    flow = parse_flow("""name: parallel
defaults: {retry: 0, fail_fast: false}
steps:
  - id: bad
    agent: bad
    prompt: x
  - id: slow
    agent: slow
    prompt: x
""")
    adapter = StaticAgentAdapter({"bad": fail, "slow": slow})
    run = await StepRunner(adapter=adapter).run(flow)
    assert finished.is_set()
    assert run.steps["slow"].status == StepStatus.COMPLETED
    assert run.status == FlowStatus.FAILED


@pytest.mark.asyncio
async def test_fail_fast_cancels_and_cleans_pending_steps():
    cancelled = asyncio.Event()

    async def fail(agent, prompt):
        await asyncio.sleep(0.01)
        return AgentResult.failure("bad")

    async def slow(agent, prompt):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    flow = parse_flow("""name: parallel
defaults: {retry: 0, fail_fast: true}
steps:
  - id: bad
    agent: bad
    prompt: x
  - id: slow
    agent: slow
    prompt: x
""")
    run = await StepRunner(adapter=StaticAgentAdapter({"bad": fail, "slow": slow})).run(flow)
    assert cancelled.is_set()
    assert run.status == FlowStatus.FAILED
    assert run.steps["slow"].status == StepStatus.FAILED
    assert "fail_fast" in run.steps["slow"].error


@pytest.mark.asyncio
async def test_parallel_execution_is_real():
    async def wait(agent, prompt):
        await asyncio.sleep(0.05)
        return agent

    flow = parse_flow("""name: parallel
steps:
  - id: a
    agent: a
    prompt: x
  - id: b
    agent: b
    prompt: x
""")
    started = time.monotonic()
    run = await StepRunner(adapter=StaticAgentAdapter({"a": wait, "b": wait})).run(flow)
    elapsed = time.monotonic() - started
    assert run.status == FlowStatus.COMPLETED
    assert elapsed < 0.09


@pytest.mark.asyncio
async def test_layer_barrier_blocks_downstream_until_both_complete():
    events = []

    async def record(agent, prompt):
        events.append(f"start-{agent}")
        await asyncio.sleep(0.02 if agent != "c" else 0)
        events.append(f"end-{agent}")
        return agent

    flow = parse_flow("""name: barrier
steps:
  - id: a
    agent: a
    prompt: x
  - id: b
    agent: b
    prompt: x
  - id: c
    agent: c
    prompt: x
    depends_on: [a, b]
""")
    await StepRunner(adapter=StaticAgentAdapter({"a": record, "b": record, "c": record})).run(flow)
    assert events.index("start-c") > events.index("end-a")
    assert events.index("start-c") > events.index("end-b")


@pytest.mark.asyncio
async def test_parallel_gate_waits_for_agent_then_pauses():
    completed = asyncio.Event()

    async def slow(agent, prompt):
        await asyncio.sleep(0.03)
        completed.set()
        return "done"

    flow = parse_flow("""name: gate
steps:
  - id: gate
    type: human_gate
    options:
      - {label: approve, action: next_step}
  - id: slow
    agent: x
    prompt: x
""")
    run = await StepRunner(adapter=StaticAgentAdapter({"x": slow})).run(flow)
    assert completed.is_set()
    assert run.status == FlowStatus.PAUSED
    assert run.steps["slow"].status == StepStatus.COMPLETED
    assert run.steps["gate"].status == StepStatus.WAITING_HUMAN


@pytest.mark.asyncio
async def test_gate_decision_is_persisted_and_observed():
    flow = parse_flow("""name: gate
steps:
  - id: gate
    type: human_gate
    options:
      - {label: approve, action: next_step}
""")
    hook = RecordingHook()
    run = await StepRunner(
        adapter=StaticAgentAdapter({}),
        gate_driver=PresetGateDriver({"gate": "approve"}),
        hook=hook,
    ).run(flow)
    assert run.status == FlowStatus.COMPLETED
    assert run.gate_decisions[0].label == "approve"
    assert ("gate", "gate", "next_step") in hook.events


@pytest.mark.asyncio
async def test_gate_terminate_cancels_flow():
    flow = parse_flow("""name: gate
steps:
  - id: gate
    type: human_gate
    options:
      - {label: stop, action: terminate}
""")
    run = await StepRunner(
        adapter=StaticAgentAdapter({}),
        gate_driver=PresetGateDriver({"gate": "stop"}),
    ).run(flow)
    assert run.status == FlowStatus.CANCELLED


@pytest.mark.asyncio
async def test_gate_goto_uses_loop_not_recursion_and_honors_max_visits():
    flow = parse_flow("""name: loop
defaults: {max_visits: 2}
steps:
  - id: work
    agent: x
    prompt: x
  - id: gate
    type: human_gate
    depends_on: [work]
    options:
      - {label: again, action: goto_step, target: work}
""")
    run = await StepRunner(
        adapter=StaticAgentAdapter({"x": "done"}),
        gate_driver=PresetGateDriver({"gate": "again"}),
    ).run(flow)
    assert run.status == FlowStatus.FAILED
    assert run.steps["work"].visit_count == 2
    assert "visit limit" in run.steps["work"].error


@pytest.mark.asyncio
async def test_resume_rebuilds_flow_from_snapshot():
    flow = parse_flow("""name: gate
steps:
  - id: gate
    type: human_gate
    options:
      - {label: approve, action: next_step}
  - id: after
    agent: x
    prompt: done
    depends_on: [gate]
""")
    store = InMemoryStateStore()
    paused = await StepRunner(adapter=StaticAgentAdapter({"x": "ok"}), store=store).run(flow)
    assert paused.status == FlowStatus.PAUSED
    resumed = await StepRunner(
        adapter=StaticAgentAdapter({"x": "ok"}),
        store=store,
        gate_driver=PresetGateDriver({"gate": "approve"}),
    ).run(run_id=paused.run_id)
    assert resumed.status == FlowStatus.COMPLETED
    assert resumed.steps["after"].output == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [FlowStatus.COMPLETED, FlowStatus.FAILED, FlowStatus.CANCELLED])
async def test_terminal_runs_cannot_resume(linear_flow, status):
    store = InMemoryStateStore()
    run_id = store.create_run("x", ["first", "second"], linear_flow.raw_yaml)
    store.update_flow_status(run_id, status)
    with pytest.raises(InvalidResumeError):
        await StepRunner(adapter=StaticAgentAdapter({"*": "x"}), store=store).run(run_id=run_id)


@pytest.mark.asyncio
async def test_paused_run_requires_current_step(linear_flow):
    store = InMemoryStateStore()
    run_id = store.create_run("x", ["first", "second"], linear_flow.raw_yaml)
    store.update_flow_status(run_id, FlowStatus.PAUSED, current_step=None)
    with pytest.raises(InvalidResumeError, match="current_step"):
        await StepRunner(adapter=StaticAgentAdapter({"*": "x"}), store=store).run(run_id=run_id)


@pytest.mark.asyncio
async def test_retry_step_forms_complete_recovery_loop():
    flow = parse_flow("""name: retry
defaults: {retry: 0}
steps:
  - id: a
    agent: x
    prompt: go
""")
    store = InMemoryStateStore()
    runner = StepRunner(adapter=StaticAgentAdapter({"x": AgentResult.failure("bad")}), store=store)
    failed = await runner.run(flow)
    runner.retry_step(failed.run_id, "a")
    recovered = await StepRunner(adapter=StaticAgentAdapter({"x": "ok"}), store=store).run(
        run_id=failed.run_id, resume_from="a"
    )
    assert recovered.status == FlowStatus.COMPLETED
    assert recovered.steps["a"].output == "ok"
    assert recovered.steps["a"].visit_count == 2


def test_retry_step_rejects_non_failed_step(linear_flow):
    store = InMemoryStateStore()
    run_id = store.create_run("linear", ["first", "second"], linear_flow.raw_yaml)
    with pytest.raises(InvalidResumeError, match="only failed"):
        StepRunner(adapter=StaticAgentAdapter({"*": "x"}), store=store).retry_step(run_id, "first")


@pytest.mark.asyncio
async def test_edit_step_resets_target_and_descendants_and_uses_new_prompt(linear_flow):
    store = InMemoryStateStore()
    first = await StepRunner(
        adapter=StaticAgentAdapter({"alpha": "draft", "beta": "old"}), store=store
    ).run(linear_flow)
    adapter = StaticAgentAdapter({"alpha": "new-draft", "beta": "new-review"})
    runner = StepRunner(adapter=adapter, store=store)
    runner.edit_step(first.run_id, "first", "edited")
    result = await runner.run(run_id=first.run_id, resume_from="first")
    assert result.status == FlowStatus.COMPLETED
    assert adapter.calls[0] == ("alpha", "edited")
    assert result.steps["second"].output == "new-review"


@pytest.mark.asyncio
async def test_invalid_resume_target_is_rejected(linear_flow):
    store = InMemoryStateStore()
    run_id = store.create_run("linear", ["first", "second"], linear_flow.raw_yaml)
    store.update_flow_status(run_id, FlowStatus.RUNNING)
    with pytest.raises(InvalidResumeError, match="does not exist"):
        await StepRunner(adapter=StaticAgentAdapter({"*": "x"}), store=store).run(
            run_id=run_id, resume_from="missing"
        )


@pytest.mark.asyncio
async def test_resume_target_requires_completed_upstream(linear_flow):
    store = InMemoryStateStore()
    run_id = store.create_run("linear", ["first", "second"], linear_flow.raw_yaml)
    store.update_flow_status(run_id, FlowStatus.RUNNING)
    with pytest.raises(InvalidResumeError, match="upstream"):
        await StepRunner(adapter=StaticAgentAdapter({"*": "x"}), store=store).run(
            run_id=run_id, resume_from="second"
        )


class FakeMemory:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def write(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("memory offline")
        return {"data": {"memory_id": 42}}


@pytest.mark.asyncio
async def test_memory_write_records_id_and_metadata():
    flow = parse_flow("""name: memory
memory: {workspace: project-label}
steps:
  - id: a
    agent: x
    prompt: go
""")
    memory = FakeMemory()
    run = await StepRunner(
        adapter=StaticAgentAdapter({"x": AgentResult.success("ok", input_tokens=2)}),
        memory_client=memory,
    ).run(flow)
    assert run.context["a.memory_id"] == 42
    assert memory.calls[0]["workspace"] == "project-label"
    assert memory.calls[0]["metadata"]["input_tokens"] == 2


@pytest.mark.asyncio
async def test_memory_failure_is_observable_but_non_blocking():
    flow = parse_flow("name: m\nsteps:\n  - {id: a, agent: x, prompt: go}\n")
    sink = RecordingSink()
    run = await StepRunner(
        adapter=StaticAgentAdapter({"x": "ok"}), memory_client=FakeMemory(True), sink=sink
    ).run(flow)
    assert run.status == FlowStatus.COMPLETED
    assert any(event[0] == "memory_failed" for event in sink.events)


@pytest.mark.asyncio
async def test_missing_resolver_is_observable():
    flow = parse_flow("""name: missing
steps:
  - id: a
    agent: x
    prompt: "{env:AGENTLANE_TEST_NOT_SET}"
""")
    sink = RecordingSink()
    run = await StepRunner(adapter=StaticAgentAdapter({"x": "ok"}), sink=sink).run(flow)
    assert run.status == FlowStatus.COMPLETED
    assert any(event[0] == "resolver_missing" for event in sink.events)


@pytest.mark.asyncio
async def test_json_variables_are_available_to_downstream():
    flow = parse_flow("""name: vars
steps:
  - id: a
    agent: x
    prompt: go
    output:
      format: json
      schema: {value: string}
  - id: b
    agent: y
    prompt: "got {steps:a.value}"
    depends_on: [a]
""")
    adapter = StaticAgentAdapter({"x": '{"value":"yes"}', "y": "ok"})
    run = await StepRunner(adapter=adapter).run(flow)
    assert run.status == FlowStatus.COMPLETED
    assert adapter.calls[-1] == ("y", "got yes")


@pytest.mark.asyncio
async def test_hooks_receive_success_and_error_events():
    success_flow = parse_flow("name: h\nsteps:\n  - {id: a, agent: x, prompt: go}\n")
    hook = RecordingHook()
    await StepRunner(adapter=StaticAgentAdapter({"x": "ok"}), hook=hook).run(success_flow)
    assert hook.events == [("before", "a"), ("after", "a")]

    failure_flow = parse_flow("""name: h
defaults: {retry: 0}
steps:
  - {id: a, agent: x, prompt: go}
""")
    failed_hook = RecordingHook()
    await StepRunner(
        adapter=StaticAgentAdapter({"x": AgentResult.failure("bad")}), hook=failed_hook
    ).run(failure_flow)
    assert ("error", "a") in failed_hook.events


@pytest.mark.asyncio
async def test_cancelling_run_cleans_flow_and_step():
    started = asyncio.Event()

    async def slow(agent, prompt):
        started.set()
        await asyncio.sleep(10)

    flow = parse_flow("name: cancel\nsteps:\n  - {id: a, agent: x, prompt: go}\n")
    store = InMemoryStateStore()
    runner = StepRunner(adapter=StaticAgentAdapter({"x": slow}), store=store)
    task = asyncio.create_task(runner.run(flow))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    run = store.list_runs()[0]
    loaded = store.load_run(run.run_id)
    assert loaded.status == FlowStatus.CANCELLED
    assert loaded.steps["a"].status == StepStatus.FAILED


@pytest.mark.asyncio
async def test_new_run_rejects_resume_from(linear_flow):
    store = InMemoryStateStore()
    with pytest.raises(InvalidResumeError):
        await StepRunner(adapter=StaticAgentAdapter({"*": "x"}), store=store).run(
            linear_flow, resume_from="first"
        )
    assert store.list_runs() == []


@pytest.mark.asyncio
async def test_existing_run_rejects_semantically_different_supplied_flow(linear_flow):
    store = InMemoryStateStore()
    run_id = store.create_run("linear", ["first", "second"], linear_flow.raw_yaml)
    store.update_flow_status(run_id, FlowStatus.RUNNING)
    changed = parse_flow(linear_flow.raw_yaml.replace("prompt: create", "prompt: changed"))
    with pytest.raises(InvalidResumeError, match="does not match"):
        await StepRunner(adapter=StaticAgentAdapter({"*": "x"}), store=store).run(
            changed, run_id=run_id
        )


@pytest.mark.asyncio
async def test_new_run_requires_flow():
    with pytest.raises(ValueError, match="flow is required"):
        await StepRunner(adapter=StaticAgentAdapter({})).run()


@pytest.mark.asyncio
async def test_required_secrets_fail_before_a_run_is_created():
    flow = parse_flow("""name: secrets
secrets: {required: [TOKEN]}
steps:
  - {id: a, agent: x, prompt: "{secret:TOKEN}"}
""")
    store = InMemoryStateStore()
    with pytest.raises(FlowExecutionError, match="TOKEN"):
        await StepRunner(adapter=StaticAgentAdapter({"x": "ok"}), store=store).run(flow)
    assert store.list_runs() == []


@pytest.mark.asyncio
async def test_secret_reference_is_injected_and_missing_secret_fails_closed():
    flow = parse_flow("""name: secrets
steps:
  - {id: a, agent: x, prompt: "token={secret:TOKEN}"}
""")
    adapter = StaticAgentAdapter({"x": "ok"})
    completed = await StepRunner(adapter=adapter, secret_provider={"TOKEN": "hidden"}).run(flow)
    assert completed.status == FlowStatus.COMPLETED
    assert adapter.calls == [("x", "token=hidden")]

    unused_adapter = StaticAgentAdapter({"x": "must-not-run"})
    failed = await StepRunner(adapter=unused_adapter, secret_provider={}).run(flow)
    assert failed.status == FlowStatus.FAILED
    assert "secret is missing" in failed.steps["a"].error
    assert unused_adapter.calls == []


@pytest.mark.asyncio
async def test_gate_self_loop_honors_its_own_visit_limit():
    flow = parse_flow("""name: gate-loop
defaults: {max_visits: 2}
steps:
  - id: gate
    type: human_gate
    options:
      - {label: again, action: goto_step, target: gate}
""")
    run = await StepRunner(
        adapter=StaticAgentAdapter({}),
        gate_driver=PresetGateDriver({"gate": "again"}),
    ).run(flow)
    assert run.status == FlowStatus.FAILED
    assert run.steps["gate"].visit_count == 2
    assert "visit limit" in run.steps["gate"].error


class InvalidGateDriver(GateDriver):
    async def ask(self, step):
        return GateOption("not-configured", "next_step")


class ExplodingGateDriver(GateDriver):
    async def ask(self, step):
        raise RuntimeError("operator channel offline")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "driver,error",
    [
        (InvalidGateDriver(), "not defined"),
        (ExplodingGateDriver(), "operator channel offline"),
    ],
)
async def test_gate_driver_failures_are_persisted(driver, error):
    flow = parse_flow("""name: gate
steps:
  - id: gate
    type: human_gate
    options:
      - {label: approve, action: next_step}
""")
    run = await StepRunner(adapter=StaticAgentAdapter({}), gate_driver=driver).run(flow)
    assert run.status == FlowStatus.FAILED
    assert error in run.steps["gate"].error


@pytest.mark.asyncio
async def test_conflicting_parallel_gate_controls_fail_the_layer():
    flow = parse_flow("""name: gates
steps:
  - id: stop
    type: human_gate
    options:
      - {label: stop, action: terminate}
  - id: jump
    type: human_gate
    options:
      - {label: jump, action: goto_step, target: stop}
""")
    run = await StepRunner(
        adapter=StaticAgentAdapter({}),
        gate_driver=PresetGateDriver({"stop": "stop", "jump": "jump"}),
    ).run(flow)
    assert run.status == FlowStatus.FAILED
    assert "conflicting gate decisions" in run.context["__flow_error"]


class UnsafeStore(InMemoryStateStore):
    concurrent_safe = False


@pytest.mark.asyncio
async def test_runner_serializes_store_declared_not_concurrent_safe():
    store = UnsafeStore()
    flow = parse_flow("""name: parallel
steps:
  - {id: a, agent: a, prompt: go}
  - {id: b, agent: b, prompt: go}
""")
    runner = StepRunner(adapter=StaticAgentAdapter({"a": "a", "b": "b"}), store=store)
    run = await runner.run(flow)
    assert run.status == FlowStatus.COMPLETED
    assert runner.store.concurrent_safe
    assert len(store.list_runs()) == 1


class AlwaysBrokenSink(ObservabilitySink):
    def on_flow_start(self, *args):
        raise RuntimeError("sink failure")

    def on_step_start(self, *args):
        raise RuntimeError("sink failure")


class AlwaysBrokenHook(FlowHook):
    async def before_step(self, *args):
        raise RuntimeError("hook failure")


@pytest.mark.asyncio
async def test_direct_extension_failures_are_isolated(caplog):
    flow = parse_flow("name: extension\nsteps:\n  - {id: a, agent: x, prompt: go}\n")
    run = await StepRunner(
        adapter=StaticAgentAdapter({"x": "ok"}),
        sink=AlwaysBrokenSink(),
        hook=AlwaysBrokenHook(),
    ).run(flow)
    assert run.status == FlowStatus.COMPLETED
    assert "sink failure" in caplog.text
    assert "hook failure" in caplog.text


@pytest.mark.asyncio
async def test_unknown_resolver_is_rejected_before_run_creation():
    flow = parse_flow('name: bad-ref\nsteps:\n  - {id: a, agent: x, prompt: "{unknown:value}"}\n')
    store = InMemoryStateStore()
    with pytest.raises(FlowValidationError, match="unknown resolver prefix"):
        await StepRunner(adapter=StaticAgentAdapter({"x": "ok"}), store=store).run(flow)
    assert store.list_runs() == []


@pytest.mark.asyncio
async def test_structured_json_result_is_persisted_for_downstream_use():
    flow = parse_flow("""name: structured
steps:
  - id: a
    agent: x
    prompt: go
    output: {format: json, schema: {value: integer, empty: 'null'}}
  - id: b
    agent: y
    prompt: "value={steps:a.value}"
    depends_on: [a]
""")
    adapter = StaticAgentAdapter({"x": {"value": 2, "empty": None}, "y": "ok"})
    run = await StepRunner(adapter=adapter).run(flow)
    assert run.status == FlowStatus.COMPLETED
    assert adapter.calls[-1] == ("y", "value=2")


@pytest.mark.asyncio
async def test_memory_write_timeout_does_not_block_flow():
    class SlowMemory:
        def write(self, **kwargs):
            time.sleep(0.2)
            return {"id": 1}

    flow = parse_flow("name: memory\nsteps:\n  - {id: a, agent: x, prompt: go}\n")
    sink = RecordingSink()
    started = time.monotonic()
    run = await StepRunner(
        adapter=StaticAgentAdapter({"x": "ok"}),
        memory_client=SlowMemory(),
        memory_timeout=0.005,
        sink=sink,
    ).run(flow)
    assert time.monotonic() - started < 0.1
    assert run.status == FlowStatus.COMPLETED
    assert any(event[0] == "memory_failed" for event in sink.events)


def test_runner_rejects_non_positive_extension_timeouts():
    with pytest.raises(ValueError, match="must be positive"):
        StepRunner(adapter=StaticAgentAdapter({}), resolver_timeout=0)
