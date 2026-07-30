"""Flow definitions and durable execution snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class FlowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True, frozen=True)
class GateOption:
    label: str
    action: str
    target: str = ""


@dataclass(slots=True)
class StepDefinition:
    id: str
    agent: str = ""
    prompt: str = ""
    type: str = "agent"
    depends_on: list[str] = field(default_factory=list)
    timeout: int | None = None
    retry: int | None = None
    max_visits: int | None = None
    group: str | None = None
    output: Any = None
    message: str = ""
    options: list[GateOption] = field(default_factory=list)
    terminal: bool = False


@dataclass(slots=True)
class FlowDefinition:
    name: str
    steps: list[StepDefinition]
    version: int = 1
    description: str = ""
    defaults_timeout: int = 300
    defaults_retry: int = 1
    defaults_max_visits: int = 3
    defaults_fail_fast: bool = False
    memory_workspace: str = "default"
    required_secrets: list[str] = field(default_factory=list)
    raw_yaml: str = ""

    def step(self, step_id: str) -> StepDefinition:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(f"unknown step: {step_id}")


@dataclass(slots=True)
class StepSnapshot:
    step_id: str
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output: Any = None
    error: str | None = None
    retry_count: int = 0
    visit_count: int = 0
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


@dataclass(slots=True, frozen=True)
class GateDecision:
    step_id: str
    label: str
    action: str
    target: str = ""
    decided_at: datetime = field(default_factory=utc_now)
    decided_by: str = "user"
    note: str | None = None


@dataclass(slots=True)
class FlowRun:
    run_id: str
    flow_name: str
    flow_yaml_snapshot: str
    status: FlowStatus
    steps: dict[str, StepSnapshot]
    context: dict[str, Any] = field(default_factory=dict)
    current_step: str | None = None
    gate_decisions: list[GateDecision] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True, frozen=True)
class FlowRunSummary:
    run_id: str
    flow_name: str
    status: FlowStatus
    current_step: str | None
    created_at: datetime
