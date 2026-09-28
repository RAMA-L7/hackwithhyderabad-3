"""The engineer at the keyboard: implements the engineer port over a terminal.

`ask` is input() in a real terminal and a scripted feed in tests. All prompting lives here; no business
rule does. Output goes through the views, so a hypothesis is never styled as verified.
"""

from __future__ import annotations

import sys
from typing import Callable, TextIO

from debugagent.domain.errors import SessionAborted
from debugagent.domain.investigation import EngineerDecision, Session
from debugagent.domain.models import OUTCOME_CLASSES, DebugInput, Evidence, Hypothesis
from debugagent.views.sections import render_stage

Ask = Callable[[str], str]


class TerminalEngineer:
    def __init__(self, ask: Ask = input, out: TextIO = sys.stdout):
        self.ask = ask
        self.out = out

    # ---- terminal primitives ----
    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def text(self, prompt: str, *, required: bool = False) -> str:
        while True:
            try:
                value = self.ask(prompt).strip()
            except EOFError:
                raise SessionAborted("input ended before the session finished; nothing retained") from None
            if value or not required:
                return value
            self.say("  an answer is required")

    def choice(self, prompt: str, options: tuple[str, ...]) -> str:
        while True:
            value = self.text(f"{prompt} [{'/'.join(options)}]: ").lower()
            if value in options:
                return value
            self.say(f"  choose one of: {', '.join(options)}")

    def lines(self, prompt: str) -> list[str]:
        self.say(prompt)
        collected = []
        while value := self.text("> "):
            collected.append(value)
        return collected

    # ---- session input ----
    def read_input(self) -> DebugInput:
        description = self.lines("Describe the issue (first line = short summary; key=value for service, runtime, "
                                 "proxy, region; blank line to finish):")
        measurements = self.lines("Measurements (one per line, blank line to finish):")
        return DebugInput.from_dict({"description": "\n".join(description), "measurements": measurements})

    # ---- EngineerPort ----
    def report(self, stage: str, session: Session) -> None:
        self.say()
        self.say(render_stage(stage, session))

    def current_facts(self, evidence: Evidence) -> dict[str, str | None]:
        hint = f" Unknown now: {', '.join(evidence.unknown_fields)}." if evidence.unknown_fields else ""
        facts: dict[str, str | None] = {}
        for line in self.lines(f"Add current facts as name=value.{hint} (blank line to continue)"):
            name, sep, value = line.partition("=")
            if sep and name.strip():
                facts[name.strip()] = value.strip() or None
            else:
                self.say(f"  skipped '{line}': use name=value")
        return facts

    def decide(self, hypothesis: Hypothesis, mismatched: list[str], missing: list[str]) -> EngineerDecision:
        self.say()
        self.say(f"Verify {hypothesis.ref}: {hypothesis.hypothesis}")
        if mismatched:
            self.say(f"  differs from the cited past case: {', '.join(mismatched)}")
        claim = None
        if missing:
            self.say(f"  current evidence lacks: {', '.join(missing)}; status will be insufficient_evidence")
        else:
            claim = self.choice("  Does current evidence support or contradict it?", ("supported", "contradicted"))
        relevant = bool(hypothesis.supporting_case_ids) and \
            self.choice("  Is the cited past case relevant to this issue?", ("y", "n")) == "y"
        decision = self.choice("  Your decision", ("accept", "modify", "reject"))
        return EngineerDecision(decision, claim, relevant, self.text("  Note (optional): "))

    def resolve(self, session: Session) -> dict | None:
        self.say()
        if self.choice("Did you resolve the issue?", ("y", "n")) == "n":
            return None
        answers = {
            "action_taken": self.text("What did you do? ", required=True),
            "observed_result": self.text("What did you observe after it? ", required=True),
            "root_cause_confirmed": self.text("Confirmed root cause (blank if not confirmed): ") or None,
            "outcome": self.choice("Outcome", OUTCOME_CLASSES),
        }
        answers["failed_approaches"] = self._failed_approaches()
        answers["evidence_refs"] = self.lines("Evidence references, e.g. log://service/2026-09-28/error.log "
                                              "(blank line to finish):")
        return answers

    def _failed_approaches(self) -> list[dict]:
        failed = []
        for line in self.lines("Approaches that failed this session, as 'approach | why it failed' (blank line to finish):"):
            approach, _, why = (part.strip() for part in line.partition("|"))
            if approach and why:
                failed.append({"approach": approach, "why_failed": why})
            else:
                self.say(f"  skipped '{line}': use 'approach | why it failed'")
        return failed
