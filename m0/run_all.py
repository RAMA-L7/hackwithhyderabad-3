"""M0 runner: executes every runtime verification test and writes an auditable result set."""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from m0.config import load_config, redact
from m0.hindsight_probe import run_all_hindsight
from m0.llm_probe import run_all_llm
from m0.results_recorder import BLOCKED, FAIL, PASS, write_results


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    config = load_config()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print("M0 runtime verification")
    print(f"run_id={run_id}")
    print(
        "config: "
        f"primary={config.primary.name}/{config.primary.model or '<unset>'} "
        f"key={redact(config.primary.api_key)} | "
        f"fallback={config.fallback.name}/{config.fallback.model or '<unset>'} "
        f"key={redact(config.fallback.api_key)} | "
        f"hindsight_url={'set' if config.hindsight_configured else '<unset>'} "
        f"bank={config.hindsight_bank_id or '<unset>'}"
    )
    print("-" * 72)

    results = run_all_llm(config) + run_all_hindsight(config)

    for item in results:
        latency = f" {item.latency_ms}ms" if item.latency_ms is not None else ""
        print(f"{item.test_id:<6} {item.result:<18} {item.capability}{latency}")
        print(f"       observed: {item.observed}")

    json_path, md_path = write_results(results, run_id)
    counts = {PASS: 0, FAIL: 0, BLOCKED: 0}
    for item in results:
        counts[item.result] = counts.get(item.result, 0) + 1
    print("-" * 72)
    print(f"PASS={counts[PASS]} FAIL={counts[FAIL]} BLOCKED={counts[BLOCKED]}")
    print(f"results_json={json_path}")
    print(f"results_md={md_path}")

    if counts[FAIL]:
        return 1
    if counts[BLOCKED]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
