"""The worker stage: what the P6 workers contribute to one investigation.

P6 built three workers and left them with nothing to do - `investigate()` ran the Memory Specialist and
ignored the other two. This module is the seam that connects all three to the flow, through the existing
`Coordinator`, and turns their results into ONE structured record.

The trust boundary is the whole point of this file, and it is enforced by WHERE each result is allowed
to go rather than by a warning attached to it:

| Result | Stands for | May become | Must never |
|---|---|---|---|
| Memory delegation | knowledge of PAST cases | context for the engineer | evidence, a decision, a verdict |
| Verifier findings | observed facts about the CURRENT system | an EVIDENCE-labelled report section | an `Evidence` item, a verification status, a verdict |
| Patch proposals | candidate changes | a PROPOSAL section | an applied change, a verified fix |

Note what "verifier results are current-system EVIDENCE" does and does not mean here. It places them on
the evidence SIDE of the boundary - they describe the system as it is, unlike a recalled case - and it
deliberately does not mean they enter `Evidence`. `evidence.py` and `verify.py` are untouched: the
verifier's observations are reported alongside them, never inside them. An observation the engineer has
not confirmed is still an observation, and folding it into the evidence set would let a worker's read of
a file stand as a verified fact about the system.

Determinism: findings are RANKED by a stable key and ties broken by (kind, ref), so the same repository
and issue always produce the same ordering regardless of which worker finished first. Nothing here
consults a clock or a completion order.

Authority: this module never authorises. It builds `TaskSpec`s through the P1 seam and hands them to
`Coordinator.fan_out`, so `authorize()` runs before every dispatch, refusals stay refusals, and memory
work still serialises through the shared `MemoryLane` exactly as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Sequence

from debugagent.agents.coordinator import FanOutResult, TaskOutcome
from debugagent.agents.tasks import SubAgentResult, TaskSpec

#: How many findings are carried in the section. A cap, not a truncation of engineering truth: the
#: count of what was dropped is reported alongside, so a truncated list cannot read as a complete one.
MAX_REPORTED_FINDINGS = 20


@dataclass(frozen=True)
class RankedFinding:
    """One verifier observation, with the deterministic rank it was given.

    `score` counts how many of the issue's own environment values and symptoms appear in the
    observation. It is a cheap, explainable relevance signal - not a judgement - and it is stable for a
    given repository and issue, which is what makes the ordering reproducible.
    """

    kind: str
    ref: str
    content: str
    score: int
    task_id: str
    source: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "content": self.content, "score": self.score,
                "task_id": self.task_id, "source": self.source}


# --- Relevance matching -----------------------------------------------------------------------
#
# Relevance is lexical, and lexical matching has one specific failure that matters for engineering
# text: the same quantity is written differently in a ticket and in a log or a config file. The issue
# says "uploads over 2 MB" and nginx says `client_max_body_size 2m`; the issue says "413 Request
# Entity Too Large" and the log says "413". Neither is a spelling mistake, and a ranker that cannot see
# that scores the actual culprit zero.
#
# So matching happens on a CANONICAL form of the text rather than on raw substrings. Nothing here
# consults a model, a network or a clock: the tables below are small, explicit and total, and the whole
# transformation is a pure function of the input string.

#: Canonical unit -> the surface forms seen for it. Bare single letters are included because they are
#: how configuration files abbreviate (`client_max_body_size 2m`, `proxy_read_timeout 30s`).
#:
#: `m` is read as MEGABYTES, which is the nginx and postgres convention and the case that motivated
#: this. It is also the abbreviation for minutes in some prose, so "5 m" as a duration will not
#: normalise; `min` covers minutes. The ambiguity is inherent to the abbreviation, not to this table.
_UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "b": ("b", "byte", "bytes"),
    "kb": ("kb", "k", "kib", "kilobyte", "kilobytes"),
    "mb": ("mb", "m", "mib", "megabyte", "megabytes"),
    "gb": ("gb", "g", "gib", "gigabyte", "gigabytes"),
    "tb": ("tb", "t", "tib", "terabyte", "terabytes"),
    "ms": ("ms", "msec", "msecs", "millisecond", "milliseconds"),
    "s": ("s", "sec", "secs", "second", "seconds"),
    "min": ("min", "mins", "minute", "minutes"),
    "h": ("h", "hr", "hrs", "hour", "hours"),
    "pct": ("%", "pct", "percent", "percentage"),
}
_UNIT_LOOKUP: dict[str, str] = {
    surface: canonical
    for canonical, surfaces in _UNIT_ALIASES.items() for surface in surfaces
}

#: HTTP status code -> the reason phrases seen for it. A code is the stable form: "413" is not a
#: phrase anyone rewrites, and RFC 9110 itself renamed 413 from "Request Entity Too Large" to
#: "Content Too Large", so old and new logs disagree about the same status.
#:
#: 501 "Not Implemented" is deliberately absent: it is ordinary English that appears in source comments
#: everywhere, and normalising it to a number would manufacture matches.
_HTTP_STATUS_ALIASES: dict[str, tuple[str, ...]] = {
    "400": ("bad request",),
    "401": ("unauthorized", "unauthorised"),
    "403": ("forbidden",),
    "404": ("not found",),
    "405": ("method not allowed",),
    "408": ("request timeout",),
    "409": ("conflict",),
    "413": ("payload too large", "request entity too large", "request body too large",
            "content too large"),
    "414": ("uri too long", "request-uri too long"),
    "415": ("unsupported media type",),
    "422": ("unprocessable entity", "unprocessable content"),
    "429": ("too many requests", "rate limit exceeded"),
    "500": ("internal server error",),
    "502": ("bad gateway",),
    "503": ("service unavailable", "service temporarily unavailable"),
    "504": ("gateway timeout", "gateway timeout error"),
    "507": ("insufficient storage",),
}

#: The errors that appear by name in one system and by prose in another. These are the ones that show
#: up in exactly this shape in Node and nginx logs.
_ERROR_ALIASES: dict[str, tuple[str, ...]] = {
    "econnreset": ("econnreset", "connection reset", "connection reset by peer"),
    "etimedout": ("etimedout", "timed out", "timeout", "timed-out"),
    "econnrefused": ("econnrefused", "connection refused"),
    "enospc": ("enospc", "no space left", "no space left on device"),
    "enoent": ("enoent", "no such file", "no such file or directory"),
}

#: Every multi-word phrase that collapses to a single canonical token, applied longest-first so
#: "no space left on device" wins over "no space left". Built once, sorted, so the table's iteration
#: order cannot vary between runs.
_PHRASE_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(sorted(
    ((re.compile(r"\b" + re.escape(surface) + r"\b"), canonical)
     for canonical, surfaces in tuple(_HTTP_STATUS_ALIASES.items()) + tuple(_ERROR_ALIASES.items())
     for surface in surfaces),
    key=lambda pair: (-len(pair[0].pattern), pair[0].pattern),
))

_NUMBER_PATTERN = r"\d+(?:[.,]\d+)*"
_TOKEN_RE = re.compile(f"({_NUMBER_PATTERN})|([a-z][a-z_]*)")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
#: A number immediately followed by a unit word, with at most whitespace between: "2m", "2 MB", "30s".
_MEASURE_RE = re.compile(rf"({_NUMBER_PATTERN})\s*([a-z%]+)")


def _strip_thousands(text: str) -> str:
    """`1,024` and `1024` are the same number. Only inside digit groups, so `20, 20` is untouched."""
    return _THOUSANDS_RE.sub("", text)


def _canonical_tokens(text: str) -> tuple[str, ...]:
    """Reduce text to comparable tokens: equivalent spellings become identical tokens.

    The transformation, in order: collapse HTTP reason phrases and named errors to their canonical code;
    then tokenise, attaching a unit word to the number in front of it so `2 MB`, `2mb` and `2m` all
    become the single token `2mb`. A thousands separator needs no step of its own here: the number
    pattern consumes `1,024` whole and the comma is dropped when the token is appended.

    A pure function of its input, cached because `rank_findings` asks the same question about the same
    strings repeatedly. The cache is a speed device only: it cannot change a result, and the tables it
    reads are module-level constants.
    """
    lowered = text.lower()
    for pattern, canonical in _PHRASE_REPLACEMENTS:
        lowered = pattern.sub(canonical, lowered)

    tokens: list[str] = []
    matches = list(_TOKEN_RE.finditer(lowered))
    index = 0
    while index < len(matches):
        match = matches[index]
        number, word = match.group(1), match.group(2)
        if number is not None and index + 1 < len(matches):
            following = matches[index + 1]
            gap = lowered[match.end():following.start()]
            unit = _UNIT_LOOKUP.get(following.group(2) or "")
            # Only merge across whitespace: `2 pool` must not become `2pool`.
            if unit is not None and gap.strip() == "":
                tokens.append(f"{number.replace(',', '')}{unit}")
                index += 2
                continue
        tokens.append((number or word).replace(",", ""))
        index += 1
    # Transport words say how the status travelled, not what happened, so "HTTP 413" and a bare "413"
    # are the same observation. Dropping them keeps the status codes comparable.
    collapsed: list[str] = []
    for token in tokens:
        # "413 Request Entity Too Large" is one status written twice once the phrase has been replaced
        # by its code. Collapsing runs keeps the token list a set of distinct facts, which is what the
        # contiguity check below compares.
        if not collapsed or collapsed[-1] != token:
            collapsed.append(token)
    return tuple(token for token in collapsed if token not in _TRANSPORT_NOISE)


#: Words that qualify how something was reported rather than what was reported.
_TRANSPORT_NOISE = frozenset({"http", "https", "status", "code", "error", "err"})


@lru_cache(maxsize=4096)
def _canonical_tokens_cached(text: str) -> tuple[str, ...]:
    return _canonical_tokens(text)


def _phrase_present(term_tokens: tuple[str, ...], document: tuple[str, ...]) -> bool:
    """Whether `term_tokens` occurs as a CONTIGUOUS run inside `document`.

    Contiguity is what keeps this honest. A term matching terms scattered across a file would make a
    long document match almost anything, which is how a ranker starts ranking by length instead of by
    relevance.
    """
    if not term_tokens or len(term_tokens) > len(document):
        return False
    span = len(term_tokens)
    for start in range(len(document) - span + 1):
        if document[start:start + span] == term_tokens:
            return True
    return False


def _unique_term_tokens(terms: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """The terms, canonicalised, with equivalents collapsed to one.

    The same measurement can reach the term list twice - stated as an environment value AND quoted in a
    symptom - and once the canonical form exists those are visibly the same term. Counting both would
    score one fact twice and let a verbose issue out-rank a precise one. Terms arrive sorted, so the
    surviving order is deterministic.
    """
    unique: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for term in terms:
        tokens = _canonical_tokens_cached(term)
        if tokens and tokens not in seen:
            seen.add(tokens)
            unique.append(tokens)
    return tuple(unique)


def _relevance_terms(case: Any) -> tuple[str, ...]:
    """The issue's own values, lower-cased, used only to rank - never to assert anything."""
    terms: list[str] = []
    for name, value in (getattr(case, "environment", None) or {}).items():
        if value:
            terms.append(str(value).lower())
    for symptom in (getattr(case, "symptoms", None) or ()):
        text = str(symptom).lower()
        # Word-ish tokens, punctuation stripped at the edges. Splitting on whitespace alone kept the
        # trailing separator - "fail;" and "ECONNRESET;" - so a term could never match the same word
        # appearing in a log line without that punctuation, and every score came back zero.
        for token in re.findall(r"[a-z0-9][a-z0-9._+-]*", text):
            if len(token) > 3:
                terms.append(token)
        # A measurement is also a term in its canonical form, which is the only way "2 MB" from a
        # symptom can reach a config file saying `2m`: the bare number and the bare unit are each too
        # short to survive the length filter above, but the pair is exactly the thing worth matching.
        for number, unit in _MEASURE_RE.findall(_strip_thousands(text)):
            canonical = _UNIT_LOOKUP.get(unit)
            if canonical is not None:
                terms.append(f"{number}{canonical}")
    # Sorted and de-duplicated so the term list - and therefore every score - is order-independent.
    return tuple(sorted({term for term in terms if term}))


