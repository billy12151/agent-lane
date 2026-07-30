from __future__ import annotations

from .base import ContextResolver, ResolveContext, ResolveResult


class SecretResolver(ContextResolver):
    prefix = "secret"
    description = "Read a secret from the configured provider"

    def resolve(self, key: str, context: ResolveContext) -> ResolveResult:
        provider = context.secret_provider
        if provider is None:
            return ResolveResult(
                "", f"secret:{key}", True, error="secret provider is not configured"
            )
        value = provider.get(key) if hasattr(provider, "get") else provider(key)
        if value is None or value == "":
            return ResolveResult("", f"secret:{key}", True, error=f"secret is missing: {key}")
        return ResolveResult(str(value), f"secret:{key}")
