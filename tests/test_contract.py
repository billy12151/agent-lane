from __future__ import annotations

import pytest

from agentlane.core.contract import StepOutputContract
from agentlane.core.result import AgentResult


@pytest.mark.parametrize("format_name", ["text", "json", "markdown"])
def test_supported_contract_definitions(format_name):
    assert StepOutputContract(format_name).validate_definition() == []


def test_rejects_unknown_format():
    assert "unsupported" in StepOutputContract("xml").validate_definition()[0]


def test_schema_only_allowed_for_json():
    assert StepOutputContract("text", {"x": "string"}).validate_definition()


@pytest.mark.parametrize(
    "field_type", ["string", "integer", "number", "boolean", "object", "array", "null"]
)
def test_supported_schema_types(field_type):
    assert StepOutputContract("json", {"x": field_type}).validate_definition() == []


def test_unknown_schema_type_is_rejected():
    assert StepOutputContract("json", {"x": "uuid"}).validate_definition()


def test_text_contract_rejects_non_text():
    assert StepOutputContract("text").validate(AgentResult.success({})) == ["output must be text"]


@pytest.mark.parametrize("value", ["", "  ", "\n"])
def test_markdown_rejects_empty(value):
    assert StepOutputContract("markdown").validate(AgentResult.success(value))


def test_json_rejects_invalid_text():
    assert StepOutputContract("json").validate(AgentResult.success("{")) == [
        "output is not valid JSON"
    ]


def test_json_requires_object():
    assert StepOutputContract("json").validate(AgentResult.success("[]")) == [
        "JSON output must be an object"
    ]


@pytest.mark.parametrize(
    "field_type,value",
    [
        ("string", "x"),
        ("integer", 1),
        ("number", 1.2),
        ("boolean", True),
        ("object", {}),
        ("array", []),
        ("null", None),
    ],
)
def test_json_schema_accepts_matching_types(field_type, value):
    contract = StepOutputContract("json", {"value": field_type})
    assert contract.validate(AgentResult.success("", parsed={"value": value})) == []


def test_json_schema_reports_missing_and_wrong_fields():
    contract = StepOutputContract("json", {"name": "string", "count": "integer"})
    errors = contract.validate(AgentResult.success("", parsed={"count": True}))
    assert errors == ["missing field: name", "count must be integer"]


def test_json_contract_accepts_direct_structured_output():
    contract = StepOutputContract("json", {"value": "integer"})
    assert contract.validate(AgentResult.success({"value": 2})) == []
