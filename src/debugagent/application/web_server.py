"""A local browser console over one engineering investigation. Standard library only.

`http.server` and nothing else: no Flask, no FastAPI, no npm, no new dependency. The page itself lives
in `console.py` as a single self-contained document served inline, so there is no build step, no asset
pipeline and nothing to install before the console runs.

## What the console is for

It makes the architecture inspectable while it runs. The engineer sees MEMORY, then EVIDENCE, then
WORKERS, then REASONING, and answers VALIDATION, and only then sees a DECISION - and every panel states
which authority produced its contents. That ordering is what the architecture already does; the console
refuses to hide it.

## Endpoints

    GET  /                          the console (inline HTML/CSS/JS)
    GET  /api/health                 liveness, and the decision options the console offers
    GET  /api/sessions               list running sessions
    POST /api/sessions               start an investigation; returns immediately
    GET  /api/sessions/<id>          the session's view model, plus any pending question
    POST /api/sessions/<id>/facts    supply engineer facts
    POST /api/sessions/<id>/decision answer a pending decision or resolution
    POST /api/sessions/<id>/abandon  release a blocked investigation without answering

`POST /api/sessions` starts the investigation on its own thread and returns at once. The browser then
polls `GET /api/sessions/<id>`, which is what makes the synchronous decision loop usable: a poll sees a
pending question, the engineer answers, and the blocked `decide()` wakes up. Polling rather than
websockets is a deliberate choice - the state is polled anyway, and a push channel would add a
dependency for no gain at this size.

## Trust boundary

The server's job is to move untrusted browser input to the `Engineer` protocol and untrusted-safe
session output back. It has no privileged path into the investigation:

  - facts go to `current_facts()`, and `add_facts` decides whether each is accepted;
  - a decision goes to `decide()`, which checks the value and the question it answers before `verify()`
    ever sees it;
  - a resolution goes to `resolve()` and is validated by `build_resolution`;
  - the response is `build_view_model(session)`, a projection of data that already exists.

There is deliberately no endpoint that writes evidence, sets a verification status, applies a patch or
touches the memory bank, because there is no such capability to expose.

## Local-only, unauthenticated

The server binds 127.0.0.1. There is no authentication, no CSRF token and no rate limiting. This is a
local engineering tool for a machine the engineer already controls; exposing it beyond loopback would
make it a service, which is a different piece of work with a different threat model.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from debugagent.application.console import CONSOLE_HTML
from debugagent.application.view_model import build_view_model
from debugagent.application.web_engineer import ENGINEER_DECISIONS, WebEngineer

#: Bound to loopback only. See the module docstring.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

#: How many sessions the process will hold. A local tool needs a bound, because sessions are in memory
#: and nothing evicts them.
MAX_SESSIONS = 32


class SessionRecord:
    """One investigation: the engineer, the session as it stands, and the thread running it.

    `session` is set by `publish()` on every stage boundary, not only when the run finishes. That is what
    makes the console a console rather than a waiting room: without it the browser can only show a
    question with no investigation behind it, because `investigate()` has not returned a session yet and
    there is nothing to project. `investigate()` already offers the hook for this - `on_step(session)`,
    documented as the way a caller persists progress - so publishing here adds no new seam.

    `done` is separate from `session` because a session exists while the run is still in flight, and
    "has a session" must not be read as "has finished".
    """

    def __init__(self, session_id: str, engineer: WebEngineer):
        self.session_id = session_id
        self.engineer = engineer
        self.session: Any = None
        self.error: str = ""
        self.done = False
        self.thread: threading.Thread | None = None
        self.started = threading.Event()

    def publish(self, session: Any) -> None:
        """Record the session as it stands. Called on every stage boundary."""
        self.session = session

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        return "complete" if self.done else "running"

    def snapshot(self) -> dict:
        """The view model plus whatever question the investigation is blocked on."""
        payload: dict[str, Any] = {"status": self.status}
        if self.error:
            payload["error"] = self.error
        if self.session is not None and self.status != "failed":
            # A run that raised produced no session, so there is nothing to project. A run still in
            # flight has a partial one, and showing the stages that have completed is the whole point.
            model = build_view_model(self.session,
                                     status="complete" if self.status == "complete" else "running")
            payload["view"] = model.to_dict()
        else:
            payload["view"] = None
        pending = self.engineer.pending
        payload["pending"] = pending.to_dict() if pending is not None else None
        payload["outcome"] = self.engineer.outcome
        payload["transcript_lines"] = len(self.engineer.transcript)
        return payload


class SessionRegistry:
    """In-memory session records, keyed by id. Bounded, and deliberately not persistent."""

    def __init__(self, limit: int = MAX_SESSIONS):
        self._records: dict[str, SessionRecord] = {}
        self._lock = threading.Lock()
        self.limit = limit

    def create(self, session_id: str, engineer: WebEngineer) -> SessionRecord:
        with self._lock:
            if len(self._records) >= self.limit:
                raise RuntimeError(f"session limit reached ({self.limit}); restart the server to clear")
            record = SessionRecord(session_id, engineer)
            self._records[session_id] = record
            return record

    def get(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            return self._records.get(session_id)

    def ids(self) -> list[str]:
        with self._lock:
            return sorted(self._records)


def _run_investigation(record: SessionRecord, *, raw: Any, port: Any, llm: Any,
                       kwargs: dict[str, Any]) -> None:
    """Run the investigation on its own thread, exactly as the CLI would.

    This function is the entire integration. It builds the session through `investigate()` with the
    `WebEngineer` in the `engineer` position, and does nothing else - which is the proof that the UI
    needed no change to the investigation architecture.

    The one thing it adds is `on_step`, so the console can project the session while it is still being
    built. `investigate()` already offers that parameter and calls it with the same `Session` it is
    mutating, so this publishes a reference rather than a copy: no stage is re-run, no state is
    reconstructed, and nothing about the investigation changes.
    """
    from debugagent.pipeline.investigate import investigate

    # Chain rather than replace: a host may pass its own `on_step` (to persist a session record, say)
    # and the console needs the same hook to show progress.
    caller_step = kwargs.pop("on_step", None)

    def on_step(session) -> None:
        record.publish(session)
        if caller_step is not None:
            caller_step(session)

    kwargs["on_step"] = on_step
    try:
        session = investigate(raw, port, llm, record.engineer, **kwargs)
        record.publish(session)
        record.done = True
        record.engineer.finish("resolved" if session.resolution is not None else "declined")
    except BaseException as exc:  # noqa: BLE001 - reported to the browser, not swallowed
        record.error = f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"
        record.done = True
        record.engineer.finish("declined")
    finally:
        record.started.set()


def create_handler(registry: SessionRegistry, *, build_investigation):
    """Build the request handler bound to a registry and a session factory.

    `build_investigation(record, description, options)` must return `(raw, port, llm, kwargs)` for a new
    investigation. Injecting it keeps the server testable without a real memory bank or a real model.
    """

    class Handler(BaseHTTPRequestHandler):
        server_version = "DebugAgentUI/1.0"

        # -- plumbing ------------------------------------------------------
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - silence stderr spam
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload, sort_keys=True).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _send_html(self, body: str) -> None:
            self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._send_json(status, {"error": message})

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("Content-Length is not a number")
            if length < 0 or length > 1_000_000:
                raise ValueError("request body is too large")
            if length == 0:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"request body is not valid JSON: {exc}")
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            return payload

        # -- routing -------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802 - http.server's naming
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/":
                self._send_html(CONSOLE_HTML)
                return
            if path == "/api/health":
                self._send_json(200, {"ok": True, "decisions": list(ENGINEER_DECISIONS),
                                      "sessions": registry.ids()})
                return
            if path == "/api/sessions":
                self._send_json(200, {"sessions": registry.ids()})
                return
            if path.startswith("/api/sessions/"):
                session_id = path[len("/api/sessions/"):]
                record = registry.get(session_id)
                if record is None:
                    self._error(404, f"no session '{session_id}'")
                    return
                self._send_json(200, {"session_id": session_id, **record.snapshot()})
                return
            self._error(404, "not found")

        def do_POST(self) -> None:  # noqa: N802 - http.server's naming
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            try:
                payload = self._read_json()
            except ValueError as exc:
                self._error(400, str(exc))
                return

            if path == "/api/sessions":
                self._start(payload)
                return

            if path.startswith("/api/sessions/"):
                rest = path[len("/api/sessions/"):]
                session_id, _, action = rest.partition("/")
                record = registry.get(session_id)
                if record is None:
                    self._error(404, f"no session '{session_id}'")
                    return
                self._action(record, action, payload)
                return

            self._error(404, "not found")

        # -- actions -------------------------------------------------------
        def _start(self, payload: dict) -> None:
            description = str(payload.get("description", "") or "").strip()
            if not description:
                self._error(400, "description is required")
                return
            try:
                session_id, record, thread = build_investigation(
                    self, description, payload.get("options") or {})
            except Exception as exc:  # noqa: BLE001 - a bad request must not kill the server
                self._error(400, f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}")
                return
            record.thread = thread
            self._send_json(202, {"session_id": session_id, "status": "running"})

        def _action(self, record: SessionRecord, action: str, payload: dict) -> None:
            engineer = record.engineer
            pending = engineer.pending
            # The token the answer was written against, when the client sends one. The engineer refuses
            # an answer composed for a question the investigation has already moved past.
            question = payload.get("question")
            question = question if isinstance(question, str) else None
            try:
                if action == "facts":
                    stored = engineer.submit_facts(payload.get("facts") or {})
                    self._send_json(200, {"facts": stored})
                elif action == "decision":
                    decision = payload.get("decision")
                    if pending is not None and pending.kind == "resolve":
                        engineer.submit_resolution(
                            decision.get("resolution") if isinstance(decision, dict) else None,
                            question=question)
                    else:
                        engineer.submit_decision(
                            decision, note=str(payload.get("note", "") or ""),
                            claim=payload.get("claim"), confirmed=payload.get("confirmed"),
                            question=question)
                    self._send_json(200, {"answered": True})
                elif action == "abandon":
                    engineer.abandon()
                    self._send_json(200, {"abandoned": True})
                else:
                    self._error(404, f"unknown action '{action}'")
            except ValueError as exc:
                # A refused payload is the trust boundary working, not a server fault.
                self._error(400, str(exc))

    return Handler


def make_server(registry: SessionRegistry, build_investigation, *,
                host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Create the server. Binding is the caller's business; the default is loopback."""
    handler = create_handler(registry, build_investigation=build_investigation)
    return ThreadingHTTPServer((host, port), handler)


def serve(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          build_investigation=None, registry: SessionRegistry | None = None) -> None:
    """Run the UI until interrupted.

    `build_investigation(handler, description, options)` must return
    `(session_id, SessionRecord, Thread)`. The host supplies the real one, wiring the memory port, the
    LLM router and the Coordinator exactly as it does for a terminal session; see `host.py`.

    `registry` is accepted so the caller can build the factory against the SAME registry the server will
    serve from. Without this the factory would have to be handed the registry after the server had
    already been created with a different one, and the two would silently disagree about which sessions
    exist.
    """
    if build_investigation is None:
        raise ValueError("build_investigation is required: the server does not know how to start a session")
    if registry is None:
        registry = SessionRegistry()
    server = make_server(registry, build_investigation, host=host, port=port)
    address = server.server_address
    print(f"engineering debug UI on http://{address[0]}:{address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
