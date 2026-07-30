from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..result import AgentResult
from ..state import FlowStatus
from .base import ObservabilitySink


@dataclass(slots=True)
class StepMetric:
    step_id: str
    ok: bool
    duration_ms: int
    tokens: int | None


@dataclass(slots=True)
class RunMetric:
    flow_name: str = ""
    status: FlowStatus | None = None
    steps: list[StepMetric] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return sum(step.duration_ms for step in self.steps)

    @property
    def total_tokens(self) -> int | None:
        values = [step.tokens for step in self.steps if step.tokens is not None]
        return sum(values) if values else None


class SummarySink(ObservabilitySink):
    def __init__(self):
        self.runs: dict[str, RunMetric] = {}
        self._lock = threading.Lock()

    def on_flow_start(self, run_id: str, flow_name: str) -> None:
        with self._lock:
            self.runs.setdefault(run_id, RunMetric()).flow_name = flow_name

    def on_flow_end(self, run_id: str, status: FlowStatus) -> None:
        with self._lock:
            self.runs.setdefault(run_id, RunMetric()).status = status

    def on_step_end(self, run_id: str, step_id: str, result: AgentResult) -> None:
        with self._lock:
            self.runs.setdefault(run_id, RunMetric()).steps.append(
                StepMetric(step_id, result.ok, result.duration_ms, result.total_tokens)
            )

    def on_error(self, run_id: str, step_id: str | None, error: str) -> None:
        with self._lock:
            label = f"{step_id}: {error}" if step_id else error
            self.runs.setdefault(run_id, RunMetric()).errors.append(label)

    def live_snapshot(self, run_id: str) -> RunMetric | None:
        with self._lock:
            metric = self.runs.get(run_id)
            if metric is None:
                return None
            return RunMetric(
                flow_name=metric.flow_name,
                status=metric.status,
                steps=list(metric.steps),
                errors=list(metric.errors),
            )

    def render_tree(self, run_id: str) -> str:
        metric = self.live_snapshot(run_id)
        if metric is None:
            return ""
        status = metric.status.value if metric.status else "running"
        lines = [f"{metric.flow_name} [{status}]"]
        for step in metric.steps:
            token_text = "-" if step.tokens is None else str(step.tokens)
            lines.append(
                f"  ├─ {step.step_id:<20} {'✓' if step.ok else '✗'}  "
                f"{step.duration_ms / 1000:.2f}s  {token_text} tokens"
            )
        for error in metric.errors:
            lines.append(f"  └─ error: {error}")
        return "\n".join(lines)
