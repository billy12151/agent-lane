from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..state import StepDefinition
from .base import FlowHook


class AuditLogHook(FlowHook):
    """Forward lifecycle failures to a logger-like callable."""

    def __init__(self, writer):
        self.writer = writer

    async def on_error(self, run_id, step, error):
        self.writer({"run_id": run_id, "step_id": step.id, "error": error})


class GatePendingFileHook(FlowHook):
    """Write a JSON notification when a human gate pauses for a decision.

    For each pending gate the hook writes
    ``<logs_dir>/gate-<run_id>-<step_id>.json`` containing the run id, the step,
    its message, and the options (label / action / target). A host agent that
    drove ``flow run --gate-notify`` can read this file to learn a decision is
    needed, ask its own user, and then resume with
    ``flow resume RUN_ID --gate-option STEP=LABEL``.

    The file is intentionally simple side-channel state: it decouples the pause
    (inside agentlane) from the decision (inside the host) without requiring the
    host to inject a callable or poll the run status.
    """

    def __init__(self, logs_dir: str | Path):
        self.logs_dir = Path(logs_dir)

    async def on_gate_pending(
        self, run_id: str, step: StepDefinition, options: list[Any]
    ) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "run_id": run_id,
            "step_id": step.id,
            "message": step.message or step.id,
            "options": [
                {"label": o.label, "action": o.action, "target": o.target} for o in options
            ],
            "resume_hint": (
                f"agentlane flow resume {run_id} --gate-option {step.id}=<label>"
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        target = self.logs_dir / f"gate-{run_id}-{step.id}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
