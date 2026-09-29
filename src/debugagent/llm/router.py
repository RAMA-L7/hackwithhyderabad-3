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

from debugagent.llm.tools import (
    MAX_TOOL_TURNS,
    ToolCall,
    ToolCallError,
    ToolDefinition,
    ToolDefinitionError,
    ToolResultMessage,
    parse_tool_calls,
    tool_choice_payload,
)

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


class LLMToolUnsupported(LLMError):
    """Tool calling was requested but the selected route is not enabled for it. Never downgraded."""


class LLMToolTurnLimit(LLMError):
    """The bounded tool loop reached `max_turns`. Typed so a runaway loop is never silent."""


class LLMToolCallInvalid(LLMError):
    """The provider returned a malformed tool call. Raised, never coerced into empty arguments."""


@dataclass(frozen=True)
class ToolLoopResult:
    """Outcome of a bounded tool loop: the validated final answer plus what happened on the way."""

    result: StructuredResult
    turns: int
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResultMessage, ...]

    def to_dict(self) -> dict:
        return {
            "provider": self.result.provider,
            "model": self.result.model,
            "fallback_used": self.result.fallback_used,
            "turns": self.turns,
            "tool_calls": [{"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
                           for c in self.tool_calls],
            "tool_results": [{"call_id": r.call_id, "name": r.name, "ok": r.ok} for r in self.tool_results],
        }


@dataclass(frozen=True)
class Route:
    role: str
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    timeout_s: float = 30.0
    # P2: tool calling is opt-in per route and off by default. Nothing in this repository has measured
    # that either provider serves tool calls, so a route must be enabled deliberately (LLM_<ROLE>_TOOLS)
    # rather than assumed. A disabled route raises LLMToolUnsupported instead of silently degrading.
    supports_tools: bool = False


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


def _flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true", "yes", "on")


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
                            env.get(names["API_KEY"], "").strip(), timeout_s,
                            supports_tools=_flag(env, f"LLM_{role}_TOOLS")))
    if missing:
        raise LLMConfigError(f"missing LLM settings: {', '.join(missing)}", error_class="CONFIG")
    return routes[0], routes[1]


