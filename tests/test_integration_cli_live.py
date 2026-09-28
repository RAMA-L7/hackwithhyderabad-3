"""Live CLI integration: the real Hindsight port through the real debugagent CLI entry point.

Skipped unless HINDSIGHT_URL is configured. The LLM is stubbed so the run is deterministic; the
memory side, the classification, the abstention, the rendering and the retention gate are all real.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest

import support  # noqa: F401  (path wiring)
from loop_support import FakeLLM, hyp


def configured() -> bool:
    return bool(os.environ.get("HINDSIGHT_URL", "").strip())


# read_input description, blank, one measurement, blank, then no current facts, then
# claim/decision/note per hypothesis (choice() retries until it gets a valid value), then "not resolved".
def answers() -> list[str]:
    return (
        ["frontend css bundle is not loading on the marketing site", "",
         "the stylesheet returns 404 on the marketing site", "", ""]
        + ["supported", "accept", "not this one"] * 4
        + ["n"]
    )


@unittest.skipUnless(configured(), "HINDSIGHT_URL not configured")
class LiveCliTests(unittest.TestCase):
    def _run(self, query_lines: list[str], bank_id: str) -> str:
        from debugagent import cli

        state = tempfile.mkdtemp()
        script = answers()
        out = io.StringIO()
        llm = FakeLLM(
            {"hypotheses": [hyp(text="the css bundle is not built for this route"),
                            hyp(text="a service worker is serving a stale bundle"),
                            hyp(text="the CDN is serving a stale asset manifest")]},
        )
        code = cli.main(
            ["debug", "--memory", "hindsight", "--env", ".env"],
            ask=lambda prompt="": script.pop(0) if script else "",
            out=out,
            err=io.StringIO(),
            llm=llm,
            state_dir=state,
        )
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_cli_renders_four_sections_and_abstains_on_an_unrelated_issue(self):
        """Nothing in the bank resembles a frontend CSS problem, so memory must abstain and the
        proposal must stay generic."""
        os.environ["HINDSIGHT_BANK_ID"] = os.environ.get("HINDSIGHT_BANK_ID", "debugagent-demo")
        text = self._run([], os.environ["HINDSIGHT_BANK_ID"])
        for section in ("MEMORY", "EVIDENCE", "PROPOSAL", "DECISION", "RETENTION"):
            self.assertIn(section, text, f"{section} section missing")
        self.assertIn("No usable memory", text)
        self.assertIn("generic · no memory used", text)
        self.assertIn("Nothing retained", text)
        # no hypothesis may cite a case when memory abstained
        self.assertNotIn("cites: 2b", text)
        self.assertIn("the stylesheet returns 404", text)

    def test_cli_writes_a_session_record_that_inspect_can_read(self):
        from debugagent import cli

        state = tempfile.mkdtemp()
        script = answers()
        out = io.StringIO()
        llm = FakeLLM({"hypotheses": [hyp(), hyp(text="another cause")]})
        cli.main(["debug", "--memory", "hindsight", "--env", ".env"],
                 ask=lambda prompt="": script.pop(0) if script else "",
                 out=out, err=io.StringIO(), llm=llm, state_dir=state)
        record = os.path.join(state, "last-session.json")
        self.assertTrue(os.path.exists(record))
        out2 = io.StringIO()
        cli.main(["inspect"], out=out2, err=io.StringIO(), state_dir=state)
        self.assertIn("normalized:", out2.getvalue())


if __name__ == "__main__":
    unittest.main()
