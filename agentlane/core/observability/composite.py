from __future__ import annotations

import logging
from typing import Any

from .base import ObservabilitySink

logger = logging.getLogger(__name__)


class CompositeSink(ObservabilitySink):
    def __init__(self, sinks: list[ObservabilitySink] | None = None):
        self.sinks = list(sinks or [])

    def _dispatch(self, method: str, *args: Any) -> None:
        for sink in self.sinks:
            try:
                getattr(sink, method)(*args)
            except Exception:
                logger.exception("observability sink failed: %s.%s", type(sink).__name__, method)

    def on_flow_start(self, *args: Any) -> None:
        self._dispatch("on_flow_start", *args)

    def on_flow_end(self, *args: Any) -> None:
        self._dispatch("on_flow_end", *args)

    def on_step_start(self, *args: Any) -> None:
        self._dispatch("on_step_start", *args)

    def on_step_end(self, *args: Any) -> None:
        self._dispatch("on_step_end", *args)

    def on_error(self, *args: Any) -> None:
        self._dispatch("on_error", *args)

    def on_resolver_missing(self, *args: Any) -> None:
        self._dispatch("on_resolver_missing", *args)

    def on_memory_write_failed(self, *args: Any) -> None:
        self._dispatch("on_memory_write_failed", *args)
