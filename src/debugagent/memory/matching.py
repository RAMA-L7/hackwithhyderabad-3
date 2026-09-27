"""Relevance classification and abstention. Pure functions, no I/O, no LLM.

Scores are query-relative; the floor is a configurable starting value derived from
observed M0 bands (relevant ~0.97-1.09, vague ~0.35-0.44, unrelated ~0.002-0.006)
and must be re-calibrated as seed data grows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from debugagent.schemas import (
    AbstentionDecision,
    MatchCandidate,
    MatchReport,
    RecallResult,
    RecallSet,
)

_TOKEN_SPLIT = set(" .:/,;()[]{}<>|_-\n\t")


@dataclass(frozen=True)
class AbstentionPolicy:
    min_final_score: float = 0.05
    semantic_floor: float = 0.70
    weak_reference_floor: float = 0.6
    stale_after_days: int = 365
    contradiction_margin: float = 0.2

    def to_dict(self) -> dict:
        return {
            "min_final_score": self.min_final_score,
            "semantic_floor": self.semantic_floor,
            "weak_reference_floor": self.weak_reference_floor,
            "stale_after_days": self.stale_after_days,
            "contradiction_margin": self.contradiction_margin,
        }


def effective_score(item: RecallResult, policy: AbstentionPolicy) -> tuple[float, str]:
    """Pick the signal that is still informative for this query.

    `final` is documented as a query-relative signal. Measured behaviour (M0 plus a live
    diagnostic on 2026-09-27): with a small bank a good match scores ~1.0, but once the
    bank holds many near-identical cases every candidate's `final` collapses toward ~0.003
    while `semantic` (a raw 0-1 vector cosine) stays informative (~0.78). So when `final`
    is below the floor we fall back to `semantic`, which is only used as an acceptance
    signal and never as a relevance ordering.
    """
    final = float(item.score_final)
    if final >= policy.min_final_score:
        return final, "final"
    semantic = item.score_semantic
    if isinstance(semantic, (int, float)) and not isinstance(semantic, bool):
        return float(semantic), "semantic_fallback"
    return final, "final"


def normalize_query(text: str) -> str:
    return " ".join((text or "").split()).strip()


def tokenize(text: str) -> set[str]:
    cleaned = (text or "").lower()
    tokens: set[str] = set()
    current: list[str] = []
    for char in cleaned:
        if char in _TOKEN_SPLIT:
            if current:
                tokens.add("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        tokens.add("".join(current))
    return tokens


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_stale(item: RecallResult, policy: AbstentionPolicy, now: datetime) -> bool:
    mentioned = _parse_timestamp(item.mentioned_at)
    if mentioned is None:
        return False
    return (now - mentioned).days > policy.stale_after_days


def _service(item: RecallResult) -> str:
    return (item.environment or {}).get("service", "unknown")


def _contradiction_groups(
    items: list[RecallResult], query_tokens: set[str]
) -> dict[str, dict[str, list[str]]]:
    """Bucket recalled cases by service, then by root cause.

    A service is only contradictory when it has two or more *distinct* root causes among
    the cases that overlap the query. Cases that agree on the root cause are corroboration,
    not conflict. Keying on the root cause is what prevents the false positive where every
    same-service pair is treated as disagreeing.
    """
    by_service: dict[str, dict[str, list[str]]] = {}
    for item in items:
        key = (item.root_cause_key or "").strip().lower()
        if key in ("", "not confirmed", "unconfirmed"):
            continue
        overlap = query_tokens & tokenize(item.text)
        if not overlap:
            continue
        bucket = by_service.setdefault(_service(item), {}).setdefault(key, [])
        if item.case_id not in bucket:
            bucket.append(item.case_id)
    return {service: buckets for service, buckets in by_service.items() if len(buckets) > 1}


def classify_candidates(
    query_text: str,
    results: RecallSet,
    policy: AbstentionPolicy,
    now: datetime | None = None,
) -> tuple[MatchReport, AbstentionDecision]:
    moment = now or datetime.now(timezone.utc)
    normalized = normalize_query(query_text)
    query_tokens = tokenize(normalized)

    ranked = sorted(results.items, key=lambda item: item.score_final, reverse=True)
    contradiction_map = _contradiction_groups(ranked, query_tokens)
    conflicts: dict[str, list[str]] = {}
    for service, buckets in contradiction_map.items():
        all_ids = [cid for ids in buckets.values() for cid in ids]
        for ids in buckets.values():
            others = [cid for cid in all_ids if cid not in ids]
            for case_id in ids:
                if others:
                    conflicts[case_id] = [
                        f"same service '{service}' with different root cause"
                    ] + others

    included: list[MatchCandidate] = []
    excluded: list[MatchCandidate] = []
    effective_top = 0.0
    fallback_used = False
    for item in ranked:
        score, signal = effective_score(item, policy)
        fallback_used = fallback_used or signal == "semantic_fallback"
        final_ok = item.score_final >= policy.min_final_score
        accepted = final_ok or (signal == "semantic_fallback" and score >= policy.semantic_floor)
        if not accepted:
            klass = "irrelevant"
            reason = (
                f"final {item.score_final:.4f} below floor {policy.min_final_score} and "
                f"semantic {score:.4f} below semantic floor {policy.semantic_floor}"
            )
        elif item.case_id in conflicts:
            klass = "contradictory"
            reason = "same service, different root cause among recalled cases"
        elif _is_stale(item, policy, moment):
            klass = "stale"
            reason = "recalled case is older than the staleness policy"
        elif score >= policy.weak_reference_floor:
            klass = "relevant"
            reason = f"{signal} score {score:.4f} at or above strong floor"
        else:
            klass = "partial"
            reason = f"{signal} score {score:.4f} above floor but below strong floor"

        if klass not in ("irrelevant", "stale"):
            effective_top = max(effective_top, score)
        candidate = MatchCandidate(
            case_id=item.case_id,
            relevance_class=klass,
            score_final=item.score_final,
            environment=dict(item.environment or {}),
            reason=reason,
            conflicts_with=conflicts.get(item.case_id, []),
        )
        if klass in ("irrelevant", "stale"):
            excluded.append(candidate)
        else:
            included.append(candidate)

    top_score = effective_top
    usable = [
        item
        for item in included
        if item.relevance_class in ("relevant", "partial", "contradictory")
    ]
    signal_note = (
        " (final scores were query-relative and below floor; semantic fallback used)"
        if fallback_used and usable
        else ""
    )
    report = MatchReport(
        candidates=included,
        excluded=excluded,
        top_score=top_score,
        threshold_used=policy.min_final_score,
    )

    if not usable:
        abstention = AbstentionDecision(
            abstained=True,
            relevance_class="irrelevant",
            top_score=top_score,
            threshold_used=policy.min_final_score,
            reason=(
                "no recalled case reached the usable floor; proceeding without historical memory"
                if ranked
                else "memory returned no candidates; proceeding without historical memory"
            ),
        )
        return report, abstention

    conflicting = [item for item in usable if item.relevance_class == "contradictory"]
    if conflicting:
        def signal_for(candidate: MatchCandidate) -> float:
            source = next(
                (i for i in results.items if i.case_id == candidate.case_id), None
            )
            if source is None:
                return candidate.score_final
            return effective_score(source, policy)[0]

        ordered = sorted(usable, key=signal_for, reverse=True)
        best = ordered[0]
        rivals = [
            item
            for item in ordered
            if item.relevance_class == "contradictory" and item.case_id != best.case_id
        ]
        margin = (
            signal_for(best) - signal_for(rivals[0])
            if rivals
            else policy.contradiction_margin
        )
        if rivals and margin < policy.contradiction_margin:
            abstention = AbstentionDecision(
                abstained=True,
                relevance_class="contradictory",
                top_score=top_score,
                threshold_used=policy.min_final_score,
                reason=(
                    "recalled cases disagree on root cause with no clear leader "
                    f"(margin {margin:.4f} < {policy.contradiction_margin})"
                ),
            )
            return report, abstention

    has_relevant = any(i.relevance_class == "relevant" for i in usable)
    has_partial = any(i.relevance_class == "partial" for i in usable)
    if has_relevant:
        overall = "relevant"
    elif has_partial:
        overall = "partial"
    else:
        overall = "contradictory"
    abstention = AbstentionDecision(
        abstained=False,
        relevance_class=overall,
        top_score=top_score,
        threshold_used=policy.min_final_score,
        reason=f"{len(usable)} usable recalled case(s) at or above floor{signal_note}",
    )
    return report, abstention
