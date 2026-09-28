"""Reading provider responses: status classes, JSON extraction, local schema validation.

Response shapes handled here were all observed in MK0 (S5): reasoning in separate fields, content as a
list of parts, `content: null` or whitespace-only padding, truncation at max_tokens, ```json fences.
"""

from __future__ import annotations

import json
import re
from typing import Any

FAILOVER_CLASSES = frozenset({"TIMEOUT", "UNREACHABLE", "RATE_LIMITED", "UNAVAILABLE", "BAD_RESPONSE"})
AUTH_CLASSES = frozenset({"AUTH"})
INVALID_OUTPUT = "INVALID_OUTPUT"
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)
_STATUS_CLASSES = {400: "BAD_REQUEST", 402: "BILLING", 404: "MODEL_NOT_FOUND", 408: "TIMEOUT", 429: "RATE_LIMITED"}
_JSON_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def classify_status(status: int, body: Any) -> str | None:
    """None means a usable 200; anything else is an error class."""
    if status == 200:
        return "BAD_RESPONSE" if not isinstance(body, dict) or body.get("error") else None
    if status in (401, 403):
        return "AUTH"
    return _STATUS_CLASSES.get(status, "UNAVAILABLE" if status >= 500 else "CLIENT_ERROR")


def _content(choice: dict) -> str | None:
    content = (choice.get("message") or {}).get("content")  # reasoning fields are ignored on purpose
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else None


def wrap_bare_array(value, schema: dict | None):
    """A hint-mode model sometimes returns the list without its single required wrapper key
    (flow C, 2026-09-28). Wrap it back; full validation still runs afterwards."""
    required = (schema or {}).get("required", [])
    props = (schema or {}).get("properties", {})
    if isinstance(value, list) and len(required) == 1 and props.get(required[0], {}).get("type") == "array":
        return {required[0]: value}
    return value


def extract_json(body: dict, schema: dict | None = None) -> tuple[dict | None, str]:
    """(data, problem): data is the JSON object in the first choice, or None with the reason."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "no choices"
    if choices[0].get("finish_reason") == "length":
        return None, "truncated at max_tokens"
    content = _content(choices[0])
    if not content or not content.strip():
        return None, "empty content"
    text = content.strip()
    fence = _FENCE.match(text)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, "content is not JSON"
    data = wrap_bare_array(data, schema)
    if not isinstance(data, dict):
        return None, "JSON root is not an object"
    return data, ""


def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    """The JSON Schema subset our response schemas use: type, required, properties,
    additionalProperties=false, items, enum, minItems/maxItems, minLength."""
    kind = schema.get("type")
    if kind in _JSON_TYPES and not isinstance(value, _JSON_TYPES[kind]):
        return [f"{path}: expected {kind}"]
    errors: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: not one of {schema['enum']}")
    if kind == "string" and len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: shorter than minLength")
    if kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value)):
            errors.append(f"{path}: item count out of range")
        for i, item in enumerate(value):
            errors += validate(item, schema.get("items", {}), f"{path}[{i}]")
    if kind == "object":
        props = schema.get("properties", {})
        errors += [f"{path}.{k}: missing" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"{path}.{k}: not allowed" for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                errors += validate(v, props[k], f"{path}.{k}")
    return errors
