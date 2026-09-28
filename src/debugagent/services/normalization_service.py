"""Normalization service (MK3, contract 2.2): DebugInput -> NormalizedDebugCase. Deterministic; no LLM.

Never invents: an environment key the engineer did not state stays None. Never overwrites: when the
text and an explicit hint disagree, it fails closed with NormalizationError (decided 2026-09-28).
"""

from __future__ import annotations

import re

from debugagent.domain.errors import InputError, NormalizationError, SchemaError
from debugagent.domain.models import DebugInput, NormalizedDebugCase

# Single-domain taxonomy (C3: service/API runtime failures). Only these keys are read from free text,
# so "size=2MB" or "status: 502" stay symptoms instead of becoming environment.
TAXONOMY_KEYS = ("service", "runtime", "proxy", "region")

_KEYS = "|".join(TAXONOMY_KEYS)
_ENV_PAIR = re.compile(rf"(?<![\w.-])({_KEYS})\s*[=:]\s*([^\s,;]+)", re.I)
_ENV_ONLY_LINE = re.compile(rf"^\s*(?:(?:{_KEYS})\s*[=:]\s*[^\s,;]+[\s,;]*)+$", re.I)
ENV_KEY = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")  # same rule as rama-m0 schemas.ENV_KEY_RE


class NormalizationService:
    def __init__(self, taxonomy_keys: tuple[str, ...] = TAXONOMY_KEYS):
        self.taxonomy_keys = taxonomy_keys

    def normalize(self, raw: DebugInput) -> NormalizedDebugCase:
        if not raw.description or not raw.description.strip():
            raise InputError("describe the issue: the description is empty")
        lines = [line for line in (" ".join(raw_line.split()) for raw_line in raw.description.splitlines()) if line]
        errors: list[str] = []
        environment = self._environment(raw, errors)
        if errors:
            raise NormalizationError(errors)
        try:
            return NormalizedDebugCase.from_dict({
                "problem_signature": lines[0].rstrip("."),
                "symptoms": self._symptoms(lines[1:], raw.measurements),
                "environment": environment,
                "raw_description": raw.description,
                "source_case_ids": [],
            })
        except SchemaError as exc:
            raise NormalizationError(exc.errors) from exc

    def _environment(self, raw: DebugInput, errors: list[str]) -> dict[str, str | None]:
        environment: dict[str, str | None] = {key: None for key in self.taxonomy_keys}
        for key, value in _ENV_PAIR.findall(raw.description):
            key, value = key.lower(), value.rstrip(".)")
            if environment[key] is not None and environment[key] != value:
                errors.append(f"environment.{key}: the description states both '{environment[key]}' and '{value}'")
            environment[key] = environment[key] or value
        for key, value in raw.environment_hints.items():
            key, value = key.strip().lower(), value.strip()
            if not ENV_KEY.match(key):
                errors.append(f"environment_hints.{key}: key must match {ENV_KEY.pattern}")
            elif environment.get(key) is not None and environment[key] != value:
                errors.append(f"environment.{key}: the description says '{environment[key]}' but the hint says '{value}'")
            else:
                environment[key] = value
        return environment

    @staticmethod
    def _symptoms(description_lines: list[str], measurements: list[str]) -> list[str]:
        symptoms: list[str] = []
        for item in description_lines + [" ".join(m.split()) for m in measurements]:
            if item and not _ENV_ONLY_LINE.match(item) and item not in symptoms:
                symptoms.append(item)
        return symptoms
