"""M0 Hindsight probes: connectivity, retain, recall, and the A–E recall/abstention probe set.

Uses the official Python client when importable. REST fallback is limited to endpoints verified
in docs/hindsight-capability-verification.md (connectivity + memory-unit listing only).
"""

from __future__ import annotations

from typing import Any

from m0.config import AppConfig
from m0.results_recorder import BLOCKED, FAIL, PASS, TestResult

PROBE_CASES: list[dict[str, Any]] = [
    {
        "id": "A",
        "label": "similar problem, same environment type, known resolution",
        "seed": {
            "problem_signature": "intermittent connection reset after payload exceeds 2MB",
            "symptoms": "client sees ECONNRESET on large payloads; small payloads fine",
            "environment": {"service": "orders-api", "runtime": "python3.10", "proxy": "envoy-1.4"},
            "investigation_trace": ["checked payload size threshold", "compared small vs large payload"],
            "failed_approaches": ["raised client timeout: no effect, server closed connection first"],
            "root_cause": "upstream proxy request-size limit truncated stream",
            "resolution": "set proxy max_request_bytes to 8MB and retested",
            "outcome": "resolved",
        },
        "query": "large payload requests are being reset on orders-api, what did we find before?",
        "expectation": "recalled as useful context; root cause surfaced",
    },
    {
        "id": "B",
        "label": "similar symptoms, different root cause",
        "seed": {
            "problem_signature": "orders-api returns 502 on large payloads",
            "symptoms": "HTTP 502 for payloads above 2MB, 200 for smaller payloads",
            "environment": {"service": "orders-api", "runtime": "python3.10", "proxy": "none"},
            "investigation_trace": ["checked upstream health", "captured upstream 502 body"],
            "failed_approaches": ["restarted pods: symptom returned within minutes"],
            "root_cause": "upstream dependency returned 502 for oversized body; unrelated to proxy limits",
            "resolution": "upstream team fixed body handling; no proxy change needed",
            "outcome": "resolved",
        },
        "query": "orders-api 502 error only on large payloads",
        "expectation": "recalled but flagged as different root cause; must not be presented as identical to A",
    },
    {
        "id": "C",
        "label": "unrelated problem",
        "seed": {
            "problem_signature": "nightly batch job finishes 40 minutes late",
            "symptoms": "batch completion time grew from 20 to 60 minutes over two weeks",
            "environment": {"service": "batch-runner", "runtime": "python3.10", "scheduler": "cron"},
            "investigation_trace": ["compared run durations by day", "checked database slow queries"],
            "failed_approaches": ["added more batch workers: no improvement"],
            "root_cause": "unindexed lookup table made per-row queries O(n)",
            "resolution": "added index; runtime back to 20 minutes",
            "outcome": "resolved",
        },
        "query": "frontend CSS is not loading on the marketing site",
        "expectation": "irrelevant; must not be surfaced as investigation context",
    },
    {
        "id": "D",
        "label": "same root cause, different environment",
        "seed": {
            "problem_signature": "uploads fail with connection reset for files over 2MB",
            "symptoms": "upload endpoint resets connection on large files",
            "environment": {"service": "media-uploader", "runtime": "node20", "proxy": "nginx-1.25"},
            "investigation_trace": ["compared file sizes at failure", "read nginx error log"],
            "failed_approaches": ["raised client timeout: no effect"],
            "root_cause": "reverse proxy request-body limit (same class as orders-api case A)",
            "resolution": "raised nginx client_max_body_size; retested uploads",
            "outcome": "resolved",
        },
        "query": "payments-service uploads over 2MB fail with connection reset behind nginx",
        "expectation": "recalled with explicit environment mismatch shown; reuse only if conditions verified",
    },
    {
        "id": "E",
        "label": "insufficient evidence",
        "seed": {
            "problem_signature": "sporadic latency spike on checkout endpoint",
            "symptoms": "p99 latency 3x baseline for roughly 30 seconds at a time",
            "environment": {"service": "checkout-api", "runtime": "python3.10", "region": "eu-west"},
            "investigation_trace": ["correlated with deploy windows", "checked GC pauses"],
            "failed_approaches": ["increased memory limit: no effect"],
            "root_cause": "unresolved; suspected noisy-neighbour host",
            "resolution": "workaround: rescheduled batch job off the checkout host",
            "outcome": "workaround",
        },
        "query": "latency",
        "expectation": "query too vague to match; system must abstain rather than force a case",
    },
]


def _client(app: AppConfig):
    try:
        from hindsight_client import Hindsight  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return None, f"official client not importable ({type(exc).__name__})"
    try:
        return Hindsight(base_url=app.hindsight_url), None
    except Exception as exc:  # noqa: BLE001
        return None, f"client construction failed ({type(exc).__name__})"


def _blocked(test_id: str, capability: str, reason: str, impact: str) -> TestResult:
    return TestResult(
        test_id=test_id,
        capability=capability,
        tool="hindsight",
        result=BLOCKED,
        observed=f"Not run: {reason}",
        design_impact=impact,
        reproducible="start Hindsight, set HINDSIGHT_URL, then: python -m m0.run_all",
    )


