from __future__ import annotations

import pytest

from agentlane.core.engine import parse_flow


@pytest.fixture
def linear_yaml() -> str:
    return """name: linear
version: 1
defaults:
  timeout: 10
  retry: 1
  max_visits: 3
steps:
  - id: first
    agent: alpha
    prompt: create
  - id: second
    agent: beta
    prompt: "review {steps:first}"
    depends_on: [first]
"""


@pytest.fixture
def linear_flow(linear_yaml):
    return parse_flow(linear_yaml)
