from __future__ import annotations

import os

from .base import ContextResolver, ResolveContext, ResolveResult


class EnvResolver(ContextResolver):
    prefix = "env"
    description = "Read an environment variable"

    def resolve(self, key: str, context: ResolveContext) -> ResolveResult:
        value = os.environ.get(key)
        if value is None:
            return ResolveResult(
                "", f"env:{key}", True, error=f"environment variable is missing: {key}"
            )
        return ResolveResult(value, f"env:{key}")
