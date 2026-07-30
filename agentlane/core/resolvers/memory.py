"""memory-arbiter search and exact step-memory lookup."""

from __future__ import annotations

from typing import Any

from .base import ContextResolver, ResolveContext, ResolveResult


def _call(client: Any, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
    for name in names:
        method = getattr(client, name, None)
        if method is not None:
            return method(*args, **kwargs)
    raise AttributeError(f"memory client has none of: {', '.join(names)}")


def _memory_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if "content" in value or "status" in value:
        return value
    data = value.get("data")
    if isinstance(data, dict):
        record = data.get("memory")
        if isinstance(record, dict):
            return record
    return None


def _first_search_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    results = value.get("results")
    if results is None and isinstance(value.get("data"), dict):
        results = value["data"].get("results")
    if isinstance(results, list) and results and isinstance(results[0], dict):
        return results[0]
    return _memory_record(value)


class MemoryResolver(ContextResolver):
    prefix = "memory"
    description = "Search memory or read an exact memory id/step alias"

    def __init__(self, max_superseded_hops: int = 5):
        self.max_superseded_hops = max_superseded_hops

    def resolve(self, key: str, context: ResolveContext) -> ResolveResult:
        if context.memory_client is None:
            return ResolveResult("", f"memory:{key}", True, error="memory client is not configured")
        if key.startswith("get:"):
            return self._get(key[4:], context)
        if key.isdigit():
            return self._get(key, context)
        value = _call(
            context.memory_client,
            ("search", "memory_search"),
            key,
            workspace=context.workspace,
        )
        record = _first_search_result(value)
        if record is None:
            return ResolveResult(
                "", f"memory:{key}", True, error=f"memory search had no result: {key}"
            )
        return ResolveResult(str(record.get("content", "")), f"memory:{key}")

    def _get(self, key: str, context: ResolveContext) -> ResolveResult:
        memory_id: int | None
        if key.isdigit():
            memory_id = int(key)
        else:
            raw = context.run_context.get(f"{key}.memory_id")
            memory_id = int(raw) if isinstance(raw, (int, str)) and str(raw).isdigit() else None
        if memory_id is None:
            return ResolveResult(
                "", f"memory:get:{key}", True, error=f"step has no memory id: {key}"
            )

        seen: set[int] = set()
        for _ in range(self.max_superseded_hops + 1):
            if memory_id in seen:
                return ResolveResult(
                    "", f"memory:get:{key}", True, error="superseded memory cycle detected"
                )
            seen.add(memory_id)
            value = _call(context.memory_client, ("get", "memory_get"), memory_id)
            record = _memory_record(value)
            if record is None:
                return ResolveResult(
                    "", f"memory:get:{key}", True, error=f"memory not found: {memory_id}"
                )
            if record.get("status") != "superseded":
                return ResolveResult(str(record.get("content", "")), f"memory:get:{key}")
            next_id = record.get("superseded_by")
            if next_id is None and isinstance(record.get("metadata"), dict):
                next_id = record["metadata"].get("superseded_by")
            if not isinstance(next_id, int):
                return ResolveResult(
                    "", f"memory:get:{key}", True, error="superseded memory has no successor"
                )
            memory_id = next_id
        return ResolveResult(
            "", f"memory:get:{key}", True, error="superseded chain exceeds hop limit"
        )
