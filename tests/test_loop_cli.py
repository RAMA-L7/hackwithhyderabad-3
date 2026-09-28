"""MK8: the CLI through scripted input: four sections in order, one-line errors, inspect, offline memory."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

import loop_support  # noqa: F401  (path wiring)
from loop_support import RELEVANT_VIEW, FakeLLM, FakeMemoryPort, hyp
from debugagent.cli import main
from debugagent.pipeline.memory_port import MemoryFailure

REL = "3f9a1c07b2e4d815"
SCRIPT = [
    "photo-api resets uploads over 2 MB", "service=photo-api proxy=nginx-1.24", "",   # description
    "20/20 2.5MB uploads fail", "",                                                    # measurements
    "runtime=python3.11", "region=eu-west-1", "",                                      # current facts
    "supported", "y", "accept", "limit is 2m here too",                                # H1
    "contradicted", "reject", "",                                                      # H2 (generic: no relevance question)
    "y", "raised client_max_body_size to 10m", "no resets above 2 MB", "reverse proxy body limit", "resolved",
    "raised client timeout | proxy closed first", "",                                  # failed approaches
    "log://photo-api/2026-09-28/nginx-error.log", "",                                  # evidence refs
]


def feed(lines):
    it = iter(lines)

    def ask(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return ask


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv, lines, port=None, llm=None):
        out, err = io.StringIO(), io.StringIO()
        code = main(argv, ask=feed(lines), out=out, err=err, port=port, state_dir=self.state,
                    llm=llm or FakeLLM({"hypotheses": [hyp(cites=[REL]), hyp(text="app-side limit")]}))
        return code, out.getvalue(), err.getvalue()

    def test_full_session(self):
        port = FakeMemoryPort(RELEVANT_VIEW)
        code, out, err = self.run_cli(["debug"], SCRIPT, port=port)
        self.assertEqual(code, 0, err)
        order = [out.index(f"{name}  ·") for name in ("MEMORY", "EVIDENCE", "PROPOSAL", "DECISION", "RETENTION")]
        self.assertEqual(order, sorted(order))
        self.assertIn("fallback_used=False", out)
        self.assertIn("H1 [memory-backed]", out)
        self.assertNotIn("verified", out.split("PROPOSAL  ·")[1].split("DECISION  ·")[0].lower().replace("unverified", ""))
        self.assertEqual(port.retained[0]["outcome"], "resolved")
        saved = json.loads((self.state / "last-session.json").read_text())
        self.assertEqual(saved["retention"]["memory_case_id"], "fake-fact-1")

        code, out, _ = self.run_cli(["inspect"], [])
        self.assertEqual(code, 0)
        self.assertIn("retention: retained=True", out)

    def test_input_ending_early_is_one_line_and_retains_nothing(self):
        port = FakeMemoryPort(RELEVANT_VIEW)
        code, out, err = self.run_cli(["debug"], SCRIPT[:9], port=port)
        self.assertEqual(code, 1)
        self.assertEqual(err.strip().splitlines()[-1], "error: input ended before the session finished; nothing retained")
        self.assertEqual(port.retained, [])
        self.assertIn("proposed", "".join(json.loads((self.state / "last-session.json").read_text())["trace"]))

    def test_memory_failure_is_one_line_error(self):
        code, _, err = self.run_cli(["debug"], SCRIPT, port=FakeMemoryPort(fail=MemoryFailure("unavailable", "bank down")))
        self.assertEqual(code, 1)
        self.assertIn("error: memory unavailable: bank down", err)
        self.assertNotIn("Traceback", err)

    def test_offline_memory_file(self):
        repo = Path(__file__).resolve().parents[1]
        code, out, err = self.run_cli(["debug", "--memory", f"offline:{repo / 'demo/offline-memory.json'}"], SCRIPT)
        self.assertEqual(code, 0, err)
        self.assertIn("bank 'offline-demo'", out)
        self.assertIn("not Hindsight", out)
        self.assertEqual(len((self.state / "offline-retained.jsonl").read_text().splitlines()), 1)

    def test_inspect_without_session(self):
        code, out, _ = self.run_cli(["inspect"], [])
        self.assertEqual(code, 1)
        self.assertIn("no session recorded yet", out)

    def test_bad_answer_is_re_asked(self):
        lines = SCRIPT[:8] + ["maybe"] + SCRIPT[8:]
        code, out, err = self.run_cli(["debug"], lines, port=FakeMemoryPort(RELEVANT_VIEW))
        self.assertEqual(code, 0, err)
        self.assertIn("choose one of: supported, contradicted", out)


if __name__ == "__main__":
    unittest.main()
