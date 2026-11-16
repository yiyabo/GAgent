"""Double-encoded tool arguments are unwrapped once, not rejected in a loop."""

from __future__ import annotations

import json

from app.services.deep_think.native_validation import (
    unwrap_raw_arguments,
    validate,
)

SCHEMA = {
    "type": "object",
    "properties": {"operation": {"type": "string"}, "title": {"type": "string"}},
    "required": ["operation"],
}


def test_double_encoded_arguments_are_unwrapped() -> None:
    arguments = {"_raw": json.dumps({"operation": "create", "title": "计划"})}
    assert unwrap_raw_arguments(arguments, SCHEMA) == {"operation": "create", "title": "计划"}
    assert validate(arguments, SCHEMA) is None


def test_unwrap_happens_before_required_field_checks() -> None:
    arguments = {"_raw": json.dumps({"title": "缺 operation"})}
    error = validate(arguments, SCHEMA)
    assert error is not None
    assert error["error_code"] == "missing_required"
    assert error["missing_fields"] == ["operation"]


def test_unparseable_raw_arguments_get_an_actionable_hint() -> None:
    error = validate({"_raw": "{not json"}, SCHEMA)
    assert error is not None
    assert error["error_code"] == "invalid_json"
    assert "not a JSON-encoded string" in error["hint"]
    assert "do not encode it twice" in error["hint"]


def test_non_object_raw_payload_is_not_silently_accepted() -> None:
    raw = json.dumps(["create"])
    assert unwrap_raw_arguments({"_raw": raw}, SCHEMA) == {"_raw": raw}
    error = validate({"_raw": raw}, SCHEMA)
    assert error is not None and error["error_code"] == "invalid_json"


def test_other_fields_or_a_declared_raw_parameter_are_untouched() -> None:
    mixed = {"_raw": json.dumps({"operation": "create"}), "operation": "create"}
    assert unwrap_raw_arguments(mixed, SCHEMA) is mixed
    declared = {"type": "object", "properties": {"_raw": {"type": "string"}}}
    arguments = {"_raw": "literal text"}
    assert unwrap_raw_arguments(arguments, declared) is arguments
    assert validate(arguments, declared) is None