def _scorable_text(content: str, signature: str) -> str:
    """The part of an observation that is evidence of anything: the quoted material.

    Every worker observation opens with a fixed sentence that echoes the issue signature - "OBSERVED in
    <file> for issue '<signature>'. Current-system observation...". Scoring the whole string therefore
    counted the TEMPLATE: every finding scored identically, however irrelevant its content, and a reader
    was told the file matched two terms when in fact the boilerplate had matched them. The audit found
    this by ranking a deliberately unrelated file first and watching it tie with the real culprit.

    So the echoed signature is removed before matching. What remains is what the worker actually read.
    When nothing in that matches, the score is honestly zero and the deterministic `(kind, ref, task_id)`
    tiebreak orders the findings - which is the correct outcome, not a gap.
    """
    lowered = content.lower()
    if signature:
        lowered = lowered.replace(signature.lower(), " ")
    return lowered


def rank_findings(observations: Sequence[tuple[SubAgentResult, Any]], case: Any) -> list[RankedFinding]:
    """Rank observations deterministically.

    Ordering is `(-score, kind, ref, task_id, content)`: the most issue-relevant first, then a total
    order that no two findings can tie on. That last part matters - a sort that can tie would make the
    output depend on the order the workers happened to finish in, so `content` is in the key rather
    than left to be an arbitrary tiebreak. Two byte-identical findings are then genuinely the same
    finding, and a stable sort leaves them in the order they arrived, which is itself deterministic.
    """
    terms = _relevance_terms(case)
    signature = getattr(case, "problem_signature", "") or ""
    # Each term is canonicalised once, not once per finding: the point of the canonical form is that
    # equivalent spellings agree, and doing it per term per document would be the same answer more
    # slowly.
    term_tokens = _unique_term_tokens(terms)
    findings: list[RankedFinding] = []
    for result, _outcome in observations:
        for observation in result.observations:
            lowered = _scorable_text(observation.content, signature)
            document = _canonical_tokens_cached(lowered)
            # A term counts at most once however many ways it could have matched, so widening the
            # equivalence table cannot inflate a score relative to a narrower one. The score stays
            # "how many of the issue's own terms appear in what the worker read".
            score = sum(1 for tokens in term_tokens if _phrase_present(tokens, document))
            findings.append(RankedFinding(
                kind=observation.kind, ref=observation.ref, content=observation.content,
                score=score, task_id=result.task_id, source=observation.source))
    findings.sort(key=lambda finding: (-finding.score, finding.kind, finding.ref, finding.task_id,
                                       finding.content))
    return findings