class LLMRouter:
    def __init__(self, primary: Route, fallback: Route, *, transport: Transport = http_post,
                 retries_on_invalid: int = 1, max_tokens: int = MAX_TOKENS,
                 max_tool_turns: int = MAX_TOOL_TURNS):
        self.primary = primary
        self.fallback = fallback
        self.transport = transport
        self.retries_on_invalid = retries_on_invalid
        self.max_tokens = max_tokens
        # P2. Bounded and configurable; the loop raises LLMToolTurnLimit rather than retrying forever.
        self.max_tool_turns = max_tool_turns

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kwargs) -> "LLMRouter":
        env = os.environ if env is None else env
        primary, fallback = load_routes(env)
        retries = env.get("LLM_MAX_RETRIES_PRIMARY") or "1"
        if not retries.isdigit():
            raise LLMConfigError(f"LLM_MAX_RETRIES_PRIMARY={retries!r} is not a whole number", error_class="CONFIG")
        turns = env.get("LLM_MAX_TOOL_TURNS") or str(MAX_TOOL_TURNS)
        if not turns.isdigit() or int(turns) < 1:
            raise LLMConfigError(f"LLM_MAX_TOOL_TURNS={turns!r} is not a positive whole number",
                                 error_class="CONFIG")
        if "max_tool_turns" not in kwargs:
            kwargs["max_tool_turns"] = int(turns)
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

    # --- P2: bounded tool loop -----------------------------------------------------------------------------
    # Transport only. This never executes a tool, never consults the P1 registry, and never crosses to
    # the other route mid-loop. The caller supplies `execute_tool`; P4+ decides what runs behind it.

    def complete_with_tools(self, prompt: str, *, tools, tool_choice: str = "auto",
                            schema: dict, name: str = "response",
                            check: Callable[[dict], list[str]] | None = None,
                            execute_tool: Callable[[ToolCall], ToolResultMessage],
                            max_turns: int | None = None) -> ToolLoopResult:
        """Run a bounded tool loop on the primary route and return the validated final answer.

        `tools` is a sequence of ToolDefinition (or mappings). When the provider asks for tools, each
        call is parsed and validated, handed to `execute_tool`, and the resulting ToolResultMessage is
        appended to the message history before the next turn.

        Deliberate limitations, documented rather than emulated:
        - **No cross-route fallback.** Switching provider mid-loop would change the semantics of a
          conversation that already carries tool messages, so a transport failure raises
          `LLMUnavailable` instead. Only `complete_structured` (the no-tools path) fails over.
        - **No `response_format` in tool mode.** The final answer is validated locally instead.
        - **Tools are opt-in per route.** A route without `supports_tools` raises `LLMToolUnsupported`;
          tools are never silently dropped and the request is never downgraded to a normal one.
        """
        if isinstance(tools, (str, bytes)) or not isinstance(tools, (list, tuple)):
            raise ToolDefinitionError(f"tools: expected a sequence of tool definitions, "
                                      f"got {type(tools).__name__}")
        if not tools:
            raise ToolDefinitionError("tools: must not be empty")
        # Accept either already-built ToolDefinition objects or plain mappings, in a list or a tuple.
        if all(isinstance(item, ToolDefinition) for item in tools):
            definitions = tuple(tools)
        else:
            definitions = ToolDefinition.from_mapping(tools)
        choice = tool_choice_payload(tool_choice, definitions)
        route = self.primary
        if not route.supports_tools:
            raise LLMToolUnsupported(
                f"route {route.role} ({route.provider}/{route.model}) is not enabled for tool calling; "
                f"set LLM_{route.role.upper()}_TOOLS=1 once tool support has been measured for it",
                error_class="TOOL_UNSUPPORTED")
        limit = self.max_tool_turns if max_turns is None else max_turns
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            # Report the value actually validated, not the parameter: when max_turns is omitted the
            # parameter is None and would have misreported a bad router default as "got None".
            raise ToolDefinitionError(f"max_turns: expected a positive integer, got {limit!r}")

        messages: list[dict] = [{"role": "user", "content": prompt}]
        known = {d.name for d in definitions}
        attempts: list[dict] = []
        all_calls: list[ToolCall] = []
        all_results: list[ToolResultMessage] = []

        for turn in range(1, limit + 1):
            message, record = self._tool_turn(route, messages, definitions, choice)
            attempts.append(record)
            if record.get("error_class"):
                raise self._tool_error(route, record, attempts)

            calls = message.get("tool_calls") or ()
            if not calls:
                data, problem = extract_json({"choices": [{"message": message}]}, schema)
                problems = ([problem] if data is None else validate(data, schema)) or \
                           (check(data) if check else [])
                if problems:
                    raise StructuredOutputError(
                        "tool loop ended with output that failed validation: " + "; ".join(problems)[:300],
                        error_class="INVALID_OUTPUT", attempts=attempts)
                return ToolLoopResult(
                    result=StructuredResult(data, route.provider, route.model, False, attempts),
                    turns=turn, tool_calls=tuple(all_calls), tool_results=tuple(all_results))

            try:
                parsed = parse_tool_calls(message, known)
            except ToolCallError as exc:
                raise LLMToolCallInvalid(f"provider returned a malformed tool call: {exc}",
                                         error_class="INVALID_TOOL_CALL", attempts=attempts) from exc
            all_calls.extend(parsed)
            messages.append({"role": "assistant", "content": message.get("content"),
                             "tool_calls": [
                                 {"id": c.call_id, "type": "function",
                                  "function": {"name": c.name, "arguments": c.arguments}}
                                 for c in parsed]})
            for call in parsed:
                result = execute_tool(call)
                if not isinstance(result, ToolResultMessage):
                    raise ToolCallError(
                        f"execute_tool returned {type(result).__name__} for call {call.call_id!r}; "
                        "a ToolResultMessage is required")
                all_results.append(result)
                messages.append(result.to_message())

        raise LLMToolTurnLimit(
            f"tool loop reached max_turns={limit} without a final answer "
            f"({len(all_calls)} tool call(s) observed)",
            error_class="TOOL_TURN_LIMIT", attempts=attempts)

    def _tool_turn(self, route: Route, messages: list[dict], definitions, choice) -> tuple[dict, dict]:
        """One provider round trip in tool mode. Returns the assistant message and an attempt record."""
        payload = {
            "model": route.model,
            "messages": [dict(m) for m in messages],
            "max_tokens": self.max_tokens,
            "tools": [d.to_payload() for d in definitions],
            "tool_choice": choice,
            **PROVIDER_OPTIONS[route.provider],
        }
        record = {"route": route.role, "provider": route.provider, "model": route.model}
        started = time.perf_counter()
        message, error_class, detail = {}, None, ""
        try:
            status, text = self.transport(f"{route.base_url}/chat/completions", route.api_key,
                                          payload, route.timeout_s)
        except TimeoutError:
            error_class, detail = "TIMEOUT", f"no response within {route.timeout_s:g}s"
        except OSError as exc:
            error_class, detail = "UNREACHABLE", type(exc).__name__
        else:
            record["status"] = status
            try:
                body = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                body = None
            error_class = classify_status(status, body)
            if error_class:
                detail = (text or "")[:200]
            else:
                choices = (body or {}).get("choices")
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    error_class, detail = "BAD_RESPONSE", "no choices"
                else:
                    candidate = choices[0].get("message")
                    if not isinstance(candidate, dict):
                        error_class, detail = "BAD_RESPONSE", "message was not an object"
                    else:
                        message = candidate
        record.update(outcome="error" if error_class else "served", error_class=error_class,
                      detail=detail, ms=int((time.perf_counter() - started) * 1000))
        log.log(logging.INFO if not error_class else logging.WARNING,
                "llm-tools %s provider=%s model=%s outcome=%s error_class=%s ms=%s",
                route.role, route.provider, route.model, record["outcome"], error_class, record["ms"])
        return message, record

    def _tool_error(self, route: Route, record: dict, attempts: list[dict]):
        error_class = record.get("error_class")
        if error_class in AUTH_CLASSES:
            return LLMAuthError(f"tool loop: {route.role} ({route.provider}) rejected the credentials",
                                error_class=error_class, attempts=attempts)
        if error_class == "BAD_RESPONSE":
            return LLMUnavailable(f"tool loop: {route.role} returned an unusable response: "
                                  f"{record.get('detail', '')}", error_class=error_class, attempts=attempts)
        return LLMUnavailable(f"tool loop: {route.role} ({route.provider}) could not serve the request: "
                              f"{error_class}", error_class=error_class, attempts=attempts)
