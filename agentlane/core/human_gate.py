"""Human-gate drivers shared by the core and CLI."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .state import GateOption, StepDefinition


class GateDriver(ABC):
    @abstractmethod
    async def ask(self, step: StepDefinition) -> GateOption | None:
        """Return a configured option or None to pause the run."""


class PauseGateDriver(GateDriver):
    async def ask(self, step: StepDefinition) -> GateOption | None:
        return None


class PresetGateDriver(GateDriver):
    def __init__(self, choices: dict[str, str]):
        self.choices = dict(choices)

    async def ask(self, step: StepDefinition) -> GateOption | None:
        label = self.choices.get(step.id)
        if label is None:
            return None
        for option in step.options:
            if option.label == label:
                return option
        raise ValueError(f"gate {step.id} has no option labelled {label}")
