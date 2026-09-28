"""LLM router: primary -> fallback, fail-closed structured output (MK4).

Every behaviour here was measured in MK0 (docs/phase1-mukul-m0-plan.md section 6):
- both models reason until max_tokens and return no JSON unless reasoning is controlled per provider;
- Space Bunny (OpenRouter) rejects strict routing with 404, so `require_parameters` is never sent;
- failover only on timeout / unreachable / 429 / 5xx / 200-with-error; auth and config errors never fail over.

Local validation is the only conformance guarantee: nothing unvalidated is ever returned.
Provider-specific details live in this module only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

log = logging.getLogger("debugagent.llm")
log.addHandler(logging.NullHandler())  # the CLI configures real handlers

MAX_TOKENS = 1500  # ~550 needed with reasoning controlled (MK0 run 061541Z); 3000 was spent on reasoning
PROVIDER_OPTIONS = {
    "baseten": {"chat_template_kwargs": {"thinking": False}},
    "openrouter": {"reasoning": {"effort": "low"}},
}
FAILOVER_CLASSES = {"TIMEOUT", "UNREACHABLE", "RATE_LIMITED", "UNAVAILABLE", "BAD_RESPONSE"}
AUTH_CLASSES = {"AUTH"}
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


class LLMError(RuntimeError):
    def __init__(self, message: str, *, error_class: str, attempts: list[dict] | None = None):
        super().__init__(message)
        self.error_class = error_class
        self.attempts = list(attempts or [])


class LLMConfigError(LLMError):
    """Bad request, unknown model, billing, or local misconfiguration. Never fails over."""


class LLMAuthError(LLMError):
    """Credentials rejected. Never fails over."""


class LLMUnavailable(LLMError):
    """No route could be reached or served a response."""


class StructuredOutputError(LLMError):
    """Routes answered, but no output passed local validation."""


@dataclass(frozen=True)
class Route:
    role: str
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    timeout_s: float = 30.0


@dataclass(frozen=True)
class StructuredResult:
    data: dict
    provider: str
    model: str
    fallback_used: bool
    attempts: list[dict]


Transport = Callable[[str, str, dict, float], "tuple[int, str]"]


def http_post(url: str, api_key: str, payload: dict, timeout_s: float) -> tuple[int, str]:
    """Stdlib transport. Returns (status, body); raises TimeoutError / OSError when nothing came back."""
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError("request timed out") from exc
        raise


def classify_status(status: int, body: Any) -> str | None:
    """None means a usable 200; everything else is an error class."""
    if status == 200:
        return "BAD_RESPONSE" if not isinstance(body, dict) or body.get("error") else None
    if status in (401, 403):
        return "AUTH"
    mapped = {400: "BAD_REQUEST", 402: "BILLING", 404: "MODEL_NOT_FOUND", 408: "TIMEOUT", 429: "RATE_LIMITED"}
    return mapped.get(status, "UNAVAILABLE" if status >= 500 else "CLIENT_ERROR")


def wrap_bare_array(value, schema: dict | None):
    """A hint-mode model sometimes returns the list without its single required wrapper key (live flow
    test, 2026-09-28: Space Bunny). Wrap it back; full schema validation still runs afterwards."""
    required = (schema or {}).get("required", [])
    props = (schema or {}).get("properties", {})
    if isinstance(value, list) and len(required) == 1 and props.get(required[0], {}).get("type") == "array":
        return {required[0]: value}
    return value


def extract_json(body: dict, schema: dict | None = None) -> tuple[dict | None, str]:
    """Pull the JSON object out of a chat completion. Returns (data, problem); data is None when invalid."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "no choices"
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        return None, "truncated at max_tokens"
    content = (choice.get("message") or {}).get("content")  # reasoning fields are ignored on purpose
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
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


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    """The JSON Schema subset our response schemas use: type, required, properties,
    additionalProperties=false, items, enum, minItems/maxItems, minLength."""
    kind = schema.get("type")
    if kind in _TYPES and not isinstance(value, _TYPES[kind]):
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


def load_routes(env: Mapping[str, str] | None = None) -> tuple[Route, Route]:
    env = os.environ if env is None else env
    routes, missing = [], []
    for role in ("PRIMARY", "FALLBACK"):
        names = {f: f"LLM_{role}_{f}" for f in ("PROVIDER", "MODEL", "BASE_URL", "API_KEY")}
        missing += [n for n in names.values() if not env.get(n, "").strip()]
        provider = env.get(names["PROVIDER"], "").strip()
        if provider and provider not in PROVIDER_OPTIONS:
            raise LLMConfigError(f"{names['PROVIDER']}={provider!r} is not one of {sorted(PROVIDER_OPTIONS)}",
                                 error_class="CONFIG")
        timeout = env.get(f"LLM_{role}_TIMEOUT_S") or env.get("LLM_TIMEOUT_S") or "30"
        try:
            timeout_s = float(timeout)
        except ValueError:
            raise LLMConfigError(f"timeout {timeout!r} for {role} is not a number", error_class="CONFIG")
        routes.append(Route(role.lower(), provider, env.get(names["MODEL"], "").strip(),
                            env.get(names["BASE_URL"], "").strip().rstrip("/"),
                            env.get(names["API_KEY"], "").strip(), timeout_s))
    if missing:
        raise LLMConfigError(f"missing LLM settings: {', '.join(missing)}", error_class="CONFIG")
    return routes[0], routes[1]


