"""Provider settings: routes from the environment and the per-provider request options.

Every value here was measured in MK0 (docs/phase1-mukul-m0-plan.md section 6):
- without reasoning control both models reason until max_tokens and return no JSON;
- Baseten ignores `reasoning_effort`, it needs `chat_template_kwargs.thinking = false`;
- Space Bunny (OpenRouter) rejects strict routing with 404, so `require_parameters` is never sent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from debugagent.domain.errors import LLMConfigError

MAX_TOKENS = 1500  # ~550 needed with reasoning controlled (MK0 run 061541Z)
DEFAULT_TIMEOUT_S = 30.0
PROVIDER_OPTIONS = {
    "baseten": {"chat_template_kwargs": {"thinking": False}},
    "openrouter": {"reasoning": {"effort": "low"}},
}
ROLES = ("PRIMARY", "FALLBACK")
FIELDS = ("PROVIDER", "MODEL", "BASE_URL", "API_KEY")


@dataclass(frozen=True)
class Route:
    role: str
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    timeout_s: float = DEFAULT_TIMEOUT_S


def _timeout(env: Mapping[str, str], role: str) -> float:
    raw = env.get(f"LLM_{role}_TIMEOUT_S") or env.get("LLM_TIMEOUT_S") or str(DEFAULT_TIMEOUT_S)
    try:
        return float(raw)
    except ValueError:
        raise LLMConfigError(f"timeout {raw!r} for {role} is not a number", error_class="CONFIG") from None


def load_routes(env: Mapping[str, str] | None = None) -> tuple[Route, Route]:
    """Primary and fallback routes. Every missing setting is listed at once; key values never appear."""
    env = os.environ if env is None else env
    routes, missing = [], []
    for role in ROLES:
        names = {f: f"LLM_{role}_{f}" for f in FIELDS}
        values = {f: env.get(n, "").strip() for f, n in names.items()}
        missing += [names[f] for f, v in values.items() if not v]
        if values["PROVIDER"] and values["PROVIDER"] not in PROVIDER_OPTIONS:
            raise LLMConfigError(f"{names['PROVIDER']}={values['PROVIDER']!r} is not one of {sorted(PROVIDER_OPTIONS)}",
                                 error_class="CONFIG")
        routes.append(Route(role.lower(), values["PROVIDER"], values["MODEL"], values["BASE_URL"].rstrip("/"),
                            values["API_KEY"], _timeout(env, role)))
    if missing:
        raise LLMConfigError(f"missing LLM settings: {', '.join(missing)}", error_class="CONFIG")
    return routes[0], routes[1]


def load_retries(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    raw = env.get("LLM_MAX_RETRIES_PRIMARY") or "1"
    if not raw.isdigit():
        raise LLMConfigError(f"LLM_MAX_RETRIES_PRIMARY={raw!r} is not a whole number", error_class="CONFIG")
    return int(raw)
