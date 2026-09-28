"""Session store: the last session as JSON on local disk (for `inspect`). Git-ignored."""

from __future__ import annotations

import json
from pathlib import Path

from debugagent.domain.investigation import Session


class SessionStore:
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "last-session.json"

    def save(self, session: Session) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(session.to_dict(), indent=2, default=str))

    def load_last(self) -> dict | None:
        return json.loads(self.path.read_text()) if self.path.exists() else None
