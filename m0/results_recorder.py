"""Result recorder for M0 runtime verification (no secrets are ever written)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "NOT_RUN_BLOCKED"


@dataclass
class TestResult:
    test_id: str
    capability: str
    tool: str
    result: str
    observed: str
    design_impact: str
    latency_ms: int | None = None
    error_class: str | None = None
    reproducible: str = ""
    details: dict = field(default_factory=dict)
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict:
        return asdict(self)


def _ensure_dir() -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)


def write_results(results: list[TestResult], run_id: str) -> tuple[str, str]:
    _ensure_dir()
    json_path = os.path.join(RESULTS_DIR, f"m0-results-{run_id}.json")
    md_path = os.path.join(RESULTS_DIR, f"m0-results-{run_id}.md")
    latest_json = os.path.join(RESULTS_DIR, "m0-results-latest.json")

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump([r.to_dict() for r in results], handle, indent=2)
    with open(latest_json, "w", encoding="utf-8") as handle:
        json.dump([r.to_dict() for r in results], handle, indent=2)

    counts = {PASS: 0, FAIL: 0, BLOCKED: 0}
    for item in results:
        counts[item.result] = counts.get(item.result, 0) + 1

    lines = [
        "# M0 Runtime Verification Results",
        "",
        f"- Run ID: `{run_id}`",
        f"- Generated (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Totals: PASS={counts[PASS]} FAIL={counts[FAIL]} BLOCKED={counts[BLOCKED]}",
        "",
        "| Test | Capability | Tool | Result | Observed | Latency | Error class | Affects | Reproducible |",
        "|------|-----------|------|--------|----------|---------|-------------|---------|--------------|",
    ]
    for item in results:
        latency = f"{item.latency_ms} ms" if item.latency_ms is not None else "-"
        lines.append(
            f"| {item.test_id} | {item.capability} | {item.tool} | {item.result} | "
            f"{item.observed} | {latency} | {item.error_class or '-'} | "
            f"{item.design_impact} | {item.reproducible or '-'} |"
        )
    lines.append("")
    lines.append("## Details")
    lines.append("")
    for item in results:
        lines.append(f"### {item.test_id} — {item.capability}")
        lines.append("")
        lines.append(f"- Result: {item.result}")
        lines.append(f"- Observed: {item.observed}")
        if item.latency_ms is not None:
            lines.append(f"- Latency: {item.latency_ms} ms")
        if item.error_class:
            lines.append(f"- Error class: {item.error_class}")
        lines.append(f"- Design impact: {item.design_impact}")
        lines.append(f"- Reproducible: {item.reproducible or 'n/a'}")
        lines.append(f"- Timestamp (UTC): {item.timestamp_utc}")
        if item.details:
            lines.append("- Extra:")
            for key, value in item.details.items():
                lines.append(f"  - {key}: {value}")
        lines.append("")

    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    return json_path, md_path
