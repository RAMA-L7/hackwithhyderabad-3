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

from debugagent.application.host import make_session_factory
from debugagent.application.web_engineer import DEFAULT_DECISION_TIMEOUT_SECONDS
from debugagent.application.web_server import DEFAULT_HOST as DEFAULT_UI_HOST
from debugagent.application.web_server import DEFAULT_PORT as DEFAULT_UI_PORT
from debugagent.application.web_server import SessionRegistry, serve as serve_ui
from debugagent.agents.memory_specialist import MemorySpecialist
from debugagent.composition import CompositionError, build_runtime
from debugagent.llm import LLMError, LLMRouter
from debugagent.pipeline.evidence import EvidenceError
from debugagent.pipeline.ingest import load_debug_input
from debugagent.pipeline.investigate import Session, investigate
from debugagent.pipeline.memory_port import MemoryFailure, OfflineMemoryPort
from debugagent.pipeline.normalize import InputError, NormalizationError
from debugagent.pipeline.types import OUTCOME_CLASSES, DebugInput, Evidence, Hypothesis, SchemaError
from debugagent.pipeline.verify import EngineerDecision, VerificationError

DEFAULT_MEMORY = "hindsight"


def _add_memory_argument(parser) -> None:
    parser.add_argument("--memory", default=DEFAULT_MEMORY,
                        help="memory source: hindsight (default) or offline:<file>")


def _add_worker_arguments(parser) -> None:
    """Repository and target flags, in the vocabulary the roster already uses.

    Nothing here names a domain. `--verifier-target` is "a file the Code/Log Verifier should inspect"
    and `--sdc-target` is "a constraint file the SDC Analyzer should inspect"; both are lists of paths
    that `RepositoryScope` will validate, and which worker reads them is the pipeline's decision, made
    in `plan_tasks`. The UI does not know what an SDC target means, and neither does this parser.

    `action="append"` rather than a comma-split string, because a path may contain a comma and because
    repeating a flag is the form every other multi-value tool in this repo already uses.
    """
    parser.add_argument("--repository", metavar="DIR",
                        help="repository root for worker inspection; mounts the worker stage")
    parser.add_argument("--verifier-target", metavar="PATH", action="append", default=[],
                        help="file for a worker to inspect (repeatable; needs --repository)")
    parser.add_argument("--sdc-target", metavar="PATH", action="append", default=[],
                        help="constraint file for the SDC Analyzer (repeatable; needs --repository)")


def _worker_options(args, coordinator, err):
    """Build the repository runtime and the per-session worker options, or `(None, {})`.

    Returns the runtime so the caller can decide whether it owns it. Mounting is the whole point of the
    flag: without a repository there is no `SourcePort`, no `RepositoryScope` and therefore no way to
    keep a read inside the tree an engineer named - so a target without a repository is refused rather
    than quietly dropped, because a silently dropped target looks identical to a worker that ran and
    found nothing.
    """
    from debugagent.composition import build_repository_runtime

    targets = tuple(args.verifier_target or ()) + tuple(args.sdc_target or ())
    if not args.repository:
        if targets:
            print("error: --verifier-target/--sdc-target need --repository DIR", file=err)
            raise ValueError("worker targets supplied without --repository")
        return None, {}

    # Shares the session Coordinator on purpose. Two Coordinators would mean two `MemoryLane`s over one
    # client, which is the P5 hazard reintroduced by composition; `investigate()` refuses it loudly too,
    # but refusing before the port is built is cheaper and clearer.
    runtime = build_repository_runtime(args.repository, coordinator=coordinator)
    return runtime, {"repository": runtime,
                     "verifier_targets": tuple(args.verifier_target or ()),
                     "sdc_targets": tuple(args.sdc_target or ())}


