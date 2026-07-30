"""TaskFlow-backed store used by the OpenClaw runtime."""

from __future__ import annotations

import copy
from typing import Any

from ..errors import StateStoreError
from ..state import FlowStatus, GateDecision, StepStatus
from .base import run_from_dict, run_to_dict
from .memory import InMemoryStateStore


class TaskFlowStateStore(InMemoryStateStore):
    """Persist complete FlowRun snapshots through an injected TaskFlow client.

    The client must provide `load_all() -> dict` and `save(run_id, payload)`;
    `delete(run_id)` is required for deletion. Missing clients fail loudly.
    """

    concurrent_safe = True

    def __init__(self, client: Any):
        if client is None:
            raise ValueError("TaskFlow client is required")
        super().__init__()
        self.client = client
        try:
            payloads = client.load_all()
            if not isinstance(payloads, dict):
                raise TypeError("load_all() must return a mapping")
            self._runs = {run_id: run_from_dict(value) for run_id, value in payloads.items()}
        except Exception as exc:
            raise StateStoreError(f"TaskFlow state load failed: {exc}") from exc

    def _save(self, run_id: str) -> None:
        self.client.save(run_id, run_to_dict(self._runs[run_id]))

    def _mutate_and_save(self, run_id: str, operation: Any) -> None:
        previous = copy.deepcopy(self._runs.get(run_id))
        existed = run_id in self._runs
        try:
            operation()
        except Exception:
            if existed:
                assert previous is not None
                self._runs[run_id] = previous
            else:
                self._runs.pop(run_id, None)
            raise
        try:
            self._save(run_id)
        except Exception as exc:
            if existed:
                assert previous is not None
                self._runs[run_id] = previous
            else:
                self._runs.pop(run_id, None)
            raise StateStoreError(f"TaskFlow state save failed: {exc}") from exc

    def create_run(self, *args: Any, **kwargs: Any) -> str:
        with self._lock:
            run_id = super().create_run(*args, **kwargs)
            try:
                self._save(run_id)
            except Exception as exc:
                self._runs.pop(run_id, None)
                raise StateStoreError(f"TaskFlow state save failed: {exc}") from exc
            return run_id

    def update_step(self, run_id: str, step_id: str, status: StepStatus, **kwargs: Any) -> None:
        with self._lock:
            self._mutate_and_save(
                run_id,
                lambda: super(TaskFlowStateStore, self).update_step(
                    run_id, step_id, status, **kwargs
                ),
            )

    def reset_step(self, run_id: str, step_id: str) -> None:
        with self._lock:
            self._mutate_and_save(
                run_id,
                lambda: super(TaskFlowStateStore, self).reset_step(run_id, step_id),
            )

    def update_flow_status(self, run_id: str, status: FlowStatus, **kwargs: Any) -> None:
        with self._lock:
            self._mutate_and_save(
                run_id,
                lambda: super(TaskFlowStateStore, self).update_flow_status(
                    run_id, status, **kwargs
                ),
            )

    def set_context(self, run_id: str, key: str, value: Any) -> None:
        with self._lock:
            self._mutate_and_save(
                run_id,
                lambda: super(TaskFlowStateStore, self).set_context(run_id, key, value),
            )

    def append_gate_decision(self, run_id: str, decision: GateDecision) -> None:
        with self._lock:
            self._mutate_and_save(
                run_id,
                lambda: super(TaskFlowStateStore, self).append_gate_decision(run_id, decision),
            )

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            try:
                self.client.delete(run_id)
            except Exception as exc:
                raise StateStoreError(f"TaskFlow state delete failed: {exc}") from exc
            super().delete_run(run_id)
