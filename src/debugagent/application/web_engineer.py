"""`WebEngineer`: the existing `Engineer` protocol, served to a browser.

This is the whole point of the milestone, so it is worth stating plainly:

    investigate(raw, port, llm, engineer)   <- engineer is a WebEngineer

Nothing in `investigate()` knows a UI exists. The protocol was already the seam - `show`,
`current_facts`, `decide`, `resolve` - and this module is simply another implementation of it, a sibling
of the CLI's engineer rather than a special case inside the flow. There is no `if web_ui:` anywhere in
the core, and there must never be one.

## The synchronous decision loop, and what it costs

`decide()` and `resolve()` block until the browser answers. The investigation runs on its own thread,
so the run continues while the engineer reads; but the *request* that would answer it blocks for as
long as the engineer takes. That is the accepted UI-1 limitation:

    UI-1 limitation: interactive decisions are synchronous. A request stays active while the
    investigation waits for Engineer.decide(). Suspend/resume is deliberately out of scope, because
    it would turn investigate() into a resumable state machine - a different architecture.

If the interaction model later proves pause/resume is necessary, that becomes its own milestone with its
own decision, not something smuggled in here.

## What a browser is allowed to do

Very little, and all of it goes through the existing validators:

  - it may supply facts, which `add_facts` then accepts or refuses (it cannot overwrite a stated fact);
  - it may return a decision, which `verify()` validates as a decision and nothing else;
  - it may supply a resolution, which `build_resolution` validates.

The UI is untrusted input on the same footing as engineer terminal input. There is no path by which a
posted payload becomes evidence or a verification status; `Evidence` is built only from the case and
`current_facts`, and a verification status only from a validated decision.
"""

from __future__ import annotations

import threading
from typing import Any

#: How long a blocked decision waits before the investigation gives up on the browser. Without this a
#: closed tab would hold a session thread forever. Exceeding it is reported to the investigation as a
#: refusal, never as an answer.
DEFAULT_DECISION_TIMEOUT_SECONDS = 300.0

#: Decisions the engineer may return. Mirrors `verify.ENGINEER_DECISIONS`; an unlisted value is rejected
#: by `verify()` itself, and this constant exists so the UI can offer exactly the legal set.
ENGINEER_DECISIONS = ("accept", "modify", "reject")


class DecisionTimeout(RuntimeError):
    """The engineer did not answer within the timeout. Raised, never converted into a decision."""


class DecisionRequest:
    """A question the investigation is currently blocked on.

    Published so the UI can render the question and the evidence behind it. The question carries the
    mismatch and gap lists because the engineer needs them to answer - hiding them would make the
    decision uninformed, which is worse than a slow one.
    """

    def __init__(self, kind: str, session_id: str, hypothesis_ref: str = "",
                 statement: str = "", mismatched: tuple[str, ...] = (),
                 missing: tuple[str, ...] = (), options: tuple[str, ...] = ()):
        self.kind = kind
        self.session_id = session_id
        self.hypothesis_ref = hypothesis_ref
        self.statement = statement
        self.mismatched = tuple(mismatched)
        self.missing = tuple(missing)
        self.options = tuple(options)

    @property
    def token(self) -> str:
        """Identifies THIS question, and only this one.

        Sent to the browser and required back with the answer. A kind check alone cannot catch the case
        that actually happens in a polling UI: the engineer reads H2, and by the time they press Accept
        the run has moved to H3. Both are `decide`, so the kind matches and the answer lands on the
        wrong hypothesis. The token names the question, so that answer is refused instead of applied.
        """
        return f"{self.kind}:{self.hypothesis_ref}"

    def to_dict(self) -> dict:
        """The question as the browser sees it.

        `mismatched` and `missing` are always present, even when empty. Every other field of this
        record is unconditional, and a gap list that vanishes when it happens to be empty would leave a
        client unable to tell "there are no gaps" from "the server did not report them" - which is the
        difference between an informed decision and a blind one.
        """
        record = {"kind": self.kind, "session_id": self.session_id, "token": self.token,
                  "options": list(self.options),
                  "mismatched": list(self.mismatched), "missing": list(self.missing)}
        if self.hypothesis_ref:
            record["hypothesis_ref"] = self.hypothesis_ref
        if self.statement:
            record["statement"] = self.statement
        return record


