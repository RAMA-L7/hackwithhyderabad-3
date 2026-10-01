"""The composition root for a browser session: build the objects, start the thread, hand back the ids.

`web_server.py` deliberately does not know how to assemble an investigation - it moves bytes. This
module is where the pieces meet, and it is a sibling of the CLI's `debug` command rather than a
different way of doing it: both build one memory port, one Coordinator, one MemoryLane, one LLM
router, and then call `investigate()`. The only thing that differs is which `Engineer` is passed in.

That is the whole integration, and it is deliberately small. If wiring a browser had required touching
`Coordinator`, `MemoryLane`, the registry or `investigate()`, the milestone would have failed its own
architectural claim; instead this file is the only place in the codebase that knows both an
`Engineer` implementation and the composition root, and it adds no new seam.

## What the browser sends, and what it becomes

The form has one textarea. It is read with the same convention the terminal already teaches - the
first line is the summary, `key=value` on any line states an environment fact, every other line is a
symptom - because a second input format would be one more thing an engineer had to learn for no gain.
`normalize()` does the parsing and it does not invent: a key nobody stated stays unknown.
"""

from __future__ import annotations

import itertools
import threading
from typing import Any, Callable

from debugagent.application.web_engineer import (
    DEFAULT_DECISION_TIMEOUT_SECONDS,
    WebEngineer,
)
from debugagent.application.web_server import SessionRecord, SessionRegistry, _run_investigation
from debugagent.pipeline.normalize import normalize
from debugagent.pipeline.types import DebugInput

__all__ = ["parse_issue", "make_session_factory"]


def parse_issue(text: str) -> DebugInput:
    """Turn the form's textarea into a `DebugInput`, using the terminal's existing convention.

    Everything goes into `description`: `normalize()` takes the first line as the signature, reads
    `service=`/`runtime=`/`proxy=`/`region=` out of any line, and treats the remaining lines as
    symptoms. Splitting the textarea into separate description and measurement fields would change
    nothing downstream and would add a second format to learn.
    """
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        raise ValueError("describe the issue: the description is empty")
    return DebugInput(description="\n".join(lines))


def make_session_factory(*, registry: SessionRegistry, port: Any, llm: Any, coordinator: Any,
                         timeout: float = DEFAULT_DECISION_TIMEOUT_SECONDS,
                         on_complete: Callable[[Any], None] | None = None,
                         ) -> Callable[[Any, str, dict], tuple[str, SessionRecord, threading.Thread]]:
    """Build the callable the server needs to start an investigation.

    The returned factory matches the server's `build_investigation(handler, description, options)`
    signature and returns `(session_id, record, thread)`.

    `on_complete` is called with the finished `Session`, or with `None` if the run raised, so a failed
    run cannot be mistaken for an empty one and cannot overwrite a good record. It is optional, and it is
    called on the session's own thread, so a slow writer delays nothing that the engineer is waiting on
    except the final trace line.
    """
    counter = itertools.count(1)

    def build(handler: Any, description: str, options: dict) -> tuple[str, SessionRecord, threading.Thread]:
        # Everything that can refuse the request happens BEFORE the session is registered, so a
        # rejected request leaves nothing behind. `normalize()` is called here purely as that check:
        # it is the one place that decides whether an issue is usable, and duplicating its opinion
        # here would be how the browser and the terminal came to disagree about what is acceptable.
        raw = parse_issue(description)
        normalize(raw)
        session_id = f"s{next(counter)}"
        engineer = WebEngineer(session_id, timeout=timeout)
        record = registry.create(session_id, engineer)
        # The Session carries the SAME id as the registry key. Two ids for one investigation would
        # mean the UI and `debugagent inspect` disagreed about which session an engineer was looking
        # at, with nothing to reconcile them.
        kwargs: dict[str, Any] = {"coordinator": coordinator, "session_id": session_id}
        targets = options.get("verifier_targets") or ()
        if targets:
            kwargs["repository"] = options.get("repository")
            kwargs["verifier_targets"] = tuple(targets)

        def run() -> None:
            _run_investigation(record, raw=raw, port=port, llm=llm, kwargs=kwargs)
            if on_complete is not None:
                # Only a run that FINISHED is handed over. `record.session` is published on every stage
                # boundary so the console can show progress, which means after a crash it holds a
                # partial investigation; persisting that would overwrite the last good record with an
                # incomplete one that reads as if it concluded.
                on_complete(record.session if record.status == "complete" else None)

        thread = threading.Thread(target=run, daemon=True, name=f"investigation-{session_id}")
        thread.start()
        return session_id, record, thread

    return build