def _outcome_records(outcomes: Sequence[TaskOutcome]) -> list[dict]:
    """Per-task records, in INPUT order, keeping every outcome distinguishable.

    A refusal is recorded as `refused` and never as `failed`: collapsing the two would report a rule
    saying no as a thing going wrong, which is the confusion the P1 vocabulary exists to prevent.
    """
    return [outcome.to_dict() for outcome in outcomes]


def _refusal_records(outcomes: Sequence[TaskOutcome]) -> list[dict]:
    return [{"task_id": outcome.task_id, "agent": outcome.agent,
             "error": f"{type(outcome.error).__name__}: "
                      f"{' '.join(str(outcome.error).split())[:200]}"}
            for outcome in outcomes if outcome.refused]


@dataclass(frozen=True)
class WorkerStage:
    """Everything the workers contributed to one investigation, in one record.

    `verifier` and `patches` are separate because they mean different things and are rendered
    separately; they are held together here so a session carries ONE structured worker result rather
    than three loosely related fields.
    """

    verifier: dict | None
    patches: dict | None

    @property
    def ran(self) -> bool:
        return self.verifier is not None or self.patches is not None

    def to_dict(self) -> dict:
        record: dict[str, Any] = {}
        if self.verifier is not None:
            record["verifier"] = self.verifier
        if self.patches is not None:
            record["patches"] = self.patches
        return record


