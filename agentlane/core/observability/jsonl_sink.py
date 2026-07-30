from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..state import FlowStatus, utc_now
from .base import ObservabilitySink


class JsonlSink(ObservabilitySink):
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _write(self, event: str, **data: Any) -> None:
        record = {"timestamp": utc_now().isoformat(), "event": event, **data}
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def on_flow_start(self, run_id: str, flow_name: str) -> None:
        self._write("flow_start", run_id=run_id, flow_name=flow_name)

    def on_flow_end(self, run_id: str, status: FlowStatus) -> None:
        self._write("flow_end", run_id=run_id, status=status.value)

    def on_step_start(self, run_id: str, step_id: str) -> None:
        self._write("step_start", run_id=run_id, step_id=step_id)

    def on_step_end(self, run_id: str, step_id: str, result: Any) -> None:
        self._write(
            "step_end",
            run_id=run_id,
            step_id=step_id,
            ok=result.ok,
            duration_ms=result.duration_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    def on_error(self, run_id: str, step_id: str | None, error: str) -> None:
        self._write("error", run_id=run_id, step_id=step_id, error=error)

    def on_resolver_missing(
        self, run_id: str, step_id: str, source: str, error: str | None = None
    ) -> None:
        self._write("resolver_missing", run_id=run_id, step_id=step_id, source=source, error=error)

    def on_memory_write_failed(self, run_id: str, step_id: str, error: str) -> None:
        self._write("memory_write_failed", run_id=run_id, step_id=step_id, error=error)
