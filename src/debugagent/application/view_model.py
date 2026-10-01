"""The session view model: a domain-neutral projection of one investigation, panel by panel.

A `Session` is the system's record of what happened. This module turns that record into something a
person - or a UI - can read, without adding a second source of truth. Every panel is a projection of
data that already exists; nothing here computes, judges, or infers.

## Why panels carry a trust level

Each panel declares what kind of claim its contents are, using the architecture's own vocabulary. That
is the point of the exercise: a reader should be able to see *which authority produced this* without
reading a caption, and the UI should never have to assert that in prose.

    KNOWLEDGE     recalled past cases; a record of something that happened elsewhere
    EVIDENCE      properties of the current system, with a source
    OBSERVATION   what a worker saw; not confirmed by anyone
    PROPOSAL      a candidate explanation or change; nobody has agreed to it
    DECISION      the engineer's judgement; the only conclusion
    TRACE         what the system DID; not a claim about the engineering problem at all

`TRACE` is deliberately outside the other five. A trace line says the system ran a stage, not that the
stage's output is true, so forcing it into one of the claim classes would be a lie in either direction.

A panel item may narrow its panel's trust, because one panel can hold more than one kind of thing: a
worker panel holds both what a verifier observed and what a patch generator proposed. The panel's trust
is the dominant class; the item's is the refinement.

An item states `trust` only when it DIFFERS from its panel. Restating the panel's own level would be
noise, and noise in a trust label is worse than no label: a reader cannot tell a deliberate refinement
from a copy, and so stops trusting either. The effective trust of an item is `item.trust or
panel.trust`, and that rule is what `PanelItem.trust is None` means.

## Absent means absent

A panel is included only when the record it projects exists. A session that ran no worker stage has no
Workers panel - it does not get an empty one, and it certainly does not get a panel saying there were
no VLSI workers. Absence, emptiness and "found nothing" are three different states and collapsing them
is how a UI starts telling an engineer stories the investigation never told:

    panel absent      the stage did not run
    panel empty       the stage ran and reported nothing
    panel with items  the stage ran and reported these

That distinction is also what keeps the UI domain-neutral. Nothing here mentions a domain, so a VLSI
session and a generic debugging session produce the same seven panel slots, and the ones that apply
differ because the session differs - not because the view model knows what a domain is.

## No domain knowledge, by construction

This module imports nothing from `debugagent.domains` and evaluates a session through its attributes
alone, so there is no code path by which a panel could acquire a domain-specific opinion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The authority a panel's contents speak with. `TRACE` is process, not a claim.
TRUST_LEVELS = ("KNOWLEDGE", "EVIDENCE", "OBSERVATION", "PROPOSAL", "DECISION", "TRACE")

#: The seven panels, in the order an engineer reads them.
#:
#: This is an INSPECTION order, not an execution order. The UI shows them top to bottom because that is
#: how a person reads an investigation; it does not claim the system runs them in this sequence.
PANEL_ORDER = ("Memory", "Evidence", "Workers", "Reasoning", "Validation", "Decision", "Trace")


class ViewModelError(ValueError):
    """A view model value is malformed. Fails closed, never coerced."""


def _require_str(value: Any, label: str, errors: list[str]) -> str:
    if not isinstance(value, str):
        errors.append(f"{label}: expected string, got {type(value).__name__}")
        return ""
    if not value.strip():
        errors.append(f"{label}: must not be empty")
    return value


@dataclass(frozen=True)
class PanelItem:
    """One line in a panel.

    `ref` is the item's own identifier - `H1`, a case id - and it is what makes an item individually
    addressable. It exists because of a concrete failure: with the identifier fused into `label` as
    `"H1: some text"`, nothing downstream can tell which item a control belongs to. A copy button
    cannot target one hypothesis, an accessible name cannot name it, and the only thing that appears to
    work is the first one. Keeping `ref` separate is what lets each item carry its own action and its
    own `aria-label`, and it keeps `label` copyable as the exact text a reader sees.

    `trust` refines the panel's own level and is `None` for the common case of an item that inherits it.
    `source` is shown wherever the underlying record carries one, because an observation without a source
    is an assertion.
    """

    label: str
    ref: str = ""
    detail: str = ""
    value: str | None = None
    source: str = ""
    trust: str | None = None

    def to_dict(self) -> dict:
        record = {"label": self.label}
        if self.ref:
            record["ref"] = self.ref
        if self.detail:
            record["detail"] = self.detail
        if self.value is not None:
            record["value"] = self.value
        if self.source:
            record["source"] = self.source
        if self.trust is not None:
            record["trust"] = self.trust
        return record


@dataclass(frozen=True)
class Panel:
    """A named group of items, with the authority its contents speak with."""

    name: str
    trust: str
    items: tuple[PanelItem, ...] = ()
    note: str = ""
    #: Present only when the panel is reporting that a stage could not run completely. Rendered
    #: prominently, because "complete and empty" and "could not be read" must never look alike.
    incomplete: bool = False

    FIELDS = frozenset({"name", "trust", "items", "note", "incomplete"})

    @property
    def empty(self) -> bool:
        return not self.items

    def to_dict(self) -> dict:
        record: dict[str, Any] = {"name": self.name, "trust": self.trust,
                                  "items": [item.to_dict() for item in self.items]}
        if self.note:
            record["note"] = self.note
        if self.incomplete:
            record["incomplete"] = True
        return record

    @classmethod
    def from_dict(cls, data: Any, label: str = "Panel", errors: list[str] | None = None) -> "Panel":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        unknown = sorted(key for key in data if key not in cls.FIELDS)
        if unknown:
            errors.append(f"{label}: unknown field(s) {unknown} (allowed: {sorted(cls.FIELDS)})")
        name = _require_str(data.get("name"), f"{label}.name", errors)
        trust = _require_str(data.get("trust"), f"{label}.trust", errors)
        if trust and trust not in TRUST_LEVELS:
            errors.append(f"{label}.trust: '{trust}' is not one of {list(TRUST_LEVELS)}")

        raw_items = data.get("items", [])
        if isinstance(raw_items, (str, bytes)) or not isinstance(raw_items, (list, tuple)):
            errors.append(f"{label}.items: expected array, got {type(raw_items).__name__}")
            raw_items = []
        items = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                errors.append(f"{label}.items[{index}]: expected object")
                continue
            item_trust = item.get("trust")
            if item_trust is not None and item_trust not in TRUST_LEVELS:
                errors.append(f"{label}.items[{index}].trust: '{item_trust}' is not one of "
                              f"{list(TRUST_LEVELS)}")
            items.append(PanelItem(
                label=_require_str(item.get("label"), f"{label}.items[{index}].label", errors),
                ref=str(item.get("ref", "") or ""),
                detail=item.get("detail", "") or "",
                value=item.get("value"),
                source=item.get("source", "") or "",
                trust=item_trust,
            ))
        return cls(name=name, trust=trust, items=tuple(items),
                   note=data.get("note", "") or "", incomplete=bool(data.get("incomplete", False)))

    @classmethod
    def parse(cls, data: Any, label: str = "Panel") -> "Panel":
        """Build a panel or raise. `from_dict` accumulates errors for a containing document to report
        together; `parse` is the single-value entry point and refuses on the first problem."""
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        if errors:
            raise ViewModelError(f"{label}: " + "; ".join(errors))
        return value


@dataclass(frozen=True)
class EnvironmentFact:
    """One environment field, and whether the engineer actually stated it.

    `stated` is kept as its own field rather than inferred from `value is None`, because "the engineer
    said the proxy is not nginx" and "the engineer never mentioned a proxy" are different facts and the
    difference is the whole point of not inventing evidence.
    """

    name: str
    value: str | None
    stated: bool

    FIELDS = frozenset({"name", "value", "stated"})

    def to_dict(self) -> dict:
        return {"name": self.name, "value": self.value, "stated": self.stated}

    @classmethod
    def from_dict(cls, data: Any, label: str, errors: list[str]) -> "EnvironmentFact":
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        unknown = sorted(key for key in data if key not in cls.FIELDS)
        if unknown:
            errors.append(f"{label}: unknown field(s) {unknown} (allowed: {sorted(cls.FIELDS)})")
        value = data.get("value")
        if value is not None and not isinstance(value, str):
            errors.append(f"{label}.value: expected string or null, got {type(value).__name__}")
            value = None
        stated = data.get("stated")
        if not isinstance(stated, bool):
            errors.append(f"{label}.stated: expected true or false, got {type(stated).__name__}")
            stated = False
        return cls(name=_require_str(data.get("name"), f"{label}.name", errors),
                   value=value, stated=stated)


@dataclass(frozen=True)
class CaseView:
    """What is being investigated: the statement, the symptoms, and the environment as stated.

    Projected separately from the panels so the header can show it. It is a projection of
    `session.case` alone and adds nothing; in particular it does not decide which evidence items are
    environment and which are observations, it reports the case's own environment dictionary as it is.
    """

    signature: str
    symptoms: tuple[str, ...] = ()
    environment: tuple[EnvironmentFact, ...] = ()

    FIELDS = frozenset({"signature", "symptoms", "environment"})

    def to_dict(self) -> dict:
        return {"signature": self.signature, "symptoms": list(self.symptoms),
                "environment": [fact.to_dict() for fact in self.environment]}

    @classmethod
    def from_dict(cls, data: Any, label: str = "case", errors: list[str] | None = None) -> "CaseView":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        unknown = sorted(key for key in data if key not in cls.FIELDS)
        if unknown:
            errors.append(f"{label}: unknown field(s) {unknown} (allowed: {sorted(cls.FIELDS)})")
        raw_symptoms = data.get("symptoms", [])
        if isinstance(raw_symptoms, (str, bytes)) or not isinstance(raw_symptoms, (list, tuple)):
            errors.append(f"{label}.symptoms: expected array, got {type(raw_symptoms).__name__}")
            raw_symptoms = []
        raw_environment = data.get("environment", [])
        if isinstance(raw_environment, (str, bytes)) or not isinstance(raw_environment, (list, tuple)):
            errors.append(f"{label}.environment: expected array, got {type(raw_environment).__name__}")
            raw_environment = []
        return cls(
            signature=_require_str(data.get("signature"), f"{label}.signature", errors),
            symptoms=tuple(str(item) for item in raw_symptoms),
            environment=tuple(EnvironmentFact.from_dict(item, f"{label}.environment[{index}]", errors)
                              for index, item in enumerate(raw_environment)))


@dataclass(frozen=True)
class SessionViewModel:
    """One investigation, projected for reading.

    `panels` holds only the panels that have something to project. `panel(name)` returning `None` is
    meaningful - it means that stage did not run - so callers must not treat it as an empty panel.
    """

    session_id: str
    title: str
    panels: tuple[Panel, ...] = ()
    #: Set when the investigation itself could not be completed. Distinct from "no findings", and it is
    #: the difference between a session that went well and one that did not happen.
    status: str = "running"
    #: `None` when no case was normalised yet. Omitted from `to_dict()` when absent so a view model of a
    #: session that has not started keeps the same shape it had before this field existed.
    case: CaseView | None = None

    FIELDS = frozenset({"session_id", "title", "panels", "status", "case"})

    #: `complete` means the flow finished. `running` while it is in flight. `failed` means it raised.
    STATUSES = ("running", "complete", "failed")

    def panel(self, name: str) -> Panel | None:
        """The named panel, or None when that stage did not run."""
        for panel in self.panels:
            if panel.name == name:
                return panel
        return None

    @property
    def panel_names(self) -> tuple[str, ...]:
        return tuple(panel.name for panel in self.panels)

    def to_dict(self) -> dict:
        record: dict[str, Any] = {"session_id": self.session_id, "title": self.title,
                                  "status": self.status,
                                  "panels": [panel.to_dict() for panel in self.panels]}
        if self.case is not None:
            record["case"] = self.case.to_dict()
        return record

    @classmethod
    def from_dict(cls, data: Any, label: str = "SessionViewModel",
                  errors: list[str] | None = None) -> "SessionViewModel":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        unknown = sorted(key for key in data if key not in cls.FIELDS)
        if unknown:
            errors.append(f"{label}: unknown field(s) {unknown} (allowed: {sorted(cls.FIELDS)})")
        status = _require_str(data.get("status", "running"), f"{label}.status", errors)
        if status and status not in cls.STATUSES:
            errors.append(f"{label}.status: '{status}' is not one of {list(cls.STATUSES)}")

        raw_panels = data.get("panels", [])
        if isinstance(raw_panels, (str, bytes)) or not isinstance(raw_panels, (list, tuple)):
            errors.append(f"{label}.panels: expected array, got {type(raw_panels).__name__}")
            raw_panels = []
        panels = [Panel.from_dict(item, f"{label}.panels[{index}]", errors)
                  for index, item in enumerate(raw_panels)]
        names = [panel.name for panel in panels]
        if len(set(names)) != len(names):
            errors.append(f"{label}.panels: duplicate panel names {sorted({n for n in names if names.count(n) > 1})}")

        return cls(session_id=_require_str(data.get("session_id"), f"{label}.session_id", errors),
                   title=_require_str(data.get("title"), f"{label}.title", errors),
                   panels=tuple(panels), status=status,
                   case=None if "case" not in data else CaseView.from_dict(
                       data["case"], f"{label}.case", errors))

    @classmethod
    def parse(cls, data: Any, label: str = "SessionViewModel") -> "SessionViewModel":
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        if errors:
            raise ViewModelError(f"{label}: " + "; ".join(errors))
        return value


def _text(value: Any) -> str:
    """Render any record value as a string, without inventing one for an absent value."""
    if value is None:
        return "(not stated)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _memory_panel(session: Any) -> Panel | None:
    """What was recalled. KNOWLEDGE: a record of other investigations, never a fact about this one."""
    memory = getattr(session, "memory", None)
    delegation = getattr(session, "memory_delegation", None)
    if memory is None and delegation is None:
        return None

    items: list[PanelItem] = []
    note = ""

    if memory is not None:
        abstention = getattr(memory, "abstention", None) or {}
        items.append(PanelItem(label="query", detail=str(getattr(memory, "query", "") or "")))
        if getattr(memory, "abstained", False):
            # Abstention is a result, not a gap. Saying so is the difference between "memory declined"
            # and "memory was never asked".
            reason = str(abstention.get("reason", "") or "")
            items.append(PanelItem(label="abstained", value="yes", detail=reason))
            note = "Memory abstained: no past case was close enough to act on."
        else:
            items.append(PanelItem(label="abstained", value="no"))
        for candidate in getattr(memory, "candidates", ()) or ():
            record = candidate if isinstance(candidate, dict) else {}
            case_id = str(record.get("case_id", "") or "")
            # Everything the recalled case itself recorded, so the engineer can judge the recall instead
            # of taking a relevance class on faith. `ref` is the case id, so the card is addressable and
            # the case id itself is selectable as text.
            environment = record.get("environment") or {}
            environment_text = ", ".join(
                f"{key}={environment[key] if environment[key] is not None else 'not stated'}"
                for key in sorted(environment)) or "no environment recorded"
            items.append(PanelItem(
                ref=case_id,
                label=case_id or "case",
                value=str(record.get("relevance_class", "")),
                detail=(f"outcome: {record.get('outcome', 'not recorded')} | environment: "
                        f"{environment_text} | recalled because: {record.get('reason', '') or 'no reason recorded'}"),
                source="memory"))
        excluded = getattr(memory, "excluded", ()) or ()
        if excluded:
            items.append(PanelItem(label="excluded", value=str(len(excluded)),
                                   detail="recalled but judged irrelevant"))

    if delegation is not None:
        status = str(delegation.get("status", "") or "")
        observations = delegation.get("observations") or []
        items.append(PanelItem(label="memory specialist", value=status,
                               detail=f"{len(observations)} observation(s)",
                               source="memory"))
        if delegation.get("failure_kind"):
            items.append(PanelItem(label="failure", value=str(delegation["failure_kind"]),
                                   detail=str(delegation.get("failure_detail", "") or ""),
                                   source="memory"))

    return Panel(name="Memory", trust="KNOWLEDGE", items=tuple(items), note=note)


def _evidence_panel(session: Any) -> Panel | None:
    """What is known about THIS system. EVIDENCE, and every item carries the source that produced it.

    A summary of what is NOT known goes in the panel's note rather than in the item list. It is an
    annotation about the panel, not a fact about the system, and letting it masquerade as an item would
    leave the panel holding a line with no source - which is exactly the shape of an assertion, and the
    one thing this panel must never contain.
    """
    evidence = getattr(session, "evidence", None)
    if evidence is None:
        return None
    items = []
    for item in getattr(evidence, "items", ()) or ():
        value = getattr(item, "value", None)
        items.append(PanelItem(
            label=str(getattr(item, "name", "")),
            value=None if value is None else str(value),
            detail="unknown" if value is None else "",
            source=str(getattr(item, "source", "") or "")))
    unknown = sorted(str(field) for field in (getattr(evidence, "unknown_fields", ()) or ()))
    note = ("Source of every item is shown. A worker observation is not in this panel."
            + (f" Not stated by the engineer: {', '.join(unknown)}." if unknown else ""))
    return Panel(name="Evidence", trust="EVIDENCE", items=tuple(items), note=note)


def _workers_panel(session: Any) -> Panel | None:
    """What the workers contributed. OBSERVATION, with proposals marked as proposals.

    The panel holds two kinds of content, so the patch proposals carry their own PROPOSAL trust rather
    than being filed under the panel's OBSERVATION.
    """
    workers = getattr(session, "workers", None)
    if workers is None:
        return None
    if not isinstance(workers, dict):
        return None

    items: list[PanelItem] = []

    verifier = workers.get("verifier")
    if isinstance(verifier, dict):
        findings = verifier.get("findings") or []
        summary = verifier.get("summary") or {}
        if findings:
            for finding in findings:
                items.append(PanelItem(
                    ref=str(finding.get("ref", "") or ""),
                    label=str(finding.get("message", "") or ""),
                    value=f"score {finding.get('score', '?')}",
                    source=str((finding.get("provenance") or {}).get("ref", "") or "")))
        else:
            items.append(PanelItem(label="code / log verifier",
                                   detail=f"no observation reported "
                                          f"({summary.get('total', 0)} task(s) completed)",
                                   source="worker"))
        refusals = verifier.get("refusals") or []
        for refusal in refusals:
            items.append(PanelItem(ref=str(refusal.get("task_id", "") or ""),
                                   label=f"refused: {refusal.get('task_id', '?')}",
                                   detail=str(refusal.get("error", "") or "")))
        if not summary.get("joined", True):
            items.append(PanelItem(label="join barrier did not hold",
                                   detail="a worker thread did not report"))

    patches = workers.get("patches")
    if isinstance(patches, dict):
        proposals = patches.get("proposals") or []
        for proposal in proposals:
            for observation in proposal.get("observations") or []:
                items.append(PanelItem(
                    ref=str(observation.get("ref", "") or ""),
                    label=str(observation.get("content", "") or ""),
                    value=f"proposal for {observation.get('ref', '?')}",
                    source=str(observation.get("ref", "") or ""),
                    trust="PROPOSAL"))
        if not proposals:
            items.append(PanelItem(label="patch generator",
                                   detail="no proposal produced", source="worker", trust="PROPOSAL"))

    if not items:
        return None
    return Panel(name="Workers", trust="OBSERVATION", items=tuple(items),
                 note="Observations are unconfirmed. Proposals are candidates; nothing is applied.")


def _reasoning_panel(session: Any) -> Panel | None:
    """The agent's candidate explanations. PROPOSAL - nobody has agreed to any of them.

    `label` is the hypothesis text and NOTHING else, and `ref` carries the identifier. That split is the
    whole reason each hypothesis can be copied, addressed and named on its own: with `H1:` prefixed onto
    the text, a copy of one hypothesis is indistinguishable from a copy of the next, and an
    `aria-label="Copy hypothesis H2"` has no way to say which card it belongs to. Metadata moves to
    `detail` where it is still readable but cannot be mistaken for the claim.
    """
    proposal = getattr(session, "proposal", None)
    if proposal is None:
        return None
    items = []
    for hypothesis in getattr(proposal, "hypotheses", ()) or ():
        cited = ", ".join(getattr(hypothesis, "supporting_case_ids", ()) or ()) or "none"
        refuted = "; ".join(getattr(hypothesis, "refutation_conditions", ()) or ()) or "none stated"
        items.append(PanelItem(
            ref=str(getattr(hypothesis, "ref", "") or ""),
            label=str(getattr(hypothesis, "hypothesis", "") or ""),
            value=str(getattr(hypothesis, "relevance_state", "")),
            detail=f"relevance: {getattr(hypothesis, 'relevance_state', '')} | cited: {cited} | "
                   f"next step: {getattr(hypothesis, 'recommended_next_step', '')} | "
                   f"would be refuted if: {refuted}",
            source="agent"))
    return Panel(name="Reasoning", trust="PROPOSAL", items=tuple(items),
                 note="Candidate explanations produced by the agent. None has been verified yet.")


def _validation_panel(session: Any) -> Panel | None:
    """What the engineer's decision was checked against. EVIDENCE-side, grouped so it cannot be misread.

    Three things are deliberately kept apart rather than merged into one sentence: the status the gate
    reached, the engineer's decision, and the environment fields that were in conflict. A single line
    reading "H1: supported, accept, conflicts: proxy" is true but unreadable, and an engineer who cannot
    tell which part is evidence and which part is their own decision cannot audit their own decision.

    `missing` evidence is NOT projected here, because a `VerificationResult` does not store it - it is
    computed per question by `evidence_gaps` and is only available while the question is open. Inventing
    it after the fact would be a claim the record does not support, so the live question carries it
    instead.
    """
    verifications = getattr(session, "verifications", None)
    if not verifications:
        return None
    items = []
    for verification in verifications:
        ref = str(getattr(verification, "hypothesis_ref", "") or "")
        items.append(PanelItem(
            ref=ref,
            label=str(getattr(verification, "status", "") or ""),
            value=str(getattr(verification, "engineer_decision", "")),
            detail=str(getattr(verification, "engineer_note", "") or "") or "no note recorded",
            source="engineer"))
        mismatched = tuple(getattr(verification, "mismatched_environment_fields", ()) or ())
        if mismatched:
            items.append(PanelItem(
                ref=ref, label="environment in conflict", value=", ".join(mismatched),
                detail="fields the cited past case recorded differently from, or not at all in, "
                       "current evidence", source="engineer", trust="OBSERVATION"))
        items.append(PanelItem(
            ref=ref, label="cited case still relevant",
            value=_text(getattr(verification, "relevance_confirmed", None)),
            source="engineer", trust="OBSERVATION"))
    return Panel(name="Validation", trust="EVIDENCE", items=tuple(items),
                 note="Each hypothesis is listed with the status the gate reached, the engineer's own "
                      "decision, and any environment fields in conflict. Missing evidence is shown on "
                      "the live question, which is the only place it is known.")


def _decision_panel(session: Any) -> Panel | None:
    """What the engineer concluded. DECISION - the only authority that concludes."""
    verifications = getattr(session, "verifications", None)
    resolution = getattr(session, "resolution", None)
    retention = getattr(session, "retention", None)
    if not verifications and resolution is None and retention is None:
        return None

    items = []
    for verification in verifications or ():
        items.append(PanelItem(
            ref=str(getattr(verification, "hypothesis_ref", "") or ""),
            label=str(getattr(verification, "engineer_decision", "") or ""),
            value=str(getattr(verification, "status", "") or ""),
            detail=str(getattr(verification, "engineer_note", "") or "") or "no note recorded",
            source="engineer"))

    if resolution is not None:
        items.append(PanelItem(label="outcome", value=str(getattr(resolution, "outcome", "")),
                               detail=str(getattr(resolution, "root_cause_confirmed", ""))))
        items.append(PanelItem(label="action taken", detail=str(getattr(resolution, "action_taken", ""))))
        items.append(PanelItem(label="observed result",
                               detail=str(getattr(resolution, "observed_result", "") or "")))

    if retention is not None:
        items.append(PanelItem(label="retained for future recall",
                               value=_text(retention.get("retained") if isinstance(retention, dict) else None),
                               detail=str(retention.get("reason", "") if isinstance(retention, dict) else ""),
                               trust="KNOWLEDGE"))

    return Panel(name="Decision", trust="DECISION", items=tuple(items),
                 note="The engineer's judgement. Nothing else in this session concludes anything.")


def _trace_panel(session: Any) -> Panel | None:
    """What the system did, in order. TRACE: process, not a claim about the design."""
    trace = getattr(session, "trace", None)
    if not trace:
        return None
    items = [PanelItem(ref=f"T{index:02d}", label=str(line), source="system")
             for index, line in enumerate(trace, start=1)]
    return Panel(name="Trace", trust="TRACE", items=tuple(items),
                 note="The order stages ran. A trace entry is not evidence that the stage's output is true.")


_PANEL_BUILDERS = (_memory_panel, _evidence_panel, _workers_panel, _reasoning_panel,
                   _validation_panel, _decision_panel, _trace_panel)


def _case_view(session: Any) -> CaseView | None:
    """Project `session.case`: the statement under investigation.

    `stated` distinguishes a field the engineer gave a value for from one they never mentioned. Both
    render as "not stated" in the header, but they are different facts, and the header must never claim
    the engineer asserted something they did not.
    """
    case = getattr(session, "case", None)
    if case is None:
        return None
    environment = tuple(
        EnvironmentFact(name=str(key), value=None if value is None else str(value),
                        stated=value is not None)
        for key, value in sorted((getattr(case, "environment", {}) or {}).items()))
    return CaseView(signature=str(getattr(case, "problem_signature", "") or ""),
                    symptoms=tuple(str(item) for item in (getattr(case, "symptoms", ()) or ())),
                    environment=environment)


def build_view_model(session: Any, *, status: str = "complete") -> SessionViewModel:
    """Project a session into panels.

    Reads a session through its attributes alone, so it works for any investigation - generic debugging,
    VLSI, or a domain that does not exist yet - and cannot acquire a domain-specific opinion.

    `status` describes the investigation itself, not its contents. A session that ran and found nothing
    is `complete`; only a session that raised is `failed`.
    """
    if status not in SessionViewModel.STATUSES:
        raise ViewModelError(f"status: '{status}' is not one of {list(SessionViewModel.STATUSES)}")

    case = _case_view(session)
    title = case.signature if case is not None else ""
    if not title:
        raw = getattr(session, "raw", None)
        title = str(getattr(raw, "description", "") or "investigation")
    title = title.splitlines()[0].strip() if title.strip() else "investigation"

    panels = [panel for builder in _PANEL_BUILDERS if (panel := builder(session)) is not None]
    order = {name: index for index, name in enumerate(PANEL_ORDER)}
    panels.sort(key=lambda panel: order.get(panel.name, len(order)))

    return SessionViewModel(session_id=str(getattr(session, "session_id", "")),
                            title=title, panels=tuple(panels), status=status, case=case)