from __future__ import annotations

import time

import pytest

from agentlane.core.resolvers import (
    ContextResolver,
    MemoryResolver,
    ResolveContext,
    ResolveResult,
    ResolverRegistry,
    SecretResolver,
    StepsResolver,
    default_registry,
)


def test_steps_reads_full_output():
    result = StepsResolver().resolve(
        "a", ResolveContext(step_outputs={"a": "hello"}, step_groups={"a": None})
    )
    assert result.value == "hello"


def test_steps_reads_nested_variable():
    context = ResolveContext(
        step_outputs={"a": '{"meta":{"count":2}}'},
        step_variables={"a": {"meta": {"count": 2}}},
        step_groups={"a": None},
    )
    assert StepsResolver().resolve("a.meta.count", context).value == "2"


def test_steps_missing_variable_is_visible():
    context = ResolveContext(step_outputs={"a": "text"}, step_groups={"a": None})
    result = StepsResolver().resolve("a.value", context)
    assert result.missing and "structured" in result.error


def test_group_namespace_enforced():
    context = ResolveContext(step_outputs={"a": "x"}, step_groups={"a": "review"})
    assert StepsResolver().resolve("a", context).missing
    assert StepsResolver("review").resolve("a", context).value == "x"


class FakeMemory:
    def __init__(self, records=None, results=None):
        self.records = records or {}
        self.results = results or []
        self.calls = []

    def get(self, memory_id):
        self.calls.append(("get", memory_id))
        return self.records.get(memory_id)

    def search(self, query, workspace="default"):
        self.calls.append(("search", query, workspace))
        return {"results": self.results}


def test_memory_exact_numeric_id():
    client = FakeMemory(records={7: {"id": 7, "status": "active", "content": "exact"}})
    result = MemoryResolver().resolve("get:7", ResolveContext(memory_client=client))
    assert result.value == "exact"
    assert client.calls == [("get", 7)]


def test_memory_step_alias_uses_recorded_id():
    client = FakeMemory(records={9: {"status": "active", "content": "alias"}})
    context = ResolveContext(memory_client=client, run_context={"draft.memory_id": 9})
    assert MemoryResolver().resolve("get:draft", context).value == "alias"
    assert client.calls == [("get", 9)]


def test_memory_alias_missing_is_not_search():
    client = FakeMemory(results=[{"content": "wrong"}])
    result = MemoryResolver().resolve("get:draft", ResolveContext(memory_client=client))
    assert result.missing
    assert client.calls == []


def test_memory_follows_superseded_chain():
    client = FakeMemory(
        records={
            1: {"status": "superseded", "superseded_by": 2, "content": "old"},
            2: {"status": "active", "content": "new"},
        }
    )
    result = MemoryResolver().resolve("1", ResolveContext(memory_client=client))
    assert result.value == "new"
    assert client.calls == [("get", 1), ("get", 2)]


def test_memory_detects_superseded_cycle():
    client = FakeMemory(
        records={
            1: {"status": "superseded", "superseded_by": 2},
            2: {"status": "superseded", "superseded_by": 1},
        }
    )
    result = MemoryResolver().resolve("1", ResolveContext(memory_client=client))
    assert result.missing and "cycle" in result.error


def test_memory_search_returns_first_result_and_workspace_metadata():
    client = FakeMemory(results=[{"content": "first"}, {"content": "second"}])
    context = ResolveContext(memory_client=client, workspace="project-label")
    result = MemoryResolver().resolve("topic", context)
    assert result.value == "first"
    assert client.calls == [("search", "topic", "project-label")]


def test_memory_without_client_is_missing():
    assert MemoryResolver().resolve("topic", ResolveContext()).missing


class AlternateMemory:
    def __init__(self, get_value=None, search_value=None):
        self.get_value = get_value
        self.search_value = search_value

    def memory_get(self, memory_id):
        return self.get_value

    def memory_search(self, query, workspace="default"):
        return self.search_value


def test_memory_accepts_nested_mcp_payloads_and_alternate_method_names():
    get_client = AlternateMemory(
        get_value={"data": {"memory": {"status": "active", "content": "nested"}}}
    )
    assert (
        MemoryResolver().resolve("get:3", ResolveContext(memory_client=get_client)).value
        == "nested"
    )

    search_client = AlternateMemory(search_value={"data": {"results": [{"content": "found"}]}})
    assert (
        MemoryResolver().resolve("query", ResolveContext(memory_client=search_client)).value
        == "found"
    )


@pytest.mark.parametrize("value", [None, "bad", {"results": []}])
def test_memory_empty_or_malformed_search_result_is_missing(value):
    result = MemoryResolver().resolve(
        "query", ResolveContext(memory_client=AlternateMemory(search_value=value))
    )
    assert result.missing and "no result" in result.error


