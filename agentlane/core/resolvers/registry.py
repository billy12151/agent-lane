"""Reference parser and timeout-aware resolver dispatch."""

from __future__ import annotations

import asyncio
import re

from ..async_utils import run_sync_with_timeout
from .base import ContextResolver, ResolveContext, ResolveResult
from .env import EnvResolver
from .memory import MemoryResolver
from .secret import SecretResolver
from .steps import StepsResolver

_TOKEN = re.compile(r"\{([^{}]+)\}")


class ResolverRegistry:
    def __init__(self):
        self._resolvers: dict[str, ContextResolver] = {}

    def register(self, resolver: ContextResolver) -> ResolverRegistry:
        self._resolvers[resolver.prefix] = resolver
        return self

    @property
    def prefixes(self) -> tuple[str, ...]:
        return tuple(sorted(self._resolvers))

    def resolver_for(self, prefix: str) -> ContextResolver | None:
        if prefix.endswith(".steps"):
            group = prefix[: -len(".steps")]
            return StepsResolver(group=group) if group else None
        return self._resolvers.get(prefix)

    async def resolve_token(
        self,
        token: str,
        context: ResolveContext,
        *,
        timeout: float = 10,
    ) -> ResolveResult:
        if ":" not in token:
            return ResolveResult("", token, True, error="reference has no prefix")
        prefix, key = token.split(":", 1)
        resolver = self.resolver_for(prefix)
        if resolver is None:
            return ResolveResult("", token, True, error=f"unknown resolver prefix: {prefix}")
        try:
            return await run_sync_with_timeout(resolver.resolve, key, context, timeout=timeout)
        except asyncio.TimeoutError:
            return ResolveResult(
                "", token, True, timed_out=True, error=f"resolver timed out: {prefix}"
            )
        except Exception as exc:
            # Never echo raw exception text for secret references: a custom
            # provider that embeds the secret value in its error would otherwise
            # be written to the observability log via on_resolver_missing.
            if prefix == "secret":
                error = f"secret resolver failed for {key}"
            else:
                error = f"resolver failed: {exc}"
            return ResolveResult("", token, True, error=error)

    async def render(
        self,
        text: str,
        context: ResolveContext,
        *,
        timeout: float = 10,
    ) -> tuple[str, list[ResolveResult]]:
        matches = list(_TOKEN.finditer(text))
        if not matches:
            return text, []
        results = await asyncio.gather(
            *(self.resolve_token(match.group(1), context, timeout=timeout) for match in matches)
        )
        parts: list[str] = []
        cursor = 0
        for match, result in zip(matches, results, strict=True):
            parts.append(text[cursor : match.start()])
            parts.append(result.value)
            cursor = match.end()
        parts.append(text[cursor:])
        return "".join(parts), list(results)


def default_registry() -> ResolverRegistry:
    return (
        ResolverRegistry()
        .register(StepsResolver())
        .register(MemoryResolver())
        .register(SecretResolver())
        .register(EnvResolver())
    )