class LLMRouter:
    def __init__(self, primary: Route, fallback: Route, *, transport: Transport = http_post,
                 retries_on_invalid: int = 1, max_tokens: int = MAX_TOKENS):
        self.primary = primary
        self.fallback = fallback
        self.transport = transport
        self.retries_on_invalid = retries_on_invalid
        self.max_tokens = max_tokens

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kwargs) -> "LLMRouter":
        env = os.environ if env is None else env
        primary, fallback = load_routes(env)
        retries = env.get("LLM_MAX_RETRIES_PRIMARY") or "1"
        if not retries.isdigit():
            raise LLMConfigError(f"LLM_MAX_RETRIES_PRIMARY={retries!r} is not a whole number", error_class="CONFIG")
        return cls(primary, fallback, retries_on_invalid=int(retries), **kwargs)

    def complete_structured(self, prompt: str, *, schema: dict, name: str = "response",
                            check: Callable[[dict], list[str]] | None = None) -> StructuredResult:
        """Return validated JSON, or raise. `check` adds caller rules (e.g. citation integrity)
        whose failure counts as invalid output: retried, then failed over, never returned."""
        attempts: list[dict] = []
        # both routes get the same single retry on invalid output (live flow test 2026-09-28: with the
        # primary down, one bad fallback answer ended the session)
        tries = 1 + self.retries_on_invalid
        plan = [(self.primary, tries), (self.fallback, tries)]
        for index, (route, route_tries) in enumerate(plan):
            for _ in range(route_tries):
                data, record = self._attempt(route, prompt, schema, name, check)
                attempts.append(record)
                if data is not None:
                    return StructuredResult(data, route.provider, route.model, index > 0, attempts)
                error_class = record["error_class"]
                if error_class in AUTH_CLASSES:
                    raise LLMAuthError(f"{route.role} ({route.provider}) rejected the credentials",
                                       error_class=error_class, attempts=attempts)
                if error_class not in FAILOVER_CLASSES and error_class != "INVALID_OUTPUT":
                    raise LLMConfigError(f"{route.role} ({route.provider}) refused the request: {error_class}",
                                         error_class=error_class, attempts=attempts)
                if error_class in FAILOVER_CLASSES:
                    break  # transport-level failure: go straight to the next route
        if any(a["error_class"] == "INVALID_OUTPUT" for a in attempts):
            raise StructuredOutputError("no route produced output that passed validation",
                                        error_class="INVALID_OUTPUT", attempts=attempts)
        raise LLMUnavailable("no LLM route could serve the request",
                             error_class=attempts[-1]["error_class"], attempts=attempts)

    def _attempt(self, route: Route, prompt: str, schema: dict, name: str,
                 check: Callable[[dict], list[str]] | None) -> tuple[dict | None, dict]:
        payload = {
            "model": route.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
            **PROVIDER_OPTIONS[route.provider],
        }
        record = {"route": route.role, "provider": route.provider, "model": route.model}
        started = time.perf_counter()
        data, error_class, detail = None, None, ""
        try:
            status, text = self.transport(f"{route.base_url}/chat/completions", route.api_key, payload, route.timeout_s)
        except TimeoutError:
            error_class, detail = "TIMEOUT", f"no response within {route.timeout_s:g}s"
        except OSError as exc:
            error_class, detail = "UNREACHABLE", type(exc).__name__
        else:
            try:
                body = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                body = None
            error_class = classify_status(status, body)
            record["status"] = status
            if error_class:
                detail = (text or "")[:200]
            else:
                data, detail = extract_json(body, schema)
                problems = ([detail] if data is None else validate(data, schema)) or (check(data) if check else [])
                if problems:
                    data, error_class, detail = None, "INVALID_OUTPUT", "; ".join(problems)[:300]
        record.update(outcome="served" if data is not None else "error", error_class=error_class,
                      detail=detail, ms=int((time.perf_counter() - started) * 1000))
        log.log(logging.INFO if data is not None else logging.WARNING,
                "llm %s provider=%s model=%s outcome=%s error_class=%s ms=%s",
                route.role, route.provider, route.model, record["outcome"], error_class, record["ms"])
        return data, record