def test_memory_search_accepts_direct_record_payload():
    client = AlternateMemory(search_value={"status": "active", "content": "direct"})
    assert MemoryResolver().resolve("query", ResolveContext(memory_client=client)).value == "direct"


def test_memory_not_found_and_superseded_edge_cases():
    assert (
        MemoryResolver()
        .resolve("get:1", ResolveContext(memory_client=AlternateMemory(get_value="bad")))
        .missing
    )

    metadata = FakeMemory(
        records={
            1: {"status": "superseded", "metadata": {"superseded_by": 2}},
            2: {"status": "active", "content": "latest"},
        }
    )
    assert MemoryResolver().resolve("1", ResolveContext(memory_client=metadata)).value == "latest"

    missing_successor = FakeMemory(records={1: {"status": "superseded"}})
    result = MemoryResolver().resolve("1", ResolveContext(memory_client=missing_successor))
    assert result.missing and "no successor" in result.error

    long_chain = FakeMemory(
        records={
            1: {"status": "superseded", "superseded_by": 2},
            2: {"status": "superseded", "superseded_by": 3},
            3: {"status": "active", "content": "too far"},
        }
    )
    result = MemoryResolver(max_superseded_hops=1).resolve(
        "1", ResolveContext(memory_client=long_chain)
    )
    assert result.missing and "hop limit" in result.error


def test_memory_client_missing_required_method_is_visible():
    with pytest.raises(AttributeError, match="has none"):
        MemoryResolver().resolve("query", ResolveContext(memory_client=object()))


class Secrets:
    def get(self, key):
        return {"TOKEN": "secret"}.get(key)


def test_secret_provider():
    context = ResolveContext(secret_provider=Secrets())
    assert SecretResolver().resolve("TOKEN", context).value == "secret"
    assert SecretResolver().resolve("MISSING", context).missing


def test_callable_secret_provider_and_missing_steps():
    assert (
        SecretResolver()
        .resolve("TOKEN", ResolveContext(secret_provider=lambda key: f"value-{key}"))
        .value
        == "value-TOKEN"
    )
    result = StepsResolver().resolve("missing", ResolveContext())
    assert result.missing and "no output" in result.error


@pytest.mark.asyncio
async def test_registry_renders_multiple_tokens(monkeypatch):
    monkeypatch.setenv("VISIBLE_ENV", "env-value")
    context = ResolveContext(
        step_outputs={"a": "step-value"}, step_groups={"a": None}, secret_provider=Secrets()
    )
    rendered, results = await default_registry().render(
        "{steps:a}|{secret:TOKEN}|{env:VISIBLE_ENV}", context
    )
    assert rendered == "step-value|secret|env-value"
    assert not any(result.missing for result in results)


@pytest.mark.asyncio
async def test_registry_dynamic_group_prefix():
    context = ResolveContext(step_outputs={"a": "x"}, step_groups={"a": "review"})
    rendered, results = await default_registry().render("{review.steps:a}", context)
    assert rendered == "x"
    assert results[0].source == "review.steps:a"


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["{unknown:x}", "{malformed}"])
async def test_registry_reports_unknown_tokens(token):
    rendered, results = await default_registry().render(token, ResolveContext())
    assert rendered == ""
    assert results[0].missing
    assert results[0].error


class SlowResolver(ContextResolver):
    prefix = "slow"
    description = "slow"

    def resolve(self, key, context):
        time.sleep(0.05)
        return ResolveResult("late", f"slow:{key}")


@pytest.mark.asyncio
async def test_registry_timeout_is_distinguishable():
    registry = ResolverRegistry().register(SlowResolver())
    rendered, results = await registry.render("{slow:x}", ResolveContext(), timeout=0.001)
    assert rendered == ""
    assert results[0].timed_out and results[0].missing


class ExplodingResolver(ContextResolver):
    prefix = "boom"
    description = "boom"

    def resolve(self, key, context):
        raise RuntimeError("broken")


@pytest.mark.asyncio
async def test_registry_preserves_resolver_failure_message():
    registry = ResolverRegistry().register(ExplodingResolver())
    _, results = await registry.render("{boom:x}", ResolveContext())
    assert "broken" in results[0].error


@pytest.mark.asyncio
async def test_registry_no_tokens_is_noop():
    rendered, results = await default_registry().render("plain text", ResolveContext())
    assert rendered == "plain text" and results == []


def test_resolver_timeout_does_not_delay_event_loop_shutdown():
    import asyncio

    registry = ResolverRegistry().register(SlowResolver())
    started = time.monotonic()
    rendered, results = asyncio.run(registry.render("{slow:x}", ResolveContext(), timeout=0.001))
    assert time.monotonic() - started < 0.04
    assert rendered == "" and results[0].timed_out
