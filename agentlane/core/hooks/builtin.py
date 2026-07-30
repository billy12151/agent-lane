from __future__ import annotations

from .base import FlowHook


class AuditLogHook(FlowHook):
    """Forward lifecycle failures to a logger-like callable."""

    def __init__(self, writer):
        self.writer = writer

    async def on_error(self, run_id, step, error):
        self.writer({"run_id": run_id, "step_id": step.id, "error": error})
