"""M0 LLM probes (stdlib only): availability, structured output, error surface, failover, latency."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from m0.config import AppConfig, ProviderConfig
from m0.results_recorder import BLOCKED, FAIL, PASS, TestResult
from m0.schema_validate import validate

HYPOTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string", "minLength": 1},
        "supporting_case_ids": {"type": "array", "items": {"type": "string"}},
        "relevance_state": {
            "type": "string",
            "enum": ["supported", "conditional", "weak-reference", "abstained"],
        },
        "refutation_conditions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hypothesis", "supporting_case_ids", "relevance_state", "refutation_conditions"],
    "additionalProperties": False,
}

STRUCTURED_PROMPT = (
    "Return a single debugging hypothesis object. supporting_case_ids must be an empty array "
    "because no prior cases were supplied. relevance_state must be 'abstained' when there is no "
    "prior evidence. refutation_conditions must contain one short string."
)

PLAIN_PROMPT = "Reply with the single word: ready"


class ProviderError(Exception):
    def __init__(self, error_class: str, status: int | None, message: str):
        super().__init__(message)
        self.error_class = error_class
        self.status = status
        self.message = message


def classify_status(status: int) -> str:
    if status in (401, 403):
        return "AUTH"
    if status == 402:
        return "BILLING"
    if status == 400:
        return "BAD_REQUEST"
    if status == 404:
        return "MODEL_NOT_FOUND"
    if status == 429:
        return "RATE_LIMITED"
    if status >= 500:
        return "UNAVAILABLE"
    return "UNKNOWN"


FAILOVER_ELIGIBLE = {"RATE_LIMITED", "UNAVAILABLE", "TIMEOUT"}


def _post_json(url: str, api_key: str, payload: dict, timeout_s: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ProviderError(classify_status(exc.code), exc.code, detail) from exc
    except TimeoutError as exc:
        raise ProviderError("TIMEOUT", None, "request timed out") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            raise ProviderError("TIMEOUT", None, "request timed out") from exc
        raise ProviderError("UNAVAILABLE", None, type(reason).__name__) from exc
    except json.JSONDecodeError as exc:
        raise ProviderError("BAD_RESPONSE", None, "response was not JSON") from exc


def build_payload(provider: ProviderConfig, prompt: str, schema: dict | None) -> dict:
    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "debug_hypothesis",
                "strict": True,
                "schema": schema,
            },
        }
        if provider.name == "openrouter":
            payload["provider"] = {"require_parameters": True}
    return payload


def call_structured(
    provider: ProviderConfig, prompt: str, schema: dict, timeout_s: int
) -> tuple[dict, int, int | None]:
    started = time.perf_counter()
    response = _post_json(
        provider.chat_url, provider.api_key, build_payload(provider, prompt, schema), timeout_s
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    usage = response.get("usage") or {}
    completion_tokens = usage.get("completion_tokens")
    return response, latency_ms, completion_tokens


def _extract_content(response: dict) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "".join(parts)
    return ""


def _missing(config: ProviderConfig) -> str:
    fields = config.missing_fields()
    return f"missing config field(s): {', '.join(fields)}" if fields else ""


def run_availability(test_id: str, label: str, provider: ProviderConfig, design_impact: str) -> TestResult:
    if not provider.configured:
        return TestResult(
            test_id=test_id,
            capability=f"{label} availability (plain chat completion)",
            tool=provider.name,
            result=BLOCKED,
            observed=f"Not run: {_missing(provider)}",
            design_impact=design_impact,
            reproducible="set env vars then: python -m m0.run_all",
        )
    try:
        started = time.perf_counter()
        response = _post_json(
            provider.chat_url,
            provider.api_key,
            {"model": provider.model, "messages": [{"role": "user", "content": PLAIN_PROMPT}]},
            provider.timeout_s,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        content = _extract_content(response).strip()
        ok = bool(content)
        return TestResult(
            test_id=test_id,
            capability=f"{label} availability (plain chat completion)",
            tool=provider.name,
            result=PASS if ok else FAIL,
            observed=(
                f"HTTP 200, non-empty content ({len(content)} chars), usage present="
                f"{bool(response.get('usage'))}"
                if ok
                else "HTTP 200 but empty content"
            ),
            design_impact=design_impact,
            latency_ms=latency_ms,
            reproducible="python -m m0.run_all",
            details={"model": provider.model},
        )
    except ProviderError as exc:
        return TestResult(
            test_id=test_id,
            capability=f"{label} availability (plain chat completion)",
            tool=provider.name,
            result=FAIL,
            observed=f"Provider error: {exc.message}",
            design_impact=design_impact,
            error_class=exc.error_class,
            reproducible="python -m m0.run_all",
            details={"model": provider.model, "status": exc.status},
        )


def run_structured(test_id: str, label: str, provider: ProviderConfig, design_impact: str) -> TestResult:
    if not provider.configured:
        return TestResult(
            test_id=test_id,
            capability=f"{label} structured output (response_format json_schema) + local validation",
            tool=provider.name,
            result=BLOCKED,
            observed=f"Not run: {_missing(provider)}",
            design_impact=design_impact,
            reproducible="set env vars then: python -m m0.run_all",
        )
    try:
        response, latency_ms, completion_tokens = call_structured(
            provider, STRUCTURED_PROMPT, HYPOTHESIS_SCHEMA, provider.timeout_s
        )
        raw = _extract_content(response)
        parse_errors: list[str] = []
        instance = None
        try:
            instance = json.loads(raw)
        except json.JSONDecodeError as exc:
            parse_errors.append(f"not valid JSON: {exc.msg}")
        schema_errors = validate(instance, HYPOTHESIS_SCHEMA) if instance is not None else ["n/a"]
        ok = instance is not None and not schema_errors
        return TestResult(
            test_id=test_id,
            capability=f"{label} structured output (response_format json_schema) + local validation",
            tool=provider.name,
            result=PASS if ok else FAIL,
            observed=(
                "Response parsed and validated against hypothesis schema"
                if ok
                else f"parse_errors={parse_errors or 'none'}; schema_errors={schema_errors or 'none'}"
            ),
            design_impact=design_impact,
            latency_ms=latency_ms,
            reproducible="python -m m0.run_all",
            details={
                "model": provider.model,
                "completion_tokens": completion_tokens,
                "raw_length": len(raw),
            },
        )
    except ProviderError as exc:
        return TestResult(
            test_id=test_id,
            capability=f"{label} structured output (response_format json_schema) + local validation",
            tool=provider.name,
            result=FAIL,
            observed=f"Provider error: {exc.message}",
            design_impact=design_impact,
            error_class=exc.error_class,
            reproducible="python -m m0.run_all",
            details={"model": provider.model, "status": exc.status},
        )


def run_error_surface(test_id: str, provider: ProviderConfig) -> TestResult:
    if not provider.configured:
        return TestResult(
            test_id=test_id,
            capability="Primary error classification (unknown model + bad key)",
            tool=provider.name,
            result=BLOCKED,
            observed=f"Not run: {_missing(provider)}",
            design_impact="Confirms documented 404/401 map to MODEL_NOT_FOUND/AUTH so failover skips them",
            reproducible="set env vars then: python -m m0.run_all",
        )
    observed: dict[str, Any] = {}
    classes: dict[str, str] = {}
    for label, model, key in (
        ("unknown_model", provider.model + "-does-not-exist", provider.api_key),
        ("bad_key", provider.model, "invalid-key-for-error-surface-test"),
    ):
        probe = ProviderConfig(
            name=provider.name,
            model=model,
            base_url=provider.base_url,
            api_key=key,
            timeout_s=provider.timeout_s,
        )
        try:
            _post_json(
                probe.chat_url,
                probe.api_key,
                {"model": model, "messages": [{"role": "user", "content": PLAIN_PROMPT}]},
                probe.timeout_s,
            )
            observed[label] = "unexpected success"
            classes[label] = "NONE"
        except ProviderError as exc:
            observed[label] = f"{exc.error_class} (status={exc.status})"
            classes[label] = exc.error_class
    expected = {"unknown_model": "MODEL_NOT_FOUND", "bad_key": "AUTH"}
    ok = classes == expected
    return TestResult(
        test_id=test_id,
        capability="Primary error classification (unknown model + bad key)",
        tool=provider.name,
        result=PASS if ok else FAIL,
        observed=f"observed={observed}; expected={expected}",
        design_impact="Confirms documented codes map to MODEL_NOT_FOUND/AUTH so failover skips them",
        error_class=None if ok else "MISMATCH",
        reproducible="python -m m0.run_all",
        details={"provider": provider.name},
    )


def run_failover_drill(test_id: str, config: AppConfig) -> TestResult:
    if not config.fallback.configured:
        return TestResult(
            test_id=test_id,
            capability="Primary→fallback routing on unavailable primary (unreachable base URL)",
            tool="router",
            result=BLOCKED,
            observed=f"Fallback not runnable: {_missing(config.fallback)}",
            design_impact="Failover is a demo-day requirement; unverified drill blocks M1",
            reproducible="set fallback env vars then: python -m m0.run_all",
        )
    unreachable = ProviderConfig(
        name=config.primary.name or "primary",
        model=config.primary.model,
        base_url="http://127.0.0.1:9",
        api_key=config.primary.api_key or "unused",
        timeout_s=3,
    )
    attempts: list[dict[str, Any]] = []
    served_by = None
    started = time.perf_counter()
    for provider in (unreachable, config.fallback):
        try:
            response, _, _ = call_structured(
                provider, STRUCTURED_PROMPT, HYPOTHESIS_SCHEMA, provider.timeout_s
            )
            raw = _extract_content(response)
            instance = None
            try:
                instance = json.loads(raw)
            except json.JSONDecodeError:
                instance = None
            valid = instance is not None and not validate(instance, HYPOTHESIS_SCHEMA)
            attempts.append(
                {
                    "provider": provider.name,
                    "base_url": provider.base_url,
                    "outcome": "served" if valid else "served_but_schema_invalid",
                }
            )
            if valid:
                served_by = provider.name
                break
        except ProviderError as exc:
            attempts.append(
                {
                    "provider": provider.name,
                    "base_url": provider.base_url,
                    "outcome": f"error:{exc.error_class}",
                }
            )
    latency_ms = int((time.perf_counter() - started) * 1000)
    ok = served_by == config.fallback.name
    return TestResult(
        test_id=test_id,
        capability="Primary→fallback routing on unavailable primary (unreachable base URL)",
        tool="router",
        result=PASS if ok else FAIL,
        observed=f"attempts={attempts}; served_by={served_by}; fallback_used={served_by == config.fallback.name}",
        design_impact="Failover is a demo-day requirement; this drill gates the router design",
        latency_ms=latency_ms,
        reproducible="python -m m0.run_all",
        details={"expected_fallback": config.fallback.name},
    )


def run_auth_no_failover(test_id: str, config: AppConfig) -> TestResult:
    if not config.primary.configured:
        return TestResult(
            test_id=test_id,
            capability="Auth error must NOT trigger failover",
            tool="router",
            result=BLOCKED,
            observed=f"Not run: {_missing(config.primary)}",
            design_impact="Team rule: auth errors must not silently fail over",
            reproducible="set primary env vars then: python -m m0.run_all",
        )
    bad_key_primary = ProviderConfig(
        name=config.primary.name,
        model=config.primary.model,
        base_url=config.primary.base_url,
        api_key="invalid-key-for-auth-drill",
        timeout_s=config.primary.timeout_s,
    )
    try:
        call_structured(bad_key_primary, STRUCTURED_PROMPT, HYPOTHESIS_SCHEMA, bad_key_primary.timeout_s)
        return TestResult(
            test_id=test_id,
            capability="Auth error must NOT trigger failover",
            tool="router",
            result=FAIL,
            observed="Primary accepted an invalid key (unexpected)",
            design_impact="Team rule: auth errors must not silently fail over",
            reproducible="python -m m0.run_all",
        )
    except ProviderError as exc:
        skipped = exc.error_class not in FAILOVER_ELIGIBLE
        return TestResult(
            test_id=test_id,
            capability="Auth error must NOT trigger failover",
            tool="router",
            result=PASS if skipped else FAIL,
            observed=f"primary error={exc.error_class}; failover_triggered={not skipped}",
            design_impact="Team rule: auth errors must not silently fail over",
            error_class=exc.error_class,
            reproducible="python -m m0.run_all",
            details={"status": exc.status},
        )


def run_latency(test_id: str, config: AppConfig) -> TestResult:
    targets = [p for p in (config.primary, config.fallback) if p.configured]
    if not targets:
        return TestResult(
            test_id=test_id,
            capability="Structured-call latency calibration (timeout budget sizing)",
            tool="both",
            result=BLOCKED,
            observed="No provider configured; cannot measure latency",
            design_impact="Sets LLM_TIMEOUT_S and retry budget so primary+fallback fits demo latency",
            reproducible="set env vars then: python -m m0.run_all",
        )
    measurements: dict[str, int] = {}
    for provider in targets:
        try:
            _, latency_ms, _ = call_structured(
                provider, STRUCTURED_PROMPT, HYPOTHESIS_SCHEMA, provider.timeout_s
            )
            measurements[provider.name] = latency_ms
        except ProviderError as exc:
            measurements[provider.name] = -1
            del exc
    return TestResult(
        test_id=test_id,
        capability="Structured-call latency calibration (timeout budget sizing)",
        tool=",".join(measurements.keys()),
        result=PASS if all(v > 0 for v in measurements.values()) else FAIL,
        observed=f"latency_ms_by_provider={measurements} (-1 means error)",
        design_impact="Sets LLM_TIMEOUT_S and retry budget so primary+fallback fits demo latency",
        reproducible="python -m m0.run_all",
        details={"configured_timeout_s": targets[0].timeout_s},
    )


def run_all_llm(config: AppConfig) -> list[TestResult]:
    return [
        run_availability(
            "RT-1",
            "Primary (OpenRouter Space Bunny Alpha)",
            config.primary,
            "Confirms the primary route is usable; if unavailable the demo runbook must start on fallback",
        ),
        run_availability(
            "RT-4",
            "Fallback (Baseten DeepSeek V4.1 Flash)",
            config.fallback,
            "Fallback is the safety net; M1 is blocked until this passes",
        ),
        run_structured(
            "RT-2",
            "Primary (OpenRouter Space Bunny Alpha)",
            config.primary,
            "Decides whether proposal paths can use primary natively or must validate-and-retry",
        ),
        run_structured(
            "RT-5",
            "Fallback (Baseten DeepSeek V4.1 Flash)",
            config.fallback,
            "Confirms fallback satisfies the same structured-output contract",
        ),
        run_error_surface("RT-3", config.primary),
        run_failover_drill("RT-6", config),
        run_auth_no_failover("RT-8", config),
        run_latency("RT-7", config),
    ]
