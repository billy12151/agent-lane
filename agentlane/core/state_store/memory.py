"""Thread-safe in-process StateStore."""

from __future__ import annotations

import copy
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..errors import InvalidResumeError, StateStoreError
from ..state import (
    FlowRun,
    FlowRunSummary,
    FlowStatus,
    GateDecision,
    StepSnapshot,
    StepStatus,
    utc_now,
)
from .base import UNSET, StateStore


class InMemoryStateStore(StateStore):
    concurrent_safe = True

    def __init__(self):
        self._runs: dict[str, FlowRun] = {}
        self._lock = threading.RLock()
        self._active_runs: set[str] = set()

    @contextmanager
    def run_lease(self, run_id: str) -> Iterator[None]:
        """Prevent two interleaved resumes of the same run in one process."""

        with self._lock:
            if run_id in self._active_runs:
                raise InvalidResumeError(f"run {run_id} is already executing")
            self._active_runs.add(run_id)
        try:
            yield
        finally:
            with self._lock:
                self._active_runs.discard(run_id)

    def create_run(self, flow_name: str, steps: list[str], yaml_snapshot: str) -> str:
        with self._lock:
            # Keep local ids compact without allowing an unlikely collision to
            # overwrite an existing run.
            run_id = uuid.uuid4().hex[:12]
            while run_id in self._runs:
                run_id = uuid.uuid4().hex[:12]
            self._runs[run_id] = FlowRun(
                run_id=run_id,
                flow_name=flow_name,
                flow_yaml_snapshot=yaml_snapshot,
                status=FlowStatus.PENDING,
                steps={step_id: StepSnapshot(step_id) for step_id in steps},
            )
        return run_id

    def load_run(self, run_id: str) -> FlowRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            return copy.deepcopy(run) if run is not None else None

    def _run(self, run_id: str) -> FlowRun:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise StateStoreError(f"run does not exist: {run_id}") from exc

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
    ) -> None:
        with self._lock:
            run = self._run(run_id)
            if step_id not in run.steps:
                raise StateStoreError(f"step does not exist in run: {step_id}")
            snapshot = run.steps[step_id]
            snapshot.status = status
            if status == StepStatus.RUNNING:
                snapshot.started_at = utc_now()
                snapshot.finished_at = None
            elif status in {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED}:
                snapshot.finished_at = utc_now()
            if output is not UNSET:
                snapshot.output = copy.deepcopy(output)
            if error is not UNSET:
                snapshot.error = error
            if visit_count is not None:
                snapshot.visit_count = visit_count
            if retry_count is not None:
                snapshot.retry_count = retry_count
            if duration_ms is not None:
                snapshot.duration_ms = duration_ms
            if input_tokens is not None:
                snapshot.input_tokens = input_tokens
            if output_tokens is not None:
                snapshot.output_tokens = output_tokens
            run.updated_at = utc_now()

    def reset_step(self, run_id: str, step_id: str) -> None:
        with self._lock:
            snapshot = self._run(run_id).steps.get(step_id)
            if snapshot is None:
                raise StateStoreError(f"step does not exist in run: {step_id}")
            snapshot.status = StepStatus.PENDING
            snapshot.started_at = None
            snapshot.finished_at = None
            snapshot.output = None
            snapshot.error = None
            snapshot.duration_ms = 0
            snapshot.input_tokens = None
            snapshot.output_tokens = None
            self._run(run_id).updated_at = utc_now()

    def update_flow_status(
        self,
        run_id: str,
        status: FlowStatus,
        *,
        current_step: Any = UNSET,
    ) -> None:
        with self._lock:
            run = self._run(run_id)
            run.status = status
            if current_step is not UNSET:
                run.current_step = current_step
            run.updated_at = utc_now()

    def set_context(self, run_id: str, key: str, value: Any) -> None:
        with self._lock:
            run = self._run(run_id)
            run.context[key] = copy.deepcopy(value)
            run.updated_at = utc_now()

    def get_context(self, run_id: str, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._run(run_id).context.get(key))

    def append_gate_decision(self, run_id: str, decision: GateDecision) -> None:
        with self._lock:
            run = self._run(run_id)
            run.gate_decisions.append(decision)
            run.updated_at = utc_now()

    def list_runs(
        self, flow_name: str | None = None, status: FlowStatus | None = None
    ) -> list[FlowRunSummary]:
        with self._lock:
            result = [
                FlowRunSummary(
                    run_id=run.run_id,
                    flow_name=run.flow_name,
                    status=run.status,
                    current_step=run.current_step,
                    created_at=run.created_at,
                )
                for run in self._runs.values()
                if (flow_name is None or run.flow_name == flow_name)
                and (status is None or run.status == status)
            ]
        return sorted(result, key=lambda item: item.created_at, reverse=True)

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)
