from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..state import StepDefinition, utc_now
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
    host to inject a callable or poll the run status. Once the decision lands,
    ``on_gate_decision`` removes the file so ``logs_dir`` does not accumulate
    one stale notification per gate ever driven.
    """

    def __init__(self, logs_dir: str | Path):
        self.logs_dir = Path(logs_dir)

    def _path(self, run_id: str, step_id: str) -> Path:
        return self.logs_dir / f"gate-{run_id}-{step_id}.json"

    async def on_gate_pending(
        self, run_id: str, step: StepDefinition, options: list[Any]
    ) -> None:
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
            "created_at": utc_now().isoformat(),
        }
        # Atomic write so a host never reads a half-written notification.
        self._atomic_write(
            self._path(run_id, step.id),
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    async def on_gate_decision(self, run_id: str, step: StepDefinition, decision: Any) -> None:
        # The decision is in; the pending notification is now stale. Remove it
        # so logs_dir does not grow one file per gate ever driven.
        with suppress(FileNotFoundError):
            self._path(run_id, step.id).unlink()

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
