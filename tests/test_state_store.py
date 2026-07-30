from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentlane.core.errors import StateStoreError
from agentlane.core.state import FlowStatus, GateDecision, StepStatus
from agentlane.core.state_store import (
    InMemoryStateStore,
    JsonFileStateStore,
    TaskFlowStateStore,
)


@pytest.fixture(params=["memory", "json"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryStateStore()
    return JsonFileStateStore(tmp_path / "runs.json")


def test_create_and_load_returns_deep_copy(store):
    run_id = store.create_run("flow", ["a"], "yaml")
    run = store.load_run(run_id)
    run.context["mutated"] = True
    assert store.load_run(run_id).context == {}


def test_step_lifecycle_and_explicit_none_output(store):
    run_id = store.create_run("flow", ["a"], "yaml")
    store.update_step(run_id, "a", StepStatus.RUNNING, visit_count=1)
    running = store.load_run(run_id).steps["a"]
    assert running.started_at is not None and running.finished_at is None
    store.update_step(run_id, "a", StepStatus.COMPLETED, output="value")
    store.update_step(run_id, "a", StepStatus.COMPLETED, output=None)
    completed = store.load_run(run_id).steps["a"]
    assert completed.output is None and completed.finished_at is not None


def test_reset_clears_result_but_preserves_counters(store):
    run_id = store.create_run("flow", ["a"], "yaml")
    store.update_step(
        run_id,
        "a",
        StepStatus.FAILED,
        output="partial",
        error="bad",
        visit_count=2,
        retry_count=1,
    )
    store.reset_step(run_id, "a")
    snapshot = store.load_run(run_id).steps["a"]
    assert snapshot.status == StepStatus.PENDING
    assert snapshot.output is None and snapshot.error is None
    assert snapshot.visit_count == 2 and snapshot.retry_count == 1


def test_context_is_copied(store):
    run_id = store.create_run("flow", ["a"], "yaml")
    value = {"nested": []}
    store.set_context(run_id, "key", value)
    value["nested"].append(1)
    assert store.get_context(run_id, "key") == {"nested": []}


def test_gate_decision_is_persisted(store):
    run_id = store.create_run("flow", ["gate"], "yaml")
    decision = GateDecision("gate", "approve", "next_step")
    store.append_gate_decision(run_id, decision)
    assert store.load_run(run_id).gate_decisions == [decision]


def test_list_filters_and_delete(store):
    first = store.create_run("one", ["a"], "yaml")
    second = store.create_run("two", ["a"], "yaml")
    store.update_flow_status(first, FlowStatus.FAILED)
    store.update_flow_status(second, FlowStatus.COMPLETED)
    assert [item.run_id for item in store.list_runs(status=FlowStatus.FAILED)] == [first]
    assert [item.run_id for item in store.list_runs(flow_name="two")] == [second]
    store.delete_run(first)
    assert store.load_run(first) is None


def test_unknown_run_and_step_raise_visible_errors(store):
    with pytest.raises(StateStoreError, match="does not exist"):
        store.update_flow_status("missing", FlowStatus.RUNNING)
    run_id = store.create_run("flow", ["a"], "yaml")
    with pytest.raises(StateStoreError, match="step does not exist"):
        store.reset_step(run_id, "missing")


def test_compact_run_id_collision_is_retried(monkeypatch):
    values = iter(["a" * 32, "a" * 32, "b" * 32])
    monkeypatch.setattr(
        "agentlane.core.state_store.memory.uuid.uuid4",
        lambda: SimpleNamespace(hex=next(values)),
    )
    store = InMemoryStateStore()
    assert store.create_run("one", ["a"], "yaml") == "a" * 12
    assert store.create_run("two", ["a"], "yaml") == "b" * 12


def test_json_store_survives_restart_with_all_fields(tmp_path):
    path = tmp_path / "runs.json"
    store = JsonFileStateStore(path)
    run_id = store.create_run("flow", ["a"], "yaml")
    store.update_step(
        run_id,
        "a",
        StepStatus.COMPLETED,
        output={"x": 1},
        visit_count=1,
        duration_ms=25,
        input_tokens=2,
        output_tokens=3,
    )
    store.update_flow_status(run_id, FlowStatus.PAUSED, current_step="a")
    store.set_context(run_id, "a.memory_id", 42)
    store.append_gate_decision(run_id, GateDecision("a", "yes", "next_step"))

    restored = JsonFileStateStore(path).load_run(run_id)
    assert restored.status == FlowStatus.PAUSED
    assert restored.current_step == "a"
    assert restored.steps["a"].output == {"x": 1}
    assert restored.steps["a"].duration_ms == 25
    assert restored.steps["a"].total_tokens == 5
    assert restored.context["a.memory_id"] == 42
    assert restored.gate_decisions[0].label == "yes"
    assert restored.created_at.tzinfo is not None


@pytest.mark.parametrize("payload", ["not-json", "[]", '{"runs": []}'])
def test_json_store_rejects_corrupt_file(tmp_path, payload):
    path = tmp_path / "runs.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid AgentLane state"):
        JsonFileStateStore(path)


def test_json_store_rejects_malformed_run_record(tmp_path):
    path = tmp_path / "runs.json"
    path.write_text('{"runs":{"bad":{}}}', encoding="utf-8")
    with pytest.raises(StateStoreError, match="run record"):
        JsonFileStateStore(path)


class FakeTaskFlow:
    def __init__(self):
        self.data = {}
        self.deleted = []

    def load_all(self):
        return dict(self.data)

    def save(self, run_id, payload):
        self.data[run_id] = payload

    def delete(self, run_id):
        self.deleted.append(run_id)
        self.data.pop(run_id, None)


class FailingTaskFlow(FakeTaskFlow):
    def __init__(self, *, fail_load=False):
        super().__init__()
        self.fail_load = fail_load
        self.fail_save = False
        self.fail_delete = False

    def load_all(self):
        if self.fail_load:
            raise RuntimeError("load offline")
        return super().load_all()

    def save(self, run_id, payload):
        if self.fail_save:
            raise RuntimeError("save offline")
        super().save(run_id, payload)

    def delete(self, run_id):
        if self.fail_delete:
            raise RuntimeError("delete offline")
        super().delete(run_id)


def test_taskflow_requires_client():
    with pytest.raises(ValueError, match="client is required"):
        TaskFlowStateStore(None)


def test_taskflow_round_trip():
    client = FakeTaskFlow()
    store = TaskFlowStateStore(client)
    run_id = store.create_run("flow", ["a"], "yaml")
    store.update_step(run_id, "a", StepStatus.COMPLETED, output="done")
    store.reset_step(run_id, "a")
    store.update_flow_status(run_id, FlowStatus.PAUSED, current_step="a")
    store.set_context(run_id, "key", {"nested": True})
    store.append_gate_decision(run_id, GateDecision("a", "yes", "next_step"))
    restored = TaskFlowStateStore(client).load_run(run_id)
    assert restored.steps["a"].status == StepStatus.PENDING
    assert restored.status == FlowStatus.PAUSED
    assert restored.context["key"] == {"nested": True}
    assert restored.gate_decisions[0].label == "yes"
    store.delete_run(run_id)
    assert client.deleted == [run_id]


def test_taskflow_failures_are_domain_errors_and_local_changes_roll_back():
    with pytest.raises(StateStoreError, match="load failed"):
        TaskFlowStateStore(FailingTaskFlow(fail_load=True))

    client = FailingTaskFlow()
    store = TaskFlowStateStore(client)
    run_id = store.create_run("flow", ["a"], "yaml")
    client.fail_save = True
    with pytest.raises(StateStoreError, match="save failed"):
        store.update_flow_status(run_id, FlowStatus.COMPLETED)
    assert store.load_run(run_id).status == FlowStatus.PENDING
    client.fail_save = False
    client.fail_delete = True
    with pytest.raises(StateStoreError, match="delete failed"):
        store.delete_run(run_id)
    assert store.load_run(run_id) is not None


def test_json_store_refreshes_between_independent_instances(tmp_path):
    path = tmp_path / "runs.json"
    first = JsonFileStateStore(path)
    second = JsonFileStateStore(path)
    first_id = first.create_run("first", ["a"], "yaml")
    second_id = second.create_run("second", ["b"], "yaml")
    assert {item.run_id for item in first.list_runs()} == {first_id, second_id}
    first.update_flow_status(first_id, FlowStatus.COMPLETED)
    second.update_flow_status(second_id, FlowStatus.FAILED)
    restored = JsonFileStateStore(path)
    assert restored.load_run(first_id).status == FlowStatus.COMPLETED
    assert restored.load_run(second_id).status == FlowStatus.FAILED


def test_json_store_prune_filters_and_keeps_recent(tmp_path):
    store = JsonFileStateStore(tmp_path / "runs.json")
    older = store.create_run("flow", ["a"], "yaml")
    newer = store.create_run("flow", ["a"], "yaml")
    failed = store.create_run("flow", ["a"], "yaml")
    store.update_flow_status(older, FlowStatus.COMPLETED)
    store.update_flow_status(newer, FlowStatus.COMPLETED)
    store.update_flow_status(failed, FlowStatus.FAILED)
    assert store.prune(status=FlowStatus.COMPLETED, keep=1) == 1
    assert store.load_run(newer) is not None
    assert store.load_run(older) is None
    assert store.load_run(failed) is not None
    with pytest.raises(ValueError, match="non-negative"):
        store.prune(keep=-1)
