from __future__ import annotations

from .engine import FlowEngine
from .state import FlowDefinition, FlowRun, StepStatus


def mermaid(flow: FlowDefinition, run: FlowRun | None = None) -> str:
    lines = ["graph TD"]
    for step in flow.steps:
        label = step.id
        lines.append(f'  {step.id}["{label}"]')
        for dependency in step.depends_on:
            lines.append(f"  {dependency} --> {step.id}")
    if run is not None:
        for step_id, snapshot in run.steps.items():
            lines.append(f"  class {step_id} {snapshot.status.value}")
        lines.append("  classDef completed fill:#dcfce7,stroke:#16a34a")
        lines.append("  classDef failed fill:#fee2e2,stroke:#dc2626")
        lines.append("  classDef running fill:#dbeafe,stroke:#2563eb")
        lines.append("  classDef waiting_human fill:#fef3c7,stroke:#d97706")
    return "\n".join(lines)


def ascii_graph(flow: FlowDefinition, run: FlowRun | None = None) -> str:
    layers = FlowEngine().layered_order(flow)
    symbols = {
        StepStatus.PENDING: "○",
        StepStatus.RUNNING: "▶",
        StepStatus.WAITING_HUMAN: "⏸",
        StepStatus.COMPLETED: "✓",
        StepStatus.FAILED: "✗",
        StepStatus.SKIPPED: "–",
    }
    lines: list[str] = []
    for index, layer in enumerate(layers, 1):
        nodes = []
        for step_id in layer:
            symbol = symbols[run.steps[step_id].status] if run is not None else "○"
            nodes.append(f"{symbol} {step_id}")
        lines.append(f"Layer {index}: " + "  |  ".join(nodes))
    return "\n".join(lines)
