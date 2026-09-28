"""Recall service (MK2): one recall through the memory port.

Memory informs; it never verifies. Relevance was decided by Rama's classify_candidates behind the
port; this service only carries the result forward. Nothing recalled ever reaches Evidence.
"""

from __future__ import annotations

from debugagent.domain.investigation import MemoryContext
from debugagent.domain.models import NormalizedDebugCase
from debugagent.logging_setup import get_logger
from debugagent.ports.memory_port import MemoryPort, check_view

log = get_logger(__name__)


def recall_query(case: NormalizedDebugCase) -> str:
    return "; ".join([case.problem_signature, *case.symptoms])


class RecallService:
    def __init__(self, memory: MemoryPort):
        self.memory = memory

    def recall(self, case: NormalizedDebugCase) -> MemoryContext:
        """MemoryFailure propagates: an unreachable bank is never 'no memory found'."""
        query = recall_query(case)
        view = check_view(self.memory.recall_and_classify(query))
        context = MemoryContext(view["bank_id"], view["recalled_at"], query, view["report"]["candidates"],
                                view["report"]["excluded"], view["abstention"])
        log.info("recall bank=%s candidates=%d excluded=%d abstained=%s", context.bank_id,
                 len(context.candidates), len(context.excluded), context.abstained)
        return context
