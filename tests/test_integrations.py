from __future__ import annotations

import sys
from types import ModuleType

import pytest

from agentlane.core.errors import FlowExecutionError
from agentlane.core.memory_client import build_memory_client
from agentlane.core.secret_provider import EnvSecretProvider


def test_memory_client_is_optional():
    assert build_memory_client(False) is None


def test_memory_client_uses_official_in_process_types(monkeypatch):
    package = ModuleType("memory_arbiter")
    package.__path__ = []
    config = ModuleType("memory_arbiter.config")
    tools = ModuleType("memory_arbiter.tools")

    class Settings:
        @classmethod
        def from_env(cls):
            return "settings"

    class MemoryTools:
        def __init__(self, settings):
            self.settings = settings

    config.Settings = Settings
    tools.MemoryTools = MemoryTools
    monkeypatch.setitem(sys.modules, "memory_arbiter", package)
    monkeypatch.setitem(sys.modules, "memory_arbiter.config", config)
    monkeypatch.setitem(sys.modules, "memory_arbiter.tools", tools)
    assert build_memory_client(True).settings == "settings"


def test_enabled_memory_fails_loudly_when_dependency_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "memory_arbiter", None)
    monkeypatch.delitem(sys.modules, "memory_arbiter.config", raising=False)
    monkeypatch.delitem(sys.modules, "memory_arbiter.tools", raising=False)
    with pytest.raises(FlowExecutionError, match="enabled but unavailable"):
        build_memory_client(True)


def test_environment_secret_provider_supports_injection(monkeypatch):
    monkeypatch.setenv("AGENTLANE_TOKEN", "from-env")
    assert EnvSecretProvider().get("AGENTLANE_TOKEN") == "from-env"
    assert EnvSecretProvider({"TOKEN": "injected"}).get("TOKEN") == "injected"
