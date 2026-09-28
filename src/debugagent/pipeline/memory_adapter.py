"""MK9: the Hindsight memory port. The only module that joins Rama's memory layer to Mukul's pipeline.

Everything else keeps its own contract. This module:
  * calls HindsightMemoryStore.recall / retain (Rama, the only place hindsight_client is imported)
  * runs classify_candidates, which decides relevance and abstention (Rama)
  * renders the result as the MemoryView / RetentionDecision dicts pipeline/memory_port.py pins
  * joins `text` and `outcome` onto each candidate from the recalled case (plan Q8: MatchCandidate
    carries neither, and the CLI and the hypothesis prompt both need them)
  * maps memory errors to MemoryFailure so an unreachable bank is never read as "no memory found"

It decides nothing. It does not rank, does not drop, does not merge a past environment into the
current case, and never lets a recalled field reach Evidence.
"""

from __future__ import annotations

from debugagent.config import MemoryConfig, load_memory_config
from debugagent.memory.hindsight_store import HindsightMemoryStore
from debugagent.memory.matching import AbstentionPolicy, classify_candidates
from debugagent.memory.store import MemoryAuthError, MemorySchemaError, MemoryUnavailable
from debugagent.pipeline.memory_port import MemoryFailure, check_retention, check_view
from debugagent.schemas import MemoryCase

__all__ = ["HindsightMemoryPort", "build_port", "close_port", "policy_from_config"]

UNRECORDED = "unknown"  # hindsight_store.case_metadata's placeholder for a field the case did not record


def policy_from_config(config: MemoryConfig) -> AbstentionPolicy:
    """Build the abstention policy from config.

    Thresholds taken from the environment: min_final_score, weak_reference_floor, stale_after_days and
    semantic_floor (DEBUGAGENT_SEMANTIC_FLOOR; closes contract limitation L5 for it). contradiction_margin
    still stays on the AbstentionPolicy default. All are provisional values, not calibrated ones.
    """
    return AbstentionPolicy(
        min_final_score=config.min_final_score,
        weak_reference_floor=config.weak_reference_floor,
        stale_after_days=config.stale_after_days,
        semantic_floor=config.semantic_floor,
    )


class HindsightMemoryPort:
    """MemoryPort backed by the real Hindsight Cloud bank."""

    def __init__(self, store: HindsightMemoryStore, policy: AbstentionPolicy | None = None):
        self._store = store
        self._policy = policy if policy is not None else policy_from_config(store.config)

    # --- recall -----------------------------------------------------------------
    def recall_and_classify(self, query: str) -> dict:
        try:
            recalled = self._store.recall(query=query)
        except MemoryAuthError as exc:
            raise MemoryFailure("auth", str(exc)) from exc
        except MemoryUnavailable as exc:
            raise MemoryFailure("unavailable", str(exc)) from exc
        except MemorySchemaError as exc:
            raise MemoryFailure("schema", str(exc)) from exc

        report, abstention = classify_candidates(query, recalled, self._policy)

        # Q8: MatchCandidate carries no text/outcome; join them from the recalled case by case_id.
        by_id = {item.case_id: item for item in recalled.items}
        view = {
            "bank_id": recalled.bank_id,
            "recalled_at": recalled.recalled_at,
            "report": {
                "candidates": [self._with_case(c, by_id) for c in report.candidates],
                "excluded": [self._with_case(c, by_id) for c in report.excluded],
                "top_score": report.top_score,
                "threshold_used": report.threshold_used,
            },
            "abstention": abstention.to_dict(),
        }
        return check_view(view)

    @staticmethod
    def _with_case(candidate, by_id: dict) -> dict:
        payload = candidate.to_dict()
        # case_metadata stores "unknown" for a field the past case never recorded. Passing it on made
        # the MEMORY section say "runtime (then unknown, now node20)" and let verify() count a field the
        # past case never had as an evidence gap. Unrecorded is not a value: drop it here.
        payload["environment"] = {k: v for k, v in payload["environment"].items() if v != UNRECORDED}
        source = by_id.get(candidate.case_id)
        payload["text"] = source.text if source else ""
        payload["outcome"] = source.outcome if source else None
        return payload

    # --- retain -----------------------------------------------------------------
    def retain(self, case: dict) -> dict:
        try:
            parsed = MemoryCase.from_dict(case)
            decision = self._store.retain(parsed)
        except MemoryAuthError as exc:
            raise MemoryFailure("auth", str(exc)) from exc
        except MemoryUnavailable as exc:
            raise MemoryFailure("unavailable", str(exc)) from exc
        except MemorySchemaError as exc:
            raise MemoryFailure("schema", str(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            # SchemaError from debugagent.schemas is a ValueError; the memory layer must never
            # accept a malformed case just because the failure surfaced as a plain ValueError.
            raise MemoryFailure("schema", f"rejected invalid case: {exc}") from exc
        return check_retention(decision.to_dict())

    def close(self) -> None:
        """Release the backend HTTP resources owned by this port. Idempotent."""
        self._store.close()


def close_port(port: object) -> None:
    """Close a port's backend resources, if it owns any.

    A port that holds no HTTP client (the offline rehearsal port, a test fake) simply has no close()
    and owns nothing to release, so there is nothing to do.
    """
    closer = getattr(port, "close", None)
    if callable(closer):
        closer()


def build_port(*, data_dir: str | None = None) -> HindsightMemoryPort:
    """Build the port from the environment. Raises MemoryFailure, never a bare config error."""
    config = load_memory_config(data_dir)
    try:
        store = HindsightMemoryStore(config)
    except MemoryAuthError as exc:
        raise MemoryFailure("auth", str(exc)) from exc
    except MemoryUnavailable as exc:
        raise MemoryFailure("unavailable", str(exc)) from exc
    return HindsightMemoryPort(store)
