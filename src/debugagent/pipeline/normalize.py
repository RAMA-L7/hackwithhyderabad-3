"""Deterministic normalizer (MK3, contract 2.2). No LLM call, no memory, no evidence writes.

Never invents: an environment key the engineer did not state stays None. Never overwrites: when the
text and an explicit hint disagree, it fails closed with NormalizationError (decided 2026-09-28).
"""

from __future__ import annotations

import re

from debugagent.pipeline.types import DebugInput, NormalizedDebugCase, SchemaError

# Single-domain taxonomy (C3: service/API runtime failures). Only these keys are read from free text,
# so "size=2MB" or "status: 502" stay symptoms instead of becoming environment.
TAXONOMY_KEYS = ("service", "runtime", "proxy", "region")

_KEYS = "|".join(TAXONOMY_KEYS)
_ENV_PAIR = re.compile(rf"(?<![\w.-])({_KEYS})\s*[=:]\s*([^\s,;]+)", re.I)
_ENV_ONLY_LINE = re.compile(rf"^\s*(?:(?:{_KEYS})\s*[=:]\s*[^\s,;]+[\s,;]*)+$", re.I)
_ENV_KEY = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")  # same rule as rama-m0 schemas.ENV_KEY_RE


class InputError(ValueError):
    """The engineer's input is unusable (e.g. blank description)."""


class NormalizationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _clean(value: str) -> str:
    return value.rstrip(".)")


def normalize(raw: DebugInput) -> NormalizedDebugCase:
    if not raw.description or not raw.description.strip():
        raise InputError("describe the issue: the description is empty")

    lines = [" ".join(line.split()) for line in raw.description.splitlines()]
    lines = [line for line in lines if line]
    signature = lines[0].rstrip(".")

    errors: list[str] = []
    environment: dict[str, str | None] = {key: None for key in TAXONOMY_KEYS}
    engineer_text = "\n".join([raw.description, *raw.measurements])  # both are the engineer's own input
    for key, value in _ENV_PAIR.findall(engineer_text):
        key, value = key.lower(), _clean(value)
        if environment[key] is not None and environment[key] != value:
            errors.append(f"environment.{key}: the input states both '{environment[key]}' and '{value}'")
        environment[key] = environment[key] or value

    for key, value in raw.environment_hints.items():
        key, value = key.strip().lower(), value.strip()
        if not _ENV_KEY.match(key):
            errors.append(f"environment_hints.{key}: key must match {_ENV_KEY.pattern}")
            continue
        stated = environment.get(key)
        if stated is not None and stated != value:
            errors.append(f"environment.{key}: the description says '{stated}' but the hint says '{value}'")
            continue
        environment[key] = value

    symptoms: list[str] = []
    for item in lines[1:] + [" ".join(m.split()) for m in raw.measurements]:
        if item and not _ENV_ONLY_LINE.match(item) and item not in symptoms:
            symptoms.append(item)

    if errors:
        raise NormalizationError(errors)
    try:
        return NormalizedDebugCase.from_dict({
            "problem_signature": signature,
            "symptoms": symptoms,
            "environment": environment,
            "raw_description": raw.description,
            "source_case_ids": [],
        })
    except SchemaError as exc:
        raise NormalizationError(exc.errors) from exc
