from __future__ import annotations

import asyncio
import sys

import pytest

from agentlane.core.adapters import ACPAgentAdapter, ShellAgentAdapter, StaticAgentAdapter
from agentlane.core.errors import UnknownAgentError
from agentlane.core.result import AgentExitCode, AgentResult


@pytest.mark.asyncio
async def test_static_unknown_agent_fails_closed():
    with pytest.raises(UnknownAgentError):
        await StaticAgentAdapter({}).execute("missing", "prompt")


@pytest.mark.asyncio
async def test_static_sequence_advances_and_sticks_to_last():
    adapter = StaticAgentAdapter({"x": ["one", "two"]})
    assert (await adapter.execute("x", "p")).output == "one"
    assert (await adapter.execute("x", "p")).output == "two"
    assert (await adapter.execute("x", "p")).output == "two"


@pytest.mark.asyncio
async def test_static_awaits_async_callable():
    async def output(agent, prompt):
        return f"{agent}:{prompt}"

    result = await StaticAgentAdapter({"x": output}).execute("x", "hello")
    assert result.output == "x:hello"


def test_shell_unknown_agent_fails_closed():
    with pytest.raises(UnknownAgentError):
        ShellAgentAdapter({}).command_for("missing")


@pytest.mark.asyncio
async def test_shell_passes_prompt_on_stdin():
    adapter = ShellAgentAdapter(
        {"echo": [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"]}
    )
    result = await adapter.execute("echo", "hello")
    assert result.ok
    assert result.output.strip() == "HELLO"


@pytest.mark.asyncio
async def test_shell_nonzero_exit_is_failure():
    adapter = ShellAgentAdapter(
        {
            "bad": [
                sys.executable,
                "-c",
                "import sys; print('problem', file=sys.stderr); raise SystemExit(7)",
            ]
        }
    )
    result = await adapter.execute("bad", "")
    assert not result.ok and result.exit_code == 7
    assert result.error == "problem"


@pytest.mark.asyncio
async def test_shell_timeout_terminates_process():
    adapter = ShellAgentAdapter({"slow": [sys.executable, "-c", "import time; time.sleep(5)"]})
    result = await adapter.execute("slow", "", timeout=0.01)
    assert not result.ok
    assert result.exit_code == AgentExitCode.TIMEOUT
    assert "timed out" in result.error


@pytest.mark.asyncio
async def test_shell_cancellation_does_not_hang():
    adapter = ShellAgentAdapter({"slow": [sys.executable, "-c", "import time; time.sleep(5)"]})
    task = asyncio.create_task(adapter.execute("slow", ""))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_acp_normalizes_plain_value():
    async def transport(**kwargs):
        return "done"

    result = await ACPAgentAdapter(transport).execute("codex", "go")
    assert result.ok and result.output == "done"


@pytest.mark.asyncio
async def test_acp_preserves_agent_result():
    expected = AgentResult.failure("bad")

    async def transport(**kwargs):
        return expected

    assert await ACPAgentAdapter(transport).execute("codex", "go") is expected


@pytest.mark.asyncio
async def test_acp_accepts_structured_result_dict():
    async def transport(**kwargs):
        return {"ok": True, "output": "structured", "input_tokens": 2}

    result = await ACPAgentAdapter(transport).execute("codex", "go")
    assert result.output == "structured" and result.input_tokens == 2


@pytest.mark.asyncio
async def test_acp_timeout_is_normalized():
    async def transport(**kwargs):
        await asyncio.sleep(1)

    result = await ACPAgentAdapter(transport).execute("codex", "go", timeout=0.01)
    assert result.exit_code == AgentExitCode.TIMEOUT


def test_acp_requires_transport():
    with pytest.raises(ValueError, match="transport"):
        ACPAgentAdapter(None)
