"""Live flow test: the real CLI, real LLM providers (.env.live), offline memory file. Not part of unittest
discovery (it spends real LLM calls, about 5). Run before demos:

    PYTHONPATH=src python3 tests/live_flows.py
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import loop_support  # noqa: F401  (path wiring)
from debugagent.adapters.env_file import load_env_file
from debugagent.adapters.llm import LLMRouter
from debugagent.cli import main

ROOT = Path(__file__).resolve().parents[1]
ACT1 = ["checkout-api returns 500 on every payment after the Monday deploy", "service=checkout-api", "",
        "error rate went from 0% to 100% at 09:14", ""]
ACT2 = ["photo-api resets connections on uploads over 2 MB behind nginx", "service=photo-api proxy=nginx-1.24", "",
        "20/20 uploads of 2.5MB fail with ECONNRESET; 1MB uploads succeed", ""]
RESOLVED = ["y", "raised nginx client_max_body_size to 10m", "no resets above 2 MB in a 20-run sweep",
            "reverse proxy request body limit", "resolved", "raised client socket timeout | proxy closed the stream first",
            "", "log://photo-api/2026-09-28/nginx-error.log", ""]


class Engineer:
    """Answers prompts by what they ask, so the flow does not depend on how many hypotheses come back."""

    def __init__(self, description, facts=(), claim="supported", resolution=RESOLVED):
        self.script = list(description) + list(facts) + [""]
        self.claim, self.resolution, self.resolving = claim, list(resolution), False

    def __call__(self, prompt: str) -> str:
        if "resolve the issue" in prompt:
            self.resolving = True
        if self.resolving:
            return self.resolution.pop(0)
        for key, answer in (("support or contradict", self.claim), ("past case relevant", "y"),
                            ("Your decision", "accept"), ("Note", "")):
            if key in prompt:
                return answer
        if not self.script:
            raise EOFError
        return self.script.pop(0)


def run(name, argv, engineer, llm=None):
    state = Path(tempfile.mkdtemp(prefix=f"flow-{name}-"))
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, ask=engineer, out=out, err=err, llm=llm, state_dir=state)
    retained_file = state / "offline-retained.jsonl"
    retained = [json.loads(line) for line in retained_file.read_text().splitlines()] if retained_file.exists() else []
    return code, out.getvalue(), err.getvalue(), retained, state


def section(text, title):
    start = text.find(f"{title}  ·")
    return text[start:text.find("━━━━\n", text.find("\n", start) + 80)] if start >= 0 else ""


def flow_a():
    code, out, err, retained, _ = run("A", ["debug"], Engineer(ACT1))
    proposal = out.split("PROPOSAL  ·")[1].split("DECISION  ·")[0]
    return [("exit 0", code == 0, err[-200:]),
            ("memory abstained", "No usable memory" in out, ""),
            ("all hypotheses generic", proposal.count("[generic · no memory used]") >= 2 and "[memory-backed" not in proposal, ""),
            ("no citations", "cites: 3f9a" not in proposal and "cites: seed" not in proposal, ""),
            ("case retained", len(retained) == 1 and retained[0]["outcome"] == "resolved", ""),
            ("no unknown env stored", retained and None not in retained[0]["environment"].values(), "")]


def flow_b():
    code, out, err, retained, state = run("B", ["debug"], Engineer(ACT2))  # no runtime given: evidence gap
    proposal = out.split("PROPOSAL  ·")[1].split("DECISION  ·")[0]
    decision = out.split("DECISION  ·")[1].split("RETENTION  ·")[0]
    inspect_out = io.StringIO()
    main(["inspect"], out=inspect_out, state_dir=state)
    return [("exit 0", code == 0, err[-200:]),
            ("relevant past case shown", "[relevant] case 3f9a1c07b2e4d815" in out, ""),
            ("original environment shown", "original environment: service=media-uploader, proxy=nginx-1.25" in out, ""),
            ("differences stated", "service (then media-uploader, now photo-api)" in out, ""),
            ("memory-backed hypothesis cites it", "[memory-backed" in proposal and "cites: 3f9a1c07b2e4d815" in proposal, ""),
            ("current system described correctly", "photo-api" in proposal or "nginx-1.24" in proposal, ""),
            ("evidence gap -> insufficient evidence", "evidence insufficient evidence" in decision, ""),
            ("differing fields flagged", "differs from the cited case" in decision, ""),
            ("failed approach stored", retained and {"approach": "raised client socket timeout",
                                                     "why_failed": "proxy closed the stream first"} in retained[0]["failed_approaches"], ""),
            ("inspect shows trace", "retention: retained=True" in inspect_out.getvalue(), "")]


def flow_c(router):
    broken = LLMRouter(dataclasses.replace(router.primary, base_url="http://127.0.0.1:9"), router.fallback)
    code, out, err, retained, _ = run("C", ["debug"], Engineer(ACT2, facts=["runtime=node20", "region=eu-west-1"]), broken)
    return [("exit 0", code == 0, err[-200:]),
            ("primary failure logged", "error_class=UNREACHABLE" in err, ""),
            ("fallback served", f"served by {router.fallback.provider}" in out and "fallback_used=True" in out, ""),
            ("case retained", len(retained) == 1, "")]


def flow_d():
    code, out, err, retained, _ = run("D", ["debug"], Engineer(ACT2, resolution=["n"]))
    return [("exit 0", code == 0, err[-200:]),
            ("nothing retained", "Nothing retained: the engineer did not report a resolution" in out and not retained, "")]


def flow_e():
    code, out, err, retained, _ = run("E", ["debug", "--memory", "offline:/no/such/file.json"], Engineer(ACT2))
    return [("exit 1", code == 1, ""),
            ("one-line memory error", err.strip().endswith("error: memory unavailable: offline memory file not found: /no/such/file.json"), err[-200:]),
            ("never 'no memory'", "No usable memory" not in out and "Traceback" not in err, "")]


def flow_f(router):
    bad = LLMRouter(dataclasses.replace(router.primary, api_key="sk-invalid-flow-test"), router.fallback)
    code, out, err, retained, _ = run("F", ["debug"], Engineer(ACT2), bad)
    llm_lines = [line for line in err.splitlines() if "adapters.llm.router" in line]
    return [("exit 1", code == 1, ""),
            ("one-line auth error", "rejected the credentials" in err.strip().splitlines()[-1], err[-200:]),
            ("no failover on auth", len(llm_lines) == 1 and "error_class=AUTH" in llm_lines[0], str(llm_lines)),
            ("nothing retained", not retained, "")]


def main_flows() -> int:
    os.chdir(ROOT)
    load_env_file(ROOT / ".env.live")
    router = LLMRouter.from_env()
    flows = [("A  Act 1: no matching history", flow_a), ("B  Act 2: memory-backed, evidence gap", flow_b),
             ("C  primary down -> fallback", lambda: flow_c(router)), ("D  not resolved", flow_d),
             ("E  memory unavailable", flow_e), ("F  bad API key", lambda: flow_f(router))]
    failures = 0
    for title, flow in flows:
        print(f"\n{title}")
        for check, ok, detail in flow():
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  {check}" + (f"   <- {detail}" if not ok and detail else ""))
    print(f"\n{'ALL FLOWS PASS' if not failures else f'{failures} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main_flows())
