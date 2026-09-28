"""MK5: hypothesis generation. One structured LLM call per investigation.

The model writes text and citations; the code decides everything that carries trust:
citations must be recalled, citable ids (checked inside the router, so a violation is retried and
then failed over, never returned); relevance_state is derived from the cited cases' classes (plan Q3);
an abstained memory yields generic hypotheses only.
"""

from __future__ import annotations

from dataclasses import dataclass

from debugagent.pipeline.recall_match import MemoryContext, compare_environment
from debugagent.pipeline.types import Hypothesis, NormalizedDebugCase

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hypotheses"],
    "properties": {"hypotheses": {"type": "array", "minItems": 2, "maxItems": 3, "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["hypothesis", "supporting_case_ids", "refutation_conditions", "recommended_next_step"],
        "properties": {
            "hypothesis": {"type": "string", "minLength": 1},
            "supporting_case_ids": {"type": "array", "items": {"type": "string"}},
            "refutation_conditions": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "recommended_next_step": {"type": "string", "minLength": 1},
        },
    }}},
}

RULES = """You assist a debugging engineer. Propose 2 or 3 hypotheses for the CURRENT ISSUE.
Order the list most likely first; the position is the ranking.

Rules:
- The CURRENT ISSUE is the only system being debugged. Describe it only with its own facts.
- PAST CASES happened on other systems at other times. They are prior experience, not evidence.
  Never describe the current system with a past case's service name, version or environment.
- supporting_case_ids may contain only case_id values listed under PAST CASES, copied exactly.
  Use [] for a hypothesis that is not based on a past case.
- If a past case records a failed approach, do not recommend it again unless you say why it would differ now.
- refutation_conditions: at least one observation that would prove the hypothesis wrong.
- recommended_next_step: one concrete check the engineer can run now.
- Return only JSON matching the schema, with exactly its fields: add no others (no rank, no confidence)."""


def _env(environment: dict) -> str:
    return ", ".join(f"{k}={v if v is not None else 'unknown'}" for k, v in environment.items()) or "none stated"


def build_prompt(case: NormalizedDebugCase, memory: MemoryContext) -> str:
    symptoms = "\n".join(f"- {s}" for s in case.symptoms) or "- none stated"
    parts = [RULES, "", "CURRENT ISSUE", f"signature: {case.problem_signature}", "symptoms:", symptoms,
             f"environment now: {_env(case.environment)}", "", "PAST CASES"]
    citable = memory.citable()
    if not citable:
        parts.append(f"none. Memory abstained ({memory.abstention['reason']}). "
                     "Every hypothesis must use supporting_case_ids = [].")
    for case_id, c in citable.items():
        differs = ", ".join(f"{k} (then {p}, now {n or 'unknown'})"
                            for k, p, n in compare_environment(c["environment"], case.environment))
        parts += [f"[case_id={case_id}] relation: {c['relevance_class']}; original environment: {_env(c['environment'])}; "
                  f"differs from now: {differs or 'nothing recorded'}", c["text"], ""]
    return "\n".join(parts).rstrip() + "\n"


def citation_check(memory: MemoryContext):
    allowed = memory.citable()

    def check(data: dict) -> list[str]:
        return [f"hypotheses[{i}] cites {cid!r}, which is not a recalled citable case"
                for i, h in enumerate(data["hypotheses"]) for cid in h["supporting_case_ids"] if cid not in allowed]
    return check


def relevance_state(cited: list[str], memory: MemoryContext) -> str:
    """Q3: the most cautious class among the cited cases decides."""
    classes = {memory.citable()[cid]["relevance_class"] for cid in cited}
    if not classes:
        return "generic"
    if "contradictory" in classes:
        return "conditional"
    if "relevant" in classes:
        return "supported"
    return "weak-reference"


@dataclass(frozen=True)
class Proposal:
    hypotheses: list[Hypothesis]
    provider: str
    model: str
    fallback_used: bool
    attempts: list[dict]


def generate_hypotheses(case: NormalizedDebugCase, memory: MemoryContext, llm) -> Proposal:
    """llm: anything with LLMRouter.complete_structured's signature. LLM errors propagate: an LLM failure
    is never presented as 'the model found nothing'."""
    result = llm.complete_structured(build_prompt(case, memory), schema=RESPONSE_SCHEMA, name="hypotheses",
                                     check=citation_check(memory))
    hypotheses = []
    for i, raw in enumerate(result.data["hypotheses"], start=1):
        cited = [] if memory.abstained else list(dict.fromkeys(raw["supporting_case_ids"]))
        hypotheses.append(Hypothesis.from_dict({
            "ref": f"H{i}", "hypothesis": raw["hypothesis"], "supporting_case_ids": cited,
            "relevance_state": relevance_state(cited, memory),
            "refutation_conditions": raw["refutation_conditions"],
            "recommended_next_step": raw["recommended_next_step"],
        }))
    return Proposal(hypotheses, result.provider, result.model, result.fallback_used, result.attempts)
