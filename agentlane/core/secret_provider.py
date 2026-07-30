"""Secret-provider implementations used by standalone execution."""

from __future__ import annotations

import os
from collections.abc import Mapping


class EnvSecretProvider:
    """Read secrets from an injected mapping or the process environment."""

    def __init__(self, values: Mapping[str, str] | None = None):
        self._values = values if values is not None else os.environ

    def get(self, key: str) -> str | None:
        return self._values.get(key)
