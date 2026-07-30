"""Domain errors exposed by AgentLane's public API."""


class AgentLaneError(Exception):
    """Base class for expected AgentLane errors."""


class FlowValidationError(AgentLaneError):
    """A flow definition is malformed or internally inconsistent."""


class FlowExecutionError(AgentLaneError):
    """A flow could not complete."""


class InvalidResumeError(AgentLaneError):
    """A run cannot be resumed from the requested boundary."""


class AdapterError(AgentLaneError):
    """An agent adapter is unavailable or failed before returning a result."""


class UnknownAgentError(AdapterError):
    """No command/session is registered for an agent name."""


class ResolverError(AgentLaneError):
    """A prompt reference could not be resolved."""


class StateStoreError(AgentLaneError, ValueError):
    """Persistent run state is missing or inconsistent."""
