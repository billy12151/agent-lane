from __future__ import annotations

import pytest

import agentlane
import agentlane.adapters as public_adapters
import agentlane.engine as public_engine
import agentlane.errors as public_errors
import agentlane.models as public_models
import agentlane.observability as public_observability
import agentlane.resolvers as public_resolvers
import agentlane.runner as public_runner
import agentlane.state as public_state
import agentlane.visualize as public_visualize


def test_compatibility_modules_export_the_canonical_objects():
    assert public_engine.FlowEngine is agentlane.FlowEngine
    assert public_runner.StepRunner is agentlane.StepRunner
    assert public_errors.FlowValidationError is agentlane.FlowValidationError
    assert public_models.AgentResult is agentlane.AgentResult
    assert public_models.now is public_models.utc_now
    assert public_adapters.StaticAgentAdapter.__name__ == "StaticAgentAdapter"
    assert public_resolvers.ResolverRegistry.__name__ == "ResolverRegistry"
    assert public_observability.SummarySink.__name__ == "SummarySink"
    assert public_state.JsonFileStateStore.__name__ == "JsonFileStateStore"
    assert callable(public_visualize.ascii_graph)


@pytest.mark.asyncio
async def test_public_run_flow_helper_executes_a_flow():
    flow = agentlane.parse_flow("name: public\nsteps:\n  - {id: a, agent: demo, prompt: hello}\n")
    run = await public_engine.run_flow(
        flow, adapter=public_adapters.StaticAgentAdapter({"demo": "done"})
    )
    assert run.status == agentlane.FlowStatus.COMPLETED
    assert run.steps["a"].output == "done"


def test_public_models_cover_missing_lookup_and_empty_token_total():
    flow = agentlane.parse_flow("name: public\nsteps:\n  - {id: a, agent: x}\n")
    with pytest.raises(KeyError, match="unknown step"):
        flow.step("missing")
    assert agentlane.AgentResult.success().total_tokens is None
