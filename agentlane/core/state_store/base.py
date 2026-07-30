"""Persistence contract and serialization helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import datetime
from typing import Any

from ..state import (
    FlowRun,
    FlowRunSummary,
    FlowStatus,
    GateDecision,
    StepSnapshot,
    StepStatus,
)

UNSET = object()


class StateStore(ABC):
    """Store methods are synchronous. `concurrent_safe` describes thread safety."""

    concurrent_safe: bool = False

    @abstractmethod
    def create_run(self, flow_name: str, steps: list[str], yaml_snapshot: str) -> str: ...

    @abstractmethod
    def load_run(self, run_id: str) -> FlowRun | None: ...

    @abstractmethod
    def update_step(
        self,
        run_id: str,
        step_id: str,
        status: StepStatus,
        *,
        output: Any = UNSET,
        error: Any = UNSET,
        visit_count: int | None = None,
        retry_count: int | None = None,
        duration_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None: ...

    @abstractmethod
    def reset_step(self, run_id: str, step_id: str) -> None: ...

    @abstractmethod
    def update_flow_status(
        self,
        run_id: str,
        status: FlowStatus,
        *,
        current_step: Any = UNSET,
    ) -> None: ...

    @abstractmethod
    def set_context(self, run_id: str, key: str, value: Any) -> None: ...

    @abstractmethod
    def get_context(self, run_id: str, key: str) -> Any: ...

    @abstractmethod
    def append_gate_decision(self, run_id: str, decision: GateDecision) -> None: ...

    @abstractmethod
    def list_runs(
        self, flow_name: str | None = None, status: FlowStatus | None = None
    ) -> list[FlowRunSummary]: ...

    @abstractmethod
    def delete_run(self, run_id: str) -> None: ...


def run_to_dict(run: FlowRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "flow_name": run.flow_name,
        "flow_yaml_snapshot": run.flow_yaml_snapshot,
        "status": run.status.value,
        "steps": {
            step_id: {
                "step_id": snapshot.step_id,
                "status": snapshot.status.value,
                "started_at": snapshot.started_at.isoformat() if snapshot.started_at else None,
                "finished_at": snapshot.finished_at.isoformat() if snapshot.finished_at else None,
                "output": snapshot.output,
                "error": snapshot.error,
                "retry_count": snapshot.retry_count,
                "visit_count": snapshot.visit_count,
                "duration_ms": snapshot.duration_ms,
                "input_tokens": snapshot.input_tokens,
                "output_tokens": snapshot.output_tokens,
            }
            for step_id, snapshot in run.steps.items()
        },
        "context": run.context,
        "current_step": run.current_step,
        "gate_decisions": [
            {
                **asdict(decision),
                "decided_at": decision.decided_at.isoformat(),
            }
            for decision in run.gate_decisions
        ],
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def run_from_dict(data: dict[str, Any]) -> FlowRun:
    steps = {
        step_id: StepSnapshot(
            step_id=value.get("step_id", step_id),
            status=StepStatus(value["status"]),
            started_at=datetime.fromisoformat(value["started_at"])
            if value.get("started_at")
            else None,
            finished_at=datetime.fromisoformat(value["finished_at"])
            if value.get("finished_at")
            else None,
            output=value.get("output"),
            error=value.get("error"),
            retry_count=int(value.get("retry_count", 0)),
            visit_count=int(value.get("visit_count", 0)),
            duration_ms=int(value.get("duration_ms") or 0),
            input_tokens=value.get("input_tokens"),
            output_tokens=value.get("output_tokens"),
        )
        for step_id, value in data.get("steps", {}).items()
    }
    decisions = [
        GateDecision(
            step_id=value["step_id"],
            label=value["label"],
            action=value["action"],
            target=value.get("target", ""),
            decided_at=datetime.fromisoformat(value["decided_at"]),
            decided_by=value.get("decided_by", "user"),
            note=value.get("note"),
        )
        for value in data.get("gate_decisions", [])
    ]
    return FlowRun(
        run_id=data["run_id"],
        flow_name=data["flow_name"],
        flow_yaml_snapshot=data["flow_yaml_snapshot"],
        status=FlowStatus(data["status"]),
        steps=steps,
        context=data.get("context", {}),
        current_step=data.get("current_step"),
        gate_decisions=decisions,
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )
