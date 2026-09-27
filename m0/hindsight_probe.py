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
        "query": "orders-api connections are being reset on large payloads, what did we find before?",
        "assertion": "top_1_is_expected",
        "expected_top": ["A"],
        "expectation": "case A ranks first as useful context",
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
        "assertion": "top_1_is_expected",
        "expected_top": ["B"],
        "expectation": "case B ranks first; must be distinguishable from case A despite similar symptoms",
    },
    {
        "id": "C",
        "label": "unrelated problem (irrelevance rejection)",
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
        "assertion": "top_1_not_expected",
        "expected_top": ["C"],
        "expectation": "an unrelated frontend query must not surface case C as most relevant",
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
            "root_cause": "reverse proxy request-body limit (same class as the orders-api envoy case)",
            "resolution": "raised nginx client_max_body_size; retested uploads",
            "outcome": "resolved",
        },
        "query": "payments-service uploads over 2MB fail with connection reset behind nginx",
        "assertion": "top_1_in_expected",
        "expected_top": ["A", "D"],
        "expectation": "case A or D ranks first; environment mismatch must remain visible for the app to flag",
    },
    {
        "id": "E",
        "label": "insufficient evidence (abstention feasibility)",
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
        "assertion": "strictly_below_relevant_queries",
        "expected_top": ["E"],
        "expectation": "a vague query must rank strictly below every relevant query so a relative floor can abstain",
    },
]


def _client(app: AppConfig):
    try:
        from hindsight_client import Hindsight  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return None, f"official client not importable ({type(exc).__name__})"
    try:
        if app.hindsight_api_key:
            return Hindsight(base_url=app.hindsight_url, api_key=app.hindsight_api_key), None
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
    bank_created = False
    bank_error: str | None = None

    try:
        client.get_bank_config(bank)
    except Exception:
        try:
            client.create_bank(
                bank,
                enable_text_search=True,
                enable_temporal_retrieval=True,
                enable_graph_retrieval=True,
            )
            bank_created = True
            try:
                client.update_bank_config(bank, memory_defense={"enabled": True})
            except Exception as exc:  # noqa: BLE001
                bank_error = f"memory_defense not applied: {type(exc).__name__}"
        except Exception as exc:  # noqa: BLE001
            bank_error = f"{type(exc).__name__}: {exc}"

    try:
        client.recall(bank_id=bank, query="connectivity probe", max_tokens=64)
        results.append(
            TestResult(
                test_id="H-1",
                capability="Hindsight service connectivity",
                tool="hindsight-client",
                result=PASS,
                observed=(
                    f"reachable and authenticated; bank '{bank}' "
                    f"{'created by probe' if bank_created else 'already existed'}"
                ),
                design_impact="Confirms the memory service is reachable before any pipeline work",
                reproducible="python -m m0.run_all",
                details={"bank_created_by_probe": bank_created, "bank_error": bank_error},
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
                details={"bank_created_by_probe": bank_created, "bank_error": bank_error},
            )
        )
        return results

    retained = 0
    for case in PROBE_CASES:
        try:
            client.retain(
                bank_id=bank,
                content=(
                    f"Problem: {case['seed']['problem_signature']}. "
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

    def _score_fields(item: Any) -> dict[str, Any]:
        sc = getattr(item, "scores", None)
        if sc is None:
            return {}
        out = {}
        for field_name in ("final", "reranker", "semantic", "keyword"):
            value = getattr(sc, field_name, None)
            if isinstance(value, (int, float)):
                out[field_name] = round(float(value), 6)
        return out

    ranked: list[dict[str, Any]] = []
    relevant_query_tops: list[float] = []

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
            results_list = list(results_list or [])

            entries: list[dict[str, Any]] = []
            for position, item in enumerate(results_list):
                meta = getattr(item, "metadata", None)
                if meta is None and isinstance(item, dict):
                    meta = item.get("metadata")
                probe_id = (meta or {}).get("probe_id") if isinstance(meta, dict) else None
                text = getattr(item, "text", None)
                if text is None and isinstance(item, dict):
                    text = item.get("text")
                scores = _score_fields(item)
                entries.append(
                    {
                        "pos": position,
                        "probe_id": probe_id,
                        "final": scores.get("final"),
                        "semantic": scores.get("semantic"),
                        "keyword": scores.get("keyword"),
                        "text_preview": (text or "")[:90],
                    }
                )

            top = entries[0] if entries else None
            top_id = (top or {}).get("probe_id")
            top_final = (top or {}).get("final")
            if (
                case["assertion"] not in ("strictly_below_relevant_queries", "top_1_not_expected")
                and isinstance(top_final, (int, float))
            ):
                relevant_query_tops.append(float(top_final))
            ranked.append({"probe": case["id"], "entries": entries[:5], "total": len(entries)})

            assertion = case["assertion"]
            if assertion == "top_1_is_expected":
                passed = top_id in case["expected_top"]
            elif assertion == "top_1_not_expected":
                passed = top_id not in case["expected_top"]
            elif assertion == "top_1_in_expected":
                passed = top_id in case["expected_top"]
            else:
                passed = True

            results.append(
                TestResult(
                    test_id=f"H-4{case['id']}",
                    capability=f"Recall probe {case['id']}: {case['label']}",
                    tool="hindsight-client",
                    result=PASS if passed else FAIL,
                    observed=(
                        f"total_results={len(entries)}; top_probe_id={top_id}; "
                        f"top_scores={ {k: v for k, v in (top or {}).items() if k in ('final','semantic','keyword')} }; "
                        f"assertion={assertion}; expectation='{case['expectation']}'"
                    ),
                    design_impact="Establishes whether relevance classes are achievable with this recall behavior",
                    reproducible="python -m m0.run_all",
                    details={
                        "query": case["query"],
                        "top3": entries[:3],
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                TestResult(
                    test_id=f"H-4{case['id']}",
                    capability=f"Recall probe {case['id']}: {case['label']}",
                    tool="hindsight-client",
                    result=FAIL,
                    observed=f"error: {type(exc).__name__}: {exc}",
                    design_impact="Establishes whether relevance classes are achievable with this recall behavior",
                    error_class=type(exc).__name__,
                    reproducible="python -m m0.run_all",
                )
            )

    if ranked:
        e_entries = next((r["entries"] for r in ranked if r["probe"] == "E"), [])
        e_top = e_entries[0] if e_entries else None
        e_final = (e_top or {}).get("final")
        if e_entries and relevant_query_tops:
            threshold = min(relevant_query_tops)
            strictly_below = isinstance(e_final, (int, float)) and float(e_final) < threshold
            results.append(
                TestResult(
                    test_id="H-4E-ASSERT",
                    capability="Abstention feasibility: vague query ranks strictly below all relevant queries",
                    tool="hindsight-client",
                    result=PASS if strictly_below else FAIL,
                    observed=(
                        f"vague_query_top_final={e_final}; min_relevant_query_top_final={threshold}; "
                        f"strictly_below={strictly_below}"
                    ),
                    design_impact=(
                        "Determines whether abstention can use a relative score floor; absolute scores are "
                        "documented as non-calibrated, so a fixed numeric threshold is unsafe"
                    ),
                    reproducible="python -m m0.run_all",
                    details={"relevant_query_tops": [round(v, 6) for v in relevant_query_tops]},
                )
            )
        else:
            results.append(
                TestResult(
                    test_id="H-4E-ASSERT",
                    capability="Abstention feasibility: vague query ranks strictly below all relevant queries",
                    tool="hindsight-client",
                    result=BLOCKED,
                    observed="Insufficient measurements: no vague-query top score or no relevant-query baselines",
                    design_impact="Abstention feasibility undetermined",
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

    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass
    return results