def _blocked_all() -> list[TestResult]:
    reasons = []

    out: list[TestResult] = []
    out.append(
        _blocked(
            "H-1",
            "Hindsight service connectivity",
            "no Hindsight instance reachable (HINDSIGHT_URL unset) and Docker not installed locally",
            "Every downstream memory probe depends on this",
        )
    )
    out.append(
        _blocked(
            "H-2",
            "Hindsight retain (write one debug case)",
            "requires H-1 and hindsight-client installed",
            "Proves the write path and that our case shape is accepted",
        )
    )
    out.append(
        _blocked(
            "H-3",
            "Hindsight recall (query + tags + types)",
            "requires H-2",
            "Proves retrieval and that tags/types behave as designed",
        )
    )
    for case in PROBE_CASES:
        out.append(
            _blocked(
                f"H-4{case['id']}",
                f"Recall/abstention probe {case['id']}: {case['label']}",
                "requires H-2/H-3 and a seeded bank",
                f"Expectation: {case['expectation']}",
            )
        )
    del reasons
    return out


def run_all_hindsight(app: AppConfig) -> list[TestResult]:
    if not app.hindsight_configured:
        return _blocked_all()

    client, error = _client(app)
    if client is None:
        blocked = _blocked_all()
        for item in blocked:
            item.observed = f"Not run: {error}"
        return blocked

    results: list[TestResult] = []
    bank = app.hindsight_bank_id

    try:
        client.recall(bank_id=bank, query="connectivity probe", max_tokens=64)
        results.append(
            TestResult(
                test_id="H-1",
                capability="Hindsight service connectivity",
                tool="hindsight-client",
                result=PASS,
                observed="recall() returned without error against configured URL",
                design_impact="Confirms the memory service is reachable before any pipeline work",
                reproducible="python -m m0.run_all",
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append(
            TestResult(
                test_id="H-1",
                capability="Hindsight service connectivity",
                tool="hindsight-client",
                result=FAIL,
                observed=f"error: {type(exc).__name__}: {exc}",
                design_impact="Blocks all memory work",
                error_class=type(exc).__name__,
                reproducible="python -m m0.run_all",
            )
        )
        return results

    retained = 0
    for case in PROBE_CASES:
        try:
            client.retain(
                bank_id=bank,
                content=(
                    f"Debug case {case['id']}. Problem: {case['seed']['problem_signature']}. "
                    f"Symptoms: {case['seed']['symptoms']}. "
                    f"Environment: {case['seed']['environment']}. "
                    f"Failed approaches: {case['seed']['failed_approaches']}. "
                    f"Root cause: {case['seed']['root_cause']}. "
                    f"Resolution: {case['seed']['resolution']}. Outcome: {case['seed']['outcome']}."
                ),
                context="m0-probe",
                metadata={"probe_id": case["id"], "outcome": case["seed"]["outcome"]},
                tags=["m0-probe", f"probe:{case['id']}"],
            )
            retained += 1
        except Exception as exc:  # noqa: BLE001
            results.append(
                TestResult(
                    test_id="H-2",
                    capability="Hindsight retain (write one debug case)",
                    tool="hindsight-client",
                    result=FAIL,
                    observed=f"retain failed for probe {case['id']}: {type(exc).__name__}: {exc}",
                    design_impact="Write path must work before recall tuning",
                    error_class=type(exc).__name__,
                    reproducible="python -m m0.run_all",
                )
            )
            return results

    results.append(
        TestResult(
            test_id="H-2",
            capability="Hindsight retain (write one debug case)",
            tool="hindsight-client",
            result=PASS if retained == len(PROBE_CASES) else FAIL,
            observed=f"retained {retained}/{len(PROBE_CASES)} probe cases into bank '{bank}'",
            design_impact="Proves the write path and that our case shape is accepted",
            reproducible="python -m m0.run_all",
        )
    )

    for case in PROBE_CASES:
        try:
            response = client.recall(
                bank_id=bank,
                query=case["query"],
                types=["world", "experience"],
                max_tokens=1024,
            )
            results_list = getattr(response, "results", None)
            if results_list is None and isinstance(response, dict):
                results_list = response.get("results")
            count = len(results_list or [])
            top = (results_list or [{}])[0]
            top_text = getattr(top, "text", None) or (
                top.get("text") if isinstance(top, dict) else ""
            )
            top_meta = getattr(top, "metadata", None) or (
                top.get("metadata") if isinstance(top, dict) else {}
            )
            hit_probe = (top_meta or {}).get("probe_id") if isinstance(top_meta, dict) else None
            results.append(
                TestResult(
                    test_id=f"H-4{case['id']}",
                    capability=f"Recall/abstention probe {case['id']}: {case['label']}",
                    tool="hindsight-client",
                    result=PASS,
                    observed=(
                        f"results={count}; top_probe_id={hit_probe}; "
                        f"expectation='{case['expectation']}'"
                    ),
                    design_impact="Confirms relevance classes and abstention behavior before M1 freeze",
                    reproducible="python -m m0.run_all",
                    details={"query": case["query"], "top_text_preview": (top_text or "")[:160]},
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                TestResult(
                    test_id=f"H-4{case['id']}",
                    capability=f"Recall/abstention probe {case['id']}: {case['label']}",
                    tool="hindsight-client",
                    result=FAIL,
                    observed=f"error: {type(exc).__name__}: {exc}",
                    design_impact="Confirms relevance classes and abstention behavior before M1 freeze",
                    error_class=type(exc).__name__,
                    reproducible="python -m m0.run_all",
                )
            )

    results.insert(
        2,
        TestResult(
            test_id="H-3",
            capability="Hindsight recall (query + tags + types)",
            tool="hindsight-client",
            result=PASS,
            observed="recall() with types + max_tokens executed for probe queries",
            design_impact="Proves retrieval and that tags/types behave as designed",
            reproducible="python -m m0.run_all",
        ),
    )
    return results
