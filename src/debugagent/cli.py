"""debugagent CLI (MK8, Hindsight memory wired at MK9).

    PYTHONPATH=src python3 -m debugagent.cli debug      # interactive session
    PYTHONPATH=src python3 -m debugagent.cli inspect    # trace of the last session

Memory is Hindsight Cloud by default (env: HINDSIGHT_URL / HINDSIGHT_API_KEY / HINDSIGHT_BANK_ID);
`--memory offline:<file>` rehearses the loop without a network. Every error reaches the engineer as
one line; memory and LLM failures are never shown as "no relevant memory" or "the model found nothing".
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from debugagent.llm import LLMError, LLMRouter
from debugagent.pipeline.evidence import EvidenceError
from debugagent.pipeline.investigate import Session, investigate
from debugagent.pipeline.memory_port import MemoryFailure, OfflineMemoryPort
from debugagent.pipeline.normalize import InputError, NormalizationError
from debugagent.pipeline.types import OUTCOME_CLASSES, DebugInput, Evidence, Hypothesis, SchemaError
from debugagent.pipeline.verify import EngineerDecision, VerificationError

DEFAULT_MEMORY = "hindsight"


class AbortSession(Exception):
    pass


def _use_utf8(*streams) -> None:
    """The four sections are drawn with box rules that a cp1252 console (the Windows default)
    cannot encode, which would crash the first render. Ask for UTF-8 and never fail if we cannot."""
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def load_env_file(path: str) -> None:
    """KEY=VALUE lines into os.environ. A non-empty value already in the shell wins."""
    file = Path(path)
    if not file.exists():
        raise InputError(f"env file not found: {path}")
    for line in file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if not os.environ.get(key.strip()):
                os.environ[key.strip()] = value.strip().strip('"').strip("'")


class TerminalEngineer:
    """The engineer at the keyboard. `ask` is input() in the terminal and a scripted feed in tests."""

    def __init__(self, ask=input, out=sys.stdout):
        self.ask = ask
        self.out = out

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def text(self, prompt: str, *, required: bool = False) -> str:
        while True:
            try:
                value = self.ask(prompt).strip()
            except EOFError:
                raise AbortSession("input ended before the session finished; nothing retained") from None
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

    def read_input(self) -> DebugInput:
        description = self.lines("Describe the issue (first line = short summary; key=value for service, runtime, "
                                 "proxy, region; blank line to finish):")
        measurements = self.lines("Measurements (one per line, blank line to finish):")
        return DebugInput.from_dict({"description": "\n".join(description), "measurements": measurements})

    # --- Engineer protocol ---
    def show(self, text: str) -> None:
        self.say()
        self.say(text)

    def current_facts(self, evidence: Evidence) -> list[tuple[str, str | None]]:
        hint = f" Unknown now: {', '.join(evidence.unknown_fields)}." if evidence.unknown_fields else ""
        known = evidence.known()
        facts: list[tuple[str, str | None]] = []
        prompt = f"Add current facts as name=value, or a plain sentence for an observation.{hint} (blank line to continue)"
        for line in self.lines(prompt):
            name, sep, value = (part.strip() for part in line.partition("="))
            key = name.lower()
            if not sep:
                facts.append(("observation", line))  # a sentence is an observation, never silently dropped
            elif not name:
                self.say(f"  skipped '{line}': use name=value")
            elif key != "observation" and key in known and value and value != known[key]:
                # a typo must not end the session; add_facts still fails closed if a conflict slips through
                self.say(f"  skipped '{line}': {key} is already '{known[key]}' from your description")
            else:
                facts.append((name, value or None))
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
        relevant = False
        if hypothesis.supporting_case_ids:
            relevant = self.choice("  Is the cited past case relevant to this issue?", ("y", "n")) == "y"
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
        failed = []
        for line in self.lines("Approaches that failed this session, as 'approach | why it failed' (blank line to finish):"):
            approach, _, why = line.partition("|")
            if approach.strip() and why.strip():
                failed.append({"approach": approach.strip(), "why_failed": why.strip()})
            else:
                self.say(f"  skipped '{line}': use 'approach | why it failed'")
        answers["failed_approaches"] = failed
        answers["evidence_refs"] = self.lines("Evidence references, e.g. log://service/2026-09-28/error.log "
                                              "(blank line to finish):")
        return answers


def make_port(spec: str, state_dir: Path):
    kind, _, target = spec.partition(":")
    if kind == "offline" and target:
        return OfflineMemoryPort(target, str(state_dir / "offline-retained.jsonl"))
    if kind == "hindsight":
        # MK9: the real Hindsight Cloud bank. Env supplies HINDSIGHT_URL / HINDSIGHT_API_KEY /
        # HINDSIGHT_BANK_ID. A missing or rejected configuration surfaces as MemoryFailure, which the
        # caller prints as one line. It is never downgraded to "no relevant memory".
        from debugagent.pipeline.memory_adapter import build_port
        return build_port()
    raise InputError(f"unknown --memory {spec!r}; use hindsight or offline:<file>")


def main(argv=None, *, ask=input, out=sys.stdout, err=sys.stderr, port=None, llm=None,
         state_dir: str | Path = ".debugagent") -> int:
    parser = argparse.ArgumentParser(prog="debugagent", description="Memory-informed debugging assistant")
    _use_utf8(out, err, sys.stdout, sys.stderr)
    commands = parser.add_subparsers(dest="command", required=True)
    debug = commands.add_parser("debug", help="investigate one issue interactively")
    debug.add_argument("--env", default=".env.live", help="env file with LLM settings (default .env.live)")
    debug.add_argument("--memory", default=DEFAULT_MEMORY,
                       help="memory source: hindsight (default) or offline:<file>")
    commands.add_parser("inspect", help="print the trace of the last session")
    args = parser.parse_args(argv)
    state = Path(state_dir)
    last = state / "last-session.json"

    if args.command == "inspect":
        if not last.exists():
            print("no session recorded yet; run: debugagent debug", file=out)
            return 1
        session = json.loads(last.read_text())
        print(f"session {session['session_id']}", file=out)
        for line in session["trace"]:
            print(f"  - {line}", file=out)
        print(f"full record: {last}", file=out)
        return 0

    logging.basicConfig(level=logging.INFO, stream=err, format="  [%(name)s] %(message)s")
    engineer = TerminalEngineer(ask, out)

    def save(session: Session) -> None:
        state.mkdir(parents=True, exist_ok=True)
        last.write_text(json.dumps(session.to_dict(), indent=2, default=str))

    owns_port = False
    try:
        if llm is None:
            load_env_file(args.env)
            llm = LLMRouter.from_env()
        if port is None:
            port = make_port(args.memory, state)
            owns_port = True
        raw = engineer.read_input()
        session = investigate(raw, port, llm, engineer, on_step=save)
    except KeyboardInterrupt:
        print("\naborted; nothing retained", file=err)
        return 130
    except (AbortSession, InputError, NormalizationError, SchemaError, EvidenceError, VerificationError,
            MemoryFailure, LLMError) as exc:
        # one line, always: backend messages can carry newlines and raw HTTP headers (Hindsight 504, 2026-09-28)
        print(f"error: {' '.join(str(exc).split())[:300]}", file=err)
        return 1
    finally:
        # Release the backend's HTTP session on every exit path. The Hindsight client opens an
        # aiohttp session on first use; without this it is only reclaimed at interpreter exit, which
        # prints "Unclosed client session" / "Unclosed connector". Only a port this function created
        # is closed - an injected port belongs to the caller.
        if owns_port and port is not None:
            from debugagent.pipeline.memory_adapter import close_port
            close_port(port)
    engineer.say(f"session {session.session_id} saved to {last}; run 'inspect' for the trace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
