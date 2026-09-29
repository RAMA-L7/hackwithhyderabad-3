"""Structured issue input: a JSON or YAML file mapped onto DebugInput. Ingestion only.

The file carries what an engineer would otherwise type at the first two prompts. It produces the same
DebugInput as typing does, so normalization, memory, matching, reasoning and verification are unchanged.

    description: |              # required; the first line is the short summary
      media-uploader resets connections on uploads over 2 MB behind nginx
    measurements:               # optional: a list of strings, or one string
      - 20/20 uploads of 2.5MB fail with ECONNRESET
    environment:                # optional: key -> value, becomes DebugInput.environment_hints
      service: media-uploader
      runtime: node20

JSON uses the same three keys. YAML needs PyYAML (not a project dependency); JSON needs nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from debugagent.pipeline.normalize import InputError
from debugagent.pipeline.types import DebugInput, SchemaError

FIELDS = ("description", "measurements", "environment")
FORMATS = {".json": "json", ".yaml": "yaml", ".yml": "yaml"}


def load_debug_input(path: str | Path) -> DebugInput:
    """Read one issue file. Every problem is an InputError with a one-line, fixable message."""
    path = Path(path)
    kind = FORMATS.get(path.suffix.lower())
    if kind is None:
        raise InputError(f"unsupported input file '{path.name}': use .json, .yaml or .yml")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise InputError(f"input file not found: {path}") from None
    except OSError as exc:
        raise InputError(f"cannot read input file {path}: {exc.strerror}") from None
    return to_debug_input(_parse(text, kind, path.name), source=path.name)


def _parse(text: str, kind: str, name: str) -> Any:
    if kind == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputError(f"{name}: not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})") from None
    try:
        import yaml  # optional: only YAML input needs it
    except ImportError:
        raise InputError("YAML input needs PyYAML: pip install pyyaml, or use a .json file") from None
    try:
        return yaml.safe_load(text)  # safe_load: plain data only, never constructs Python objects
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise InputError(f"{name}: not valid YAML{where}") from None


def _text(value: Any, label: str, errors: list[str]) -> str | None:
    """Plain text, or a number written without quotes (YAML reads `runtime: 3.11` as a float)."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    errors.append(f"{label}: expected text, got {'nothing' if value is None else type(value).__name__}")
    return None


def to_debug_input(data: Any, *, source: str = "input") -> DebugInput:
    """Map parsed JSON/YAML onto DebugInput. Unknown fields are rejected, never ignored."""
    if not isinstance(data, dict):
        raise InputError(f"{source}: expected a mapping with 'description' at the top level")
    errors: list[str] = []
    unknown = sorted(str(k) for k in data if k not in FIELDS)
    if unknown:
        errors.append(f"unknown field(s) {unknown}; allowed: {', '.join(FIELDS)}")

    description = _text(data.get("description"), "description", errors) if "description" in data else None
    if not description:
        errors.append("description: required, the issue in words (first line = short summary)")

    raw_measurements = data.get("measurements") or []
    if isinstance(raw_measurements, (str, int, float)) and not isinstance(raw_measurements, bool):
        raw_measurements = [raw_measurements]
    measurements: list[str] = []
    if not isinstance(raw_measurements, list):
        errors.append("measurements: expected a list of strings or one string")
    else:
        for i, item in enumerate(raw_measurements):
            value = _text(item, f"measurements[{i}]", errors)
            if value:
                measurements.append(value)

    raw_environment = data.get("environment") or {}
    environment: dict[str, str] = {}
    if not isinstance(raw_environment, dict):
        errors.append("environment: expected key: value pairs")
    else:
        for key, item in raw_environment.items():
            value = _text(item, f"environment.{key}", errors)
            if not isinstance(key, str) or not key.strip():
                errors.append(f"environment: keys must be text, got {key!r}")
            elif value:
                environment[key.strip()] = value

    if errors:
        raise InputError(f"{source}: " + "; ".join(errors))
    try:
        return DebugInput.from_dict({"description": description, "measurements": measurements,
                                     "environment_hints": environment})
    except SchemaError as exc:
        raise InputError(f"{source}: {exc}") from None
