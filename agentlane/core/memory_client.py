"""Optional in-process memory-arbiter integration."""

from __future__ import annotations

from typing import Any

from .errors import FlowExecutionError


def build_memory_client(enabled: bool) -> Any | None:
    """Build the official in-process client when explicitly enabled.

    memory-arbiter is intentionally optional. An explicit enablement fails
    loudly when the package or its configured database cannot be opened.
    """

    if not enabled:
        return None
    try:
        from memory_arbiter.config import Settings
        from memory_arbiter.tools import MemoryTools

        return MemoryTools(Settings.from_env())
    except Exception as exc:
        raise FlowExecutionError(
            f"memory-arbiter is enabled but unavailable: {type(exc).__name__}: {exc}"
        ) from exc
