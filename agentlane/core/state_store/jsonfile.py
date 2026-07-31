"""Atomic JSON persistence for the standalone CLI."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

from ..errors import InvalidResumeError, StateStoreError
from ..state import FlowRun, FlowRunSummary, FlowStatus
from .base import run_from_dict, run_to_dict
from .memory import InMemoryStateStore


class JsonFileStateStore(InMemoryStateStore):
    concurrent_safe = True

    def __init__(self, path: str | Path):
        super().__init__()
        self.path = Path(path).expanduser()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cached_sig: tuple[int, int] | None = None
        self._load_file()

    def _load_file(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._runs = {}
            self._cached_sig = None
            return
        # Skip re-parsing when the file is unchanged on disk since the last
        # load. The runner reads run state on nearly every step transition, so
        # this avoids repeating json.loads for an unmodified file. A write by
        # another process changes (mtime, size) and invalidates the cache.
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._cached_sig:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateStoreError(f"invalid AgentLane state file {self.path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("runs", {}), dict):
            raise StateStoreError(f"invalid AgentLane state file shape: {self.path}")
        try:
            self._runs = {
                run_id: run_from_dict(value) for run_id, value in data.get("runs", {}).items()
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise StateStoreError(f"invalid AgentLane run record in {self.path}: {exc}") from exc
        self._cached_sig = signature

    @contextmanager
    def _file_guard(self, *, exclusive: bool) -> Iterator[None]:
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(handle.fileno(), mode)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _persist(self) -> None:
        payload = json.dumps(
            {"version": 1, "runs": {key: run_to_dict(value) for key, value in self._runs.items()}},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            stat = self.path.stat()
            self._cached_sig = (stat.st_mtime_ns, stat.st_size)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _mutate(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self._lock, self._file_guard(exclusive=True):
            self._load_file()
            result = operation(*args, **kwargs)
            self._persist()
            return result

    def load_run(self, run_id: str) -> FlowRun | None:
        with self._lock, self._file_guard(exclusive=False):
            self._load_file()
            return super().load_run(run_id)

    def get_context(self, run_id: str, key: str) -> Any:
        with self._lock, self._file_guard(exclusive=False):
            self._load_file()
            return super().get_context(run_id, key)

    def list_runs(
        self, flow_name: str | None = None, status: FlowStatus | None = None
    ) -> list[FlowRunSummary]:
        with self._lock, self._file_guard(exclusive=False):
            self._load_file()
            return super().list_runs(flow_name=flow_name, status=status)

    def create_run(self, *args: Any, **kwargs: Any) -> str:
        return self._mutate(super().create_run, *args, **kwargs)

    def update_step(self, *args: Any, **kwargs: Any) -> None:
        self._mutate(super().update_step, *args, **kwargs)

    def reset_step(self, *args: Any, **kwargs: Any) -> None:
        self._mutate(super().reset_step, *args, **kwargs)

    def update_flow_status(self, *args: Any, **kwargs: Any) -> None:
        self._mutate(super().update_flow_status, *args, **kwargs)

    def set_context(self, *args: Any, **kwargs: Any) -> None:
        self._mutate(super().set_context, *args, **kwargs)

    def append_gate_decision(self, *args: Any, **kwargs: Any) -> None:
        self._mutate(super().append_gate_decision, *args, **kwargs)

    def delete_run(self, *args: Any, **kwargs: Any) -> None:
        self._mutate(super().delete_run, *args, **kwargs)

    def prune(
        self,
        *,
        status: FlowStatus | None = None,
        keep: int = 0,
        older_than_days: int | None = None,
    ) -> int:
        """Delete matching runs in one atomic state-file rewrite."""

        if keep < 0:
            raise ValueError("keep must be non-negative")
        if older_than_days is not None and older_than_days < 0:
            raise ValueError("older_than_days must be non-negative")
        now = datetime.now(timezone.utc)
        with self._lock, self._file_guard(exclusive=True):
            self._load_file()
            candidates = sorted(
                (run for run in self._runs.values() if status is None or run.status == status),
                key=lambda run: run.created_at,
                reverse=True,
            )
            selected = []
            for index, run in enumerate(candidates):
                if index < keep:
                    continue
                if older_than_days is not None and (now - run.created_at).days < older_than_days:
                    continue
                selected.append(run.run_id)
            for run_id in selected:
                self._runs.pop(run_id, None)
            if selected:
                self._persist()
            return len(selected)

    @contextmanager
    def run_lease(self, run_id: str) -> Iterator[None]:
        """Cross-process execution lease via a per-run advisory file lock.

        Combined with the in-process guard inherited from InMemoryStateStore,
        this prevents two processes (or two interleaved resumes in one process)
        from driving the same run concurrently and racing on the state file.
        """

        with self._lock:
            if run_id in self._active_runs:
                raise InvalidResumeError(f"run {run_id} is already executing")
            self._active_runs.add(run_id)
        lock_path = self.path.parent / f".{self.path.name}.{run_id}.lock"
        handle = lock_path.open("a+", encoding="utf-8")
        acquired = False
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as exc:
                    raise InvalidResumeError(
                        f"run {run_id} is already executing in another process"
                    ) from exc
            yield
        finally:
            if acquired:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            with self._lock:
                self._active_runs.discard(run_id)
