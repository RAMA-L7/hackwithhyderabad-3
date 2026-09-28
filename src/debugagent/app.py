"""Composition root: the one place that wires adapters into services.

Services receive ports; only this module knows which adapter stands behind each port.
"""

from __future__ import annotations

from pathlib import Path

from debugagent.adapters.offline_memory import OfflineMemoryPort
from debugagent.domain.errors import InputError
from debugagent.ports.llm_port import LLMPort
from debugagent.ports.memory_port import MemoryPort
from debugagent.services.evidence_service import EvidenceService
from debugagent.services.hypothesis_service import HypothesisService
from debugagent.services.investigation_service import InvestigationService
from debugagent.services.normalization_service import NormalizationService
from debugagent.services.recall_service import RecallService
from debugagent.services.retention_service import RetentionService
from debugagent.services.verification_service import VerificationService

DEFAULT_MEMORY = "offline:demo/offline-memory.json"


def build_memory(spec: str, state_dir: Path) -> MemoryPort:
    kind, _, target = spec.partition(":")
    if kind == "offline" and target:
        return OfflineMemoryPort(target, state_dir / "offline-retained.jsonl")
    if kind == "hindsight":
        raise InputError("the Hindsight memory adapter arrives at MK9; use --memory offline:<file> until then")
    raise InputError(f"unknown --memory {spec!r}; use offline:<file>")


def build_investigation(memory: MemoryPort, llm: LLMPort) -> InvestigationService:
    return InvestigationService(
        normalizer=NormalizationService(),
        recall=RecallService(memory),
        evidence=EvidenceService(),
        hypotheses=HypothesisService(llm),
        verification=VerificationService(),
        retention=RetentionService(memory),
    )