def _verifier_section(fan: FanOutResult, findings: Sequence[RankedFinding]) -> dict:
    reported = list(findings[:MAX_REPORTED_FINDINGS])
    return {
        "agent": _agent_of(fan),
        "boundary": ("current-system observations. NOT an Evidence item, NOT a verification, and not a "
                     "verdict: the engineer confirms them, exactly as they confirm any observation"),
        "findings": [finding.to_dict() for finding in reported],
        "findings_total": len(findings),
        "findings_truncated": len(findings) > len(reported),
        "outcomes": _outcome_records(fan.outcomes),
        "refusals": _refusal_records(fan.outcomes),
        "summary": fan.summary(),
    }


def _patches_section(fan: FanOutResult) -> dict:
    return {
        "agent": _agent_of(fan),
        "boundary": ("PROPOSALS. Nothing here is applied, written, committed or verified, and no patch is "
                     "ever applied automatically. Applying one is an engineer decision"),
        "proposals": [result.to_dict() for result in fan.results if result.ok],
        "outcomes": _outcome_records(fan.outcomes),
        "refusals": _refusal_records(fan.outcomes),
        "summary": fan.summary(),
    }


def _agent_of(fan: FanOutResult) -> str:
    for outcome in fan.outcomes:
        return outcome.agent
    return ""


