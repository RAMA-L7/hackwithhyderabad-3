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
                         worker_options: dict | None = None,
                         on_complete: Callable[[Any], None] | None = None,
                         ) -> Callable[[Any, str, dict], tuple[str, SessionRecord, threading.Thread]]:
    """Build the callable the server needs to start an investigation.

    The returned factory matches the server's `build_investigation(handler, description, options)`
    signature and returns `(session_id, record, thread)`.

    `on_complete` is called with the finished `Session`, or with `None` if the run raised, so a failed
    run cannot be mistaken for an empty one and cannot overwrite a good record. It is optional, and it is
    called on the session's own thread, so a slow writer delays nothing that the engineer is waiting on
    except the final trace line.

    ## `worker_options`: how the worker stage becomes reachable from a host

    Without this, a host cannot run the worker stage at all. `investigate()` takes a `repository` and
    the target lists, but a server builds sessions from an HTTP body, and a `RepositoryRuntime` is a
    live object rather than anything JSON could carry. So the two halves come from different parties:
    the host passes `worker_options` holding a real repository runtime and any targets every session
    should inspect, and the request body may add to the target lists.

    `repository` is deliberately NOT overridable from the request. It is the one value here that names a
    filesystem boundary, and a browser must not be able to choose which directory an investigation
    reads. Only the target lists are taken from the request, and they are repository-relative paths the
    `RepositoryScope` validates anyway - stringified here because they arrived as JSON.
    """
    counter = itertools.count(1)
    base_options = dict(worker_options or {})

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
        # Host-supplied configuration first, then whatever the request added. Only the target lists are
        # read from the request; `repository` stays the host's, because it is the filesystem boundary.
        merged = dict(base_options)
        for key in ("verifier_targets", "sdc_targets"):
            supplied = (options or {}).get(key)
            if supplied:
                merged[key] = tuple(str(entry) for entry in supplied)

        kwargs: dict[str, Any] = {"coordinator": coordinator, "session_id": session_id}

        # Which targets exist is the caller's decision, not this factory's. A target list is passed
        # through verbatim and a repository runtime is attached only when there is something to read -
        # building one for a session that asked for no inspection would put a `RepositoryScope` on a
        # session that never touches a file.
        #
        # The two lists are kept apart rather than merged into one `inspect` list, because they are
        # routed to DIFFERENT workers: `verifier_targets` go to the Code/Log Verifier, `sdc_targets` to
        # the SDC Analyzer. Merging them here would mean this file knew which target kind needed which
        # worker, and the application layer would be making a routing decision that belongs to the
        # pipeline. It hands over two named lists and `plan_tasks` decides who gets what.
        verifier_targets = tuple(merged.get("verifier_targets") or ())
        sdc_targets = tuple(merged.get("sdc_targets") or ())
        if verifier_targets or sdc_targets:
            kwargs["repository"] = merged.get("repository")
            if merged.get("repository") is None:
                # Targets were named but no repository was mounted, so there is nothing to read them
                # from. Refused loudly rather than run: a silent no-op here would look exactly like a
                # worker stage that ran and found nothing.
                raise ValueError(
                    "worker targets were supplied but no repository runtime is mounted; pass "
                    "worker_options={'repository': ...} to make_session_factory")
            if verifier_targets:
                kwargs["verifier_targets"] = verifier_targets
            if sdc_targets:
                kwargs["sdc_targets"] = sdc_targets

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
