from __future__ import annotations

import json
import logging

import pytest

from agentlane.core.hooks import AuditLogHook, CompositeHook, FlowHook
from agentlane.core.observability import (
    CompositeSink,
    JsonlSink,
    ObservabilitySink,
    SummarySink,
)
from agentlane.core.result import AgentResult
from agentlane.core.state import FlowStatus, StepDefinition


def test_jsonl_sink_writes_structured_status_and_diagnostics(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path)
    sink.on_flow_start("run", "flow")
    sink.on_step_start("run", "a")
    sink.on_step_end(
        "run", "a", AgentResult.success("ok", duration_ms=10, input_tokens=2, output_tokens=3)
    )
    sink.on_resolver_missing("run", "a", "env:X", "missing")
    sink.on_memory_write_failed("run", "a", "offline")
    sink.on_flow_end("run", FlowStatus.COMPLETED)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "flow_start",
        "step_start",
        "step_end",
        "resolver_missing",
        "memory_write_failed",
        "flow_end",
    ]
    assert records[-1]["status"] == "completed"
    assert all("timestamp" in record for record in records)


def test_summary_accumulates_tokens_duration_and_errors():
    sink = SummarySink()
    sink.on_flow_start("run", "flow")
    sink.on_step_end(
        "run", "a", AgentResult.success("", duration_ms=1500, input_tokens=2, output_tokens=3)
    )
    sink.on_error("run", "b", "bad")
    sink.on_flow_end("run", FlowStatus.FAILED)
    metric = sink.live_snapshot("run")
    assert metric.total_tokens == 5
    assert metric.duration_ms == 1500
    assert metric.status == FlowStatus.FAILED
    rendered = sink.render_tree("run")
    assert "flow [failed]" in rendered
    assert "5 tokens" in rendered and "b: bad" in rendered


def test_summary_missing_run_is_empty():
    sink = SummarySink()
    assert sink.live_snapshot("missing") is None
    assert sink.render_tree("missing") == ""


class BadSink(ObservabilitySink):
    def on_flow_start(self, run_id, flow_name):
        raise RuntimeError("sink broke")


class GoodSink(ObservabilitySink):
    def __init__(self):
        self.called = False

    def on_flow_start(self, run_id, flow_name):
        self.called = True


def test_composite_sink_isolates_failure_and_logs(caplog):
    good = GoodSink()
    with caplog.at_level(logging.ERROR):
        CompositeSink([BadSink(), good]).on_flow_start("run", "flow")
    assert good.called
    assert "sink failed" in caplog.text


class BadHook(FlowHook):
    async def before_step(self, run_id, step, run):
        raise RuntimeError("hook broke")


class GoodHook(FlowHook):
    def __init__(self):
        self.called = False

    async def before_step(self, run_id, step, run):
        self.called = True


@pytest.mark.asyncio
async def test_composite_hook_isolates_failure(caplog):
    good = GoodHook()
    with caplog.at_level(logging.ERROR):
        await CompositeHook([BadHook(), good]).before_step("run", object(), object())
    assert good.called
    assert "hook failed" in caplog.text


@pytest.mark.asyncio
async def test_audit_log_hook_writes_structured_error():
    records = []
    await AuditLogHook(records.append).on_error("run", StepDefinition(id="step"), "broken")
    assert records == [{"run_id": "run", "step_id": "step", "error": "broken"}]
