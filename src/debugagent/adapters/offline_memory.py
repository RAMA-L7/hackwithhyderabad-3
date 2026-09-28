"""Offline memory adapter: a pre-MK9 stand-in for Rama's layer so the CLI can be rehearsed without Hindsight.

Serves canned MemoryViews from a JSON file ({"views": [{"match": [terms], "view": {...}}], "default": {...}};
the first entry whose terms all appear in the query wins) and appends retained cases to a local JSONL
file. It is not Hindsight and does not validate cases like Rama's layer, and says so in every retention
reason. MK9 replaces it with the Hindsight adapter.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from debugagent.domain.errors import MemoryFailure


class OfflineMemoryPort:
    def __init__(self, views_path: str | Path, retained_path: str | Path):
        data = self._load(Path(views_path))
        self.views = data["views"]
        self.default = data["default"]
        self.retained_path = Path(retained_path)

    @staticmethod
    def _load(path: Path) -> dict:
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            raise MemoryFailure("unavailable", f"offline memory file not found: {path}") from None
        except json.JSONDecodeError as exc:
            raise MemoryFailure("schema", f"offline memory file is not JSON: {exc}") from None
        if not isinstance(data, dict) or not isinstance(data.get("views"), list) or "default" not in data:
            raise MemoryFailure("schema", "offline memory file needs 'views' (list) and 'default'")
        return data

    def recall_and_classify(self, query: str) -> dict:
        text = query.lower()
        for entry in self.views:
            if all(term.lower() in text for term in entry.get("match", [])):
                return copy.deepcopy(entry["view"])
        return copy.deepcopy(self.default)

    def retain(self, case: dict) -> dict:
        self.retained_path.parent.mkdir(parents=True, exist_ok=True)
        with self.retained_path.open("a") as handle:
            handle.write(json.dumps(case) + "\n")
        count = len(self.retained_path.read_text().splitlines())
        return {"retained": True,
                "reason": f"written to the offline file {self.retained_path} (not Hindsight; not validated by the memory layer)",
                "memory_case_id": f"offline-{count}", "validated": False, "case_key": None}