def _serve_ui(args, state: Path, last: Path, save, out, err, *, llm, port) -> int:
    """Run the browser UI. Same composition root as `debug`, different Engineer.

    The server has no idea how to assemble an investigation - `host.py` does that, and this is
    where the real port, LLM and Coordinator are handed to it. Nothing here is new wiring: it is
    the same three objects the terminal path builds, in the same order, for the same reasons.
    """
    registry = SessionRegistry()
    owns_port = False
    repository = None
    try:
        if llm is None:
            load_env_file(args.env)
            llm = LLMRouter.from_env()
        if port is None:
            port = make_port(args.memory, state)
            owns_port = True
        runtime = build_runtime(port, memory_specialist=MemorySpecialist(port))
        # The repository runtime shares the session Coordinator. Both are closed in `finally`, and
        # `owns_repository` records that the CLI built this one rather than being handed it.
        repository, worker_options = _worker_options(args, runtime.coordinator, err)
        factory = make_session_factory(registry=registry, port=port, llm=llm,
                                       coordinator=runtime.coordinator,
                                       timeout=args.decision_timeout,
                                       worker_options=worker_options,
                                       on_complete=lambda session: save(session) if session else None)
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print(f"warning: {args.host} is not loopback. The UI has no login, no CSRF token"
                  f" and no rate limiting; anything beyond this machine can drive it.", file=err)
        serve_ui(host=args.host, port=args.port, build_investigation=factory, registry=registry)
    except KeyboardInterrupt:
        print("\nui stopped; nothing retained", file=err)
        return 130
    except (InputError, NormalizationError, SchemaError, EvidenceError, VerificationError,
            MemoryFailure, LLMError, CompositionError, OSError) as exc:
        print(f"error: {' '.join(str(exc).split())[:300]}", file=err)
        return 1
    finally:
        if owns_port and port is not None:
            runtime.close()
    return 0


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
    _add_worker_arguments(debug)
    debug.add_argument("--input", metavar="FILE",
                       help="read the issue from a .json/.yaml file instead of the first two prompts")
    _add_memory_argument(debug)
    ui = commands.add_parser("ui", help="investigate issues in a local browser UI")
    _add_worker_arguments(ui)
    ui.add_argument("--env", default=".env.live", help="env file with LLM settings (default .env.live)")
    ui.add_argument("--host", default=DEFAULT_UI_HOST,
                    help=f"interface to bind (default {DEFAULT_UI_HOST})")
    ui.add_argument("--port", type=int, default=DEFAULT_UI_PORT,
                    help=f"port to listen on (default {DEFAULT_UI_PORT})")
    ui.add_argument("--decision-timeout", type=float, default=DEFAULT_DECISION_TIMEOUT_SECONDS,
                    help="seconds a blocked question waits for the engineer before the run is abandoned")
    _add_memory_argument(ui)
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

    def save(session: Session) -> None:
        state.mkdir(parents=True, exist_ok=True)
        last.write_text(json.dumps(session.to_dict(), indent=2, default=str))

    if args.command == "ui":
        return _serve_ui(args, state, last, save, out, err, llm=llm, port=port)

    engineer = TerminalEngineer(ask, out)

    owns_port = False
    repository = None
    try:
        if llm is None:
            load_env_file(args.env)
            llm = LLMRouter.from_env()
        if port is None:
            port = make_port(args.memory, state)
            owns_port = True
        # THE composition root for memory objects. One port -> one Coordinator -> one MemoryLane, built
        # here and injected below, rather than assembled per session by whoever calls `investigate()`.
        # `build_runtime` is idempotent per client, so a host process that drives several sessions
        # through this same function gets one lane for all of them; and a second Coordinator over the
        # same client is refused rather than silently allowed to defeat the serial memory lane.
        runtime = build_runtime(port, memory_specialist=MemorySpecialist(port))
        if args.input:
            raw = load_debug_input(args.input)
            engineer.say(f"Issue loaded from {args.input}: {raw.description.splitlines()[0]}")
        else:
            raw = engineer.read_input()
        # Mounted here for the same reason the UI mounts it: `investigate()` needs a live
        # `RepositoryRuntime` to give a worker a `SourcePort`, and there is no other way to get one.
        # `_worker_options` returns `{}` when no repository was asked for, so a plain run is unchanged.
        repository, worker_options = _worker_options(args, runtime.coordinator, err)
        session = investigate(raw, port, llm, engineer, on_step=save, coordinator=runtime.coordinator,
                              **worker_options)
    except KeyboardInterrupt:
        print("\naborted; nothing retained", file=err)
        return 130
    except (AbortSession, InputError, NormalizationError, SchemaError, EvidenceError, VerificationError,
            MemoryFailure, LLMError, CompositionError) as exc:
        # one line, always: backend messages can carry newlines and raw HTTP headers (Hindsight 504, 2026-09-28)
        print(f"error: {' '.join(str(exc).split())[:300]}", file=err)
        return 1
    finally:
        # Release the backend's HTTP session on every exit path. The Hindsight client opens an
        # aiohttp session on first use; without this it is only reclaimed at interpreter exit, which
        # prints "Unclosed client session" / "Unclosed connector". Only a port this function created
        # is closed - an injected port belongs to the caller.
        if owns_port and port is not None:
            # Unbind before closing: the binding is weak, but dropping it explicitly keeps the
            # registry empty for a process that composes more than one runtime in turn.
            runtime.close()
    engineer.say(f"session {session.session_id} saved to {last}; run 'inspect' for the trace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