def plan_tasks(case: Any, *, verifier_targets: Sequence[str] = (),
               patch_requests: Sequence[tuple[str, str]] = ()) -> tuple[list[TaskSpec], list[TaskSpec]]:
    """Turn caller INTENT into worker tasks, one task per target for failure isolation.

    The caller says "look at these files" and "propose these changes"; this module says which task
    shape that becomes. That split is deliberate - `investigate()` should not have to know a worker's
    schema, and a worker should not be told what to look at by the flow that dispatched it.

    One task per target, not one task listing all of them: a missing or unreadable file then fails on
    its own and leaves the other files' findings intact, instead of one bad path discarding a whole
    stage.

    Nothing is invented here. A patch request must arrive with both a target and a proposed body,
    because a worker choosing its own target is a worker deciding what to change.
    """
    from debugagent.agents.code_log_verifier import build_verifier_task
    from debugagent.agents.patch_generator import build_patch_task

    signature = getattr(case, "problem_signature", "")
    symptoms = tuple(getattr(case, "symptoms", ()) or ())

    verifier_tasks = [
        build_verifier_task(task_id=f"verify:{target}", case_signature=signature,
                            targets=(target,), symptoms=symptoms)
        for target in verifier_targets]

    patch_tasks = [
        build_patch_task(task_id=f"patch:{target}", case_signature=signature,
                         target=target, proposed=proposed)
        for target, proposed in patch_requests]

    return verifier_tasks, patch_tasks


def run_worker_stage(repository: Any, case: Any, *, verifier_targets: Sequence[str] = (),
                     patch_requests: Sequence[tuple[str, str]] = (),
                     timeout: float | None = None) -> WorkerStage:
    """Run the verifier and patcher through the Coordinator and structure what comes back.

    A stage with no targets in one of the two channels records `None` for that channel rather than an
    empty section, so a session that never asked for patches does not grow a "no patches" section that
    reads like a finding. Which targets exist is the caller's decision.

    The Coordinator comes from the repository runtime, so the dispatch goes through the same
    `authorize()` seam - and, in a memory session, the same `MemoryLane` - as every other delegation.
    """
    verifier_tasks, patch_tasks = plan_tasks(case, verifier_targets=verifier_targets,
                                            patch_requests=patch_requests)

    verifier_section = None
    if verifier_tasks:
        fan = repository.coordinator.fan_out(tuple(verifier_tasks), timeout=timeout)
        observations = [(outcome.result, outcome) for outcome in fan.outcomes
                        if outcome.result is not None and outcome.result.ok]
        verifier_section = _verifier_section(fan, rank_findings(observations, case))

    patches_section = None
    if patch_tasks:
        fan = repository.coordinator.fan_out(tuple(patch_tasks), timeout=timeout)
        patches_section = _patches_section(fan)

    return WorkerStage(verifier=verifier_section, patches=patches_section)
