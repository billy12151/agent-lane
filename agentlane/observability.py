"""Compatibility exports for observability sinks."""

from .core.observability import CompositeSink, JsonlSink, NullSink, ObservabilitySink, SummarySink

__all__ = ["ObservabilitySink", "NullSink", "CompositeSink", "JsonlSink", "SummarySink"]
