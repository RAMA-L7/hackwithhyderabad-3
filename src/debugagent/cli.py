"""debugagent CLI entry point: parse arguments, wire the app, hand off to a controller.

    PYTHONPATH=src python3 -m debugagent.cli debug      # interactive session
    PYTHONPATH=src python3 -m debugagent.cli inspect    # trace of the last session
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from debugagent.adapters.env_file import load_env_file
from debugagent.adapters.llm import LLMRouter
from debugagent.adapters.session_store import SessionStore
from debugagent.app import DEFAULT_MEMORY, build_investigation, build_memory
from debugagent.controllers.debug_controller import DebugController
from debugagent.controllers.inspect_controller import InspectController
from debugagent.controllers.terminal_engineer import TerminalEngineer
from debugagent.domain.errors import DebugAgentError
from debugagent.logging_setup import configure_logging


def parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="debugagent", description="Memory-informed debugging assistant")
    commands = parser.add_subparsers(dest="command", required=True)
    debug = commands.add_parser("debug", help="investigate one issue interactively")
    debug.add_argument("--env", default=".env.live", help="env file with LLM settings (default .env.live)")
    debug.add_argument("--memory", default=DEFAULT_MEMORY, help=f"memory source (default {DEFAULT_MEMORY})")
    commands.add_parser("inspect", help="print the trace of the last session")
    return parser.parse_args(argv)


def main(argv=None, *, ask=input, out=sys.stdout, err=sys.stderr, memory=None, llm=None,
         state_dir: str | Path = ".debugagent") -> int:
    """`memory` and `llm` can be injected (tests); otherwise they are built from --memory and --env."""
    args = parse_args(argv)
    store = SessionStore(state_dir)
    if args.command == "inspect":
        return InspectController(store, out).run()

    configure_logging(stream=err)
    try:
        if llm is None:
            load_env_file(args.env)
            llm = LLMRouter.from_env()
        memory = memory or build_memory(args.memory, Path(state_dir))
    except DebugAgentError as exc:
        print(f"error: {exc}", file=err)
        return 1
    return DebugController(build_investigation(memory, llm), TerminalEngineer(ask, out), store, err).run()


if __name__ == "__main__":
    sys.exit(main())