class WebEngineer:
    """An `Engineer` whose answers come from a browser.

    Implements `show`, `current_facts`, `decide` and `resolve` - nothing more. It deliberately does not
    implement anything that would let a browser reach past the flow: no evidence access, no session
    mutation, no dispatch, no memory.
    """

    def __init__(self, session_id: str, *, timeout: float = DEFAULT_DECISION_TIMEOUT_SECONDS):
        self.session_id = session_id
        self.timeout = timeout
        self.transcript: list[str] = []
        self.facts: dict[str, str | None] = {}
        self._lock = threading.Lock()
        self._pending: DecisionRequest | None = None
        self._answer: Any = None
        self._answered = threading.Event()
        self._finished = threading.Event()
        self._done_status = "declined"

    # -- Engineer.show -------------------------------------------------------
    def show(self, text: str) -> None:
        """Record a rendered section. The investigation pushes; this only listens."""
        with self._lock:
            self.transcript.append(text)

    # -- Engineer.current_facts ---------------------------------------------
    def current_facts(self, evidence: Any) -> dict[str, str | None]:
        """Facts the engineer supplied through the UI.

        `evidence` is accepted and ignored: the investigation already knows it, and a UI that tried to
        derive facts from it would be inventing the engineer's answers for them.
        """
        with self._lock:
            return dict(self.facts)

    # -- Engineer.decide -----------------------------------------------------
    # -- Engineer.decide -----------------------------------------------------
    def decide(self, hypothesis: Any, mismatched: list[str], missing: list[str]):
        """Block until the engineer answers for this hypothesis.

        The answer is not re-validated here: `submit_decision` is the one place a decision value is
        checked, and `verify()` remains the backstop that decides what a decision MEANS. Re-checking an
        already-checked value would only add a second place to keep in step.
        """
        from debugagent.pipeline.verify import EngineerDecision

        answer = self._await(
            DecisionRequest(
                kind="decide",
                session_id=self.session_id,
                hypothesis_ref=str(getattr(hypothesis, "ref", "")),
                statement=str(getattr(hypothesis, "hypothesis", "") or getattr(hypothesis, "statement", "")),
                mismatched=tuple(mismatched or ()),
                missing=tuple(missing or ()),
                options=ENGINEER_DECISIONS,
            ))
        decision = str(answer.get("decision", ""))
        note = str(answer.get("note", "") or "")
        confirmed = bool(answer.get("confirmed", decision == "accept"))
        return EngineerDecision(decision, answer.get("claim"), confirmed, note)

    # -- Engineer.resolve ----------------------------------------------------
    def resolve(self, session: Any) -> dict | None:
        """Block until the engineer supplies a resolution, or declines to."""
        answer = self._await(DecisionRequest(kind="resolve", session_id=self.session_id))
        if answer is None or answer.get("resolution") is None:
            self._done_status = "declined"
            return None
        self._done_status = "resolved"
        resolution = answer["resolution"]
        if not isinstance(resolution, dict):
            raise ValueError("resolution: expected an object or null")
        return dict(resolution)

    # -- UI-facing surface --------------------------------------------------
    @property
    def pending(self) -> DecisionRequest | None:
        """The question the investigation is blocked on, or None."""
        with self._lock:
            return self._pending

    @property
    def awaiting(self) -> bool:
        with self._lock:
            return self._pending is not None and not self._answered.is_set()

    @property
    def outcome(self) -> str:
        """`resolved`, `declined` or `running` - how the engineer left the session."""
        if self._done_status != "running":
            return self._done_status
        return "declined" if self._finished.is_set() else "running"

    def submit_facts(self, facts: Any) -> dict[str, str | None]:
        """Accept engineer facts. Values must be strings or null; anything else is refused."""
        if not isinstance(facts, dict):
            raise ValueError("facts: expected an object")
        cleaned: dict[str, str | None] = {}
        for name, value in facts.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("facts: keys must be non-empty strings")
            if value is None:
                cleaned[name.strip().lower()] = None
            elif isinstance(value, str):
                cleaned[name.strip().lower()] = value.strip() or None
            else:
                raise ValueError(f"facts.{name}: expected a string or null, got {type(value).__name__}")
        with self._lock:
            self.facts = cleaned
        return dict(cleaned)

    def submit_decision(self, decision: Any, *, note: str = "", claim: Any = None,
                        confirmed: bool | None = None, question: str | None = None) -> None:
        """Answer the pending decision. Refuses if nothing is pending or the value is illegal.

        `decision` may be a bare decision string or the whole answer object; `note`, `claim` and
        `confirmed` may arrive either as keywords or inside that object, and a keyword wins. Both
        routes are accepted because the HTTP handler receives one and a library caller the other, and
        a field that was silently dropped on one of them would be a field the engineer believes they
        supplied and the investigation never received.

        `question` is the `DecisionRequest.token` the answer was written against. When supplied it must
        match the question that is actually open, so an answer composed for a question that has since
        been superseded is refused rather than applied to the next one.
        """
        if isinstance(decision, str):
            decision = {"decision": decision}
        if not isinstance(decision, dict):
            raise ValueError("decision: expected an object or a decision string")
        self._check_question(question)
        value = str(decision.get("decision", ""))
        if value not in ENGINEER_DECISIONS:
            raise ValueError(f"decision: '{value}' is not one of {list(ENGINEER_DECISIONS)}")
        if claim is None:
            claim = decision.get("claim")
        if confirmed is None:
            confirmed = decision.get("confirmed")
        if not note:
            note = str(decision.get("note", "") or "")
        payload = {"decision": value, "note": str(note or "")}
        if claim is not None:
            if claim not in ("supported", "contradicted"):
                raise ValueError(f"claim: '{claim}' is not one of ['supported', 'contradicted']")
            payload["claim"] = claim
        if confirmed is not None:
            payload["confirmed"] = bool(confirmed)
        self._deliver(payload)

    def submit_resolution(self, resolution: Any, *, question: str | None = None) -> None:
        """Answer the pending resolution. `None` declines, which ends the session unresolved.

        `question` carries the same correlation guarantee as `submit_decision`.
        """
        if resolution is not None and not isinstance(resolution, dict):
            raise ValueError("resolution: expected an object or null")
        self._check_question(question)
        self._deliver({"resolution": resolution})

    def abandon(self) -> None:
        """Release the investigation without an answer.

        Used when a session is cancelled. The pending call raises rather than returning a default,
        because a fabricated "reject" is indistinguishable from a real one to everything downstream.
        """
        with self._lock:
            if self._pending is not None and not self._answered.is_set():
                self._answer = DecisionTimeout("the engineer abandoned the session")
                self._answered.set()

    def finish(self, status: str = "complete") -> None:
        with self._lock:
            self._done_status = status
            self._finished.set()
            if self._pending is not None and not self._answered.is_set():
                self._answer = DecisionTimeout("the session ended before the engineer answered")
                self._answered.set()

    # -- internals ----------------------------------------------------------
    def _check_question(self, question: str | None) -> None:
        """Refuse an answer written against a question that is not the one now open.

        Cheap, and the alternative is worse than a wasted click: an answer computed from H2's evidence
        and applied to H3 would be recorded as the engineer's judgement on H3, and nothing downstream
        could ever tell that it was not.
        """
        if question is None:
            return
        with self._lock:
            if self._pending is None:
                raise ValueError("no decision is pending")
            if str(question) != self._pending.token:
                raise ValueError(
                    f"that answer is for question '{question}', but the open question is "
                    f"'{self._pending.token}': the investigation moved on before you answered")

    def _deliver(self, payload: Any) -> None:
        """Hand an answer to the blocked caller, if there is a question it can actually answer.

        Two refusals matter here, and the second is the one that is easy to miss. There is only one
        pending question at a time, so an answer that arrives for the PREVIOUS question - a stale tab, a
        double-clicked button, a resolution posted to a decision slot - would otherwise be accepted by
        whichever question happened to be open. A decision payload landing on a `resolve` question is
        the worst case: `resolve()` would read no `resolution` key, treat the engineer's answer as a
        refusal, and end a resolved investigation as declined. So the payload must match the kind of
        question it is answering.
        """
        with self._lock:
            if self._pending is None:
                raise ValueError("no decision is pending")
            if self._answered.is_set():
                raise ValueError("the pending question has already been answered")
            if not isinstance(payload, dict):
                raise ValueError("answer: expected an object")
            kind = self._pending.kind
            if kind == "resolve" and "resolution" not in payload:
                raise ValueError(f"the pending question is 'resolve'; this answer carries no resolution")
            if kind != "resolve" and "resolution" in payload:
                raise ValueError(f"the pending question is '{kind}'; a resolution does not answer it")
            self._answer = payload
            self._answered.set()

    def _await(self, request: DecisionRequest) -> Any:
        """Publish a question and block for its answer, or raise if the engineer never comes."""
        with self._lock:
            self._pending = request
            self._answered = threading.Event()
            self._answer = None
        if not self._answered.wait(timeout=self.timeout):
            with self._lock:
                self._pending = None
            raise DecisionTimeout(
                f"the engineer did not answer '{request.kind}' within {self.timeout:g}s")

        with self._lock:
            answer = self._answer
            self._pending = None
        if isinstance(answer, DecisionTimeout):
            raise answer
        return answer