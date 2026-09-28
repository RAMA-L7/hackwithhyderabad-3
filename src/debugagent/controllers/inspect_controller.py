"""`inspect` command: print the trace of the last session."""

from __future__ import annotations

from typing import TextIO

from debugagent.adapters.session_store import SessionStore


class InspectController:
    def __init__(self, store: SessionStore, out: TextIO):
        self.store = store
        self.out = out

    def run(self) -> int:
        session = self.store.load_last()
        if session is None:
            print("no session recorded yet; run: debugagent debug", file=self.out)
            return 1
        print(f"session {session['session_id']}", file=self.out)
        for line in session["trace"]:
            print(f"  - {line}", file=self.out)
        print(f"full record: {self.store.path}", file=self.out)
        return 0
