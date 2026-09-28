"""`debug` command: read the issue, run one investigation, save the session, report errors as one line."""

from __future__ import annotations

from typing import TextIO

from debugagent.adapters.session_store import SessionStore
from debugagent.controllers.terminal_engineer import TerminalEngineer
from debugagent.domain.errors import DebugAgentError
from debugagent.services.investigation_service import InvestigationService


class DebugController:
    def __init__(self, investigation: InvestigationService, engineer: TerminalEngineer, store: SessionStore,
                 err: TextIO):
        self.investigation = investigation
        self.engineer = engineer
        self.store = store
        self.err = err

    def run(self) -> int:
        try:
            raw = self.engineer.read_input()
            session = self.investigation.run(raw, self.engineer, on_step=self.store.save)
        except KeyboardInterrupt:
            print("\naborted; nothing retained", file=self.err)
            return 130
        except DebugAgentError as exc:
            print(f"error: {exc}", file=self.err)
            return 1
        self.engineer.say(f"session {session.session_id} saved to {self.store.path}; run 'inspect' for the trace")
        return 0
