"""Non-blocking observability event contract."""

from __future__ import annotations

from ..result import AgentResult
from ..state import FlowStatus


class ObservabilitySink:
    def on_flow_start(self, run_id: str, flow_name: str) -> None:
        pass

    def on_flow_end(self, run_id: str, status: FlowStatus) -> None:
        pass

    def on_step_start(self, run_id: str, step_id: str) -> None:
        pass

    def on_step_end(self, run_id: str, step_id: str, result: AgentResult) -> None:
        pass

    def on_error(self, run_id: str, step_id: str | None, error: str) -> None:
        pass

    def on_resolver_missing(
        self, run_id: str, step_id: str, source: str, error: str | None = None
    ) -> None:
        pass

    def on_memory_write_failed(self, run_id: str, step_id: str, error: str) -> None:
        pass
