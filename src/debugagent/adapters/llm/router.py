"""LLM router: implements the LLM port with primary -> fallback and fail-closed structured output.

Policy (Phase 0 rules, confirmed by MK0):
- invalid output (truncated, empty, non-JSON, schema or caller check failure): retry the same route once,
  then fall back (which gets the same one retry), then raise StructuredOutputError;
- timeout / unreachable / 429 / 5xx / 200-with-error: fall back immediately;
- 401/403 raise LLMAuthError and other 4xx raise LLMConfigError, never falling back.
Nothing unvalidated is ever returned.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Callable, Mapping

from debugagent.adapters.llm.response import (AUTH_CLASSES, FAILOVER_CLASSES, INVALID_OUTPUT, classify_status,
                                             extract_json, validate)
from debugagent.adapters.llm.settings import MAX_TOKENS, PROVIDER_OPTIONS, Route, load_retries, load_routes
from debugagent.adapters.llm.transport import Transport, http_post
from debugagent.domain.errors import LLMAuthError, LLMConfigError, LLMUnavailable, StructuredOutputError
from debugagent.logging_setup import get_logger
from debugagent.ports.llm_port import StructuredResult

log = get_logger(__name__)
Check = Callable[[dict], list[str]]


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
        primary, fallback = load_routes(env)
        return cls(primary, fallback, retries_on_invalid=load_retries(env), **kwargs)

    def complete_structured(self, prompt: str, *, schema: dict, name: str = "response",
                            check: Check | None = None) -> StructuredResult:
        attempts: list[dict] = []
        tries = 1 + self.retries_on_invalid
        plan = [(self.primary, tries), (self.fallback, tries)]
        for index, (route, route_tries) in enumerate(plan):
            for _ in range(route_tries):
                data, record = self._attempt(route, prompt, schema, name, check)
                attempts.append(record)
                if data is not None:
                    return StructuredResult(data, route.provider, route.model, index > 0, attempts)
                self._raise_if_final(route, record["error_class"], attempts)
                if record["error_class"] in FAILOVER_CLASSES:
                    break  # transport-level failure: straight to the next route, no same-route retry
        if any(a["error_class"] == INVALID_OUTPUT for a in attempts):
            raise StructuredOutputError("no route produced output that passed validation",
                                        error_class=INVALID_OUTPUT, attempts=attempts)
        raise LLMUnavailable("no LLM route could serve the request", error_class=attempts[-1]["error_class"],
                             attempts=attempts)

    @staticmethod
    def _raise_if_final(route: Route, error_class: str, attempts: list[dict]) -> None:
        """Auth and config errors stop the request: they are never retried or failed over."""
        if error_class in AUTH_CLASSES:
            raise LLMAuthError(f"{route.role} ({route.provider}) rejected the credentials",
                               error_class=error_class, attempts=attempts)
        if error_class not in FAILOVER_CLASSES and error_class != INVALID_OUTPUT:
            raise LLMConfigError(f"{route.role} ({route.provider}) refused the request: {error_class}",
                                 error_class=error_class, attempts=attempts)

    def _payload(self, route: Route, prompt: str, schema: dict, name: str) -> dict:
        return {
            "model": route.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
            **PROVIDER_OPTIONS[route.provider],
        }

    def _attempt(self, route: Route, prompt: str, schema: dict, name: str,
                 check: Check | None) -> tuple[dict | None, dict]:
        record = {"route": route.role, "provider": route.provider, "model": route.model}
        started = time.perf_counter()
        data, error_class, detail = None, None, ""
        try:
            status, text = self.transport(f"{route.base_url}/chat/completions", route.api_key,
                                          self._payload(route, prompt, schema, name), route.timeout_s)
        except TimeoutError:
            error_class, detail = "TIMEOUT", f"no response within {route.timeout_s:g}s"
        except OSError as exc:
            error_class, detail = "UNREACHABLE", type(exc).__name__
        else:
            record["status"] = status
            data, error_class, detail = self._read(status, text, schema, check)
        record.update(outcome="served" if data is not None else "error", error_class=error_class,
                      detail=detail, ms=int((time.perf_counter() - started) * 1000))
        log.log(logging.INFO if data is not None else logging.WARNING, "llm %s provider=%s model=%s outcome=%s error_class=%s ms=%s",
                route.role, route.provider, route.model, record["outcome"], error_class, record["ms"])
        return data, record

    @staticmethod
    def _read(status: int, text: str, schema: dict, check: Check | None) -> tuple[dict | None, str | None, str]:
        """(data, error_class, detail) for one HTTP answer."""
        try:
            body = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            body = None
        error_class = classify_status(status, body)
        if error_class:
            return None, error_class, (text or "")[:200]
        data, problem = extract_json(body, schema)
        problems = [problem] if data is None else (validate(data, schema) or (check(data) if check else []))
        if problems:
            return None, INVALID_OUTPUT, "; ".join(problems)[:300]
        return data, None, ""
