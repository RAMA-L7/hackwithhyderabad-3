"""Dependency-free JSON Schema subset validator used to fail closed on structured output."""

from __future__ import annotations

from typing import Any

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    py_type = _TYPES.get(expected)
    if py_type is None:
        return True
    if py_type is dict or py_type is list or py_type is str:
        return isinstance(value, py_type)
    return isinstance(value, py_type)


def validate(instance: Any, schema: dict, path: str = "$") -> list[str]:
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    expected = schema.get("type")
    if isinstance(expected, str) and not _type_ok(instance, expected):
        return [f"{path}: expected type {expected}, got {type(instance).__name__}"]

    if "enum" in schema and isinstance(schema["enum"], list):
        if instance not in schema["enum"]:
            errors.append(f"{path}: value not in enum")

    if isinstance(instance, str) and "minLength" in schema:
        if len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum")

    if isinstance(instance, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(validate(item, item_schema, f"{path}[{index}]"))

    if isinstance(instance, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        for key in schema.get("required", []) or []:
            if key not in instance:
                errors.append(f"{path}.{key}: required property missing")
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}.{key}: additional property not allowed")
        for key, value in instance.items():
            sub_schema = properties.get(key)
            if isinstance(sub_schema, dict):
                errors.extend(validate(value, sub_schema, f"{path}.{key}"))

    return errors
