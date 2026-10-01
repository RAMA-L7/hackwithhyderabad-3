"""VLSI artifact contracts: the things a session is HANDED, as distinct from what is observed about them.

An artifact is an input: a constraint file, a netlist, a timing report, a technology library, a tool
log. It has identity, a category, and a location in the authorised tree. It has **no findings, no
severity and no conclusion** - those belong to `VlsiFinding`, and the two are separate types on
purpose (see `test_an_artifact_carries_no_observation_fields`).

Identity is the pair `(kind, ref)`. There is deliberately **no synthetic id**: generating one would
require randomness or a clock, and this contract has to be reproducible. Within a session, kind plus
repository-relative path identifies an input unambiguously, which is all a task context needs.

No parser lives here. An artifact says *that* a `.sdc` is in scope and where; VLSI-1 is where reading
it becomes a concern.

Trust boundary: data only. Nothing here opens a file, and nothing here writes one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from debugagent.agents.tasks import Artifact as TaskArtifact

from .provenance import Provenance, VlsiContractError, _errors_to_raise, _reject_unknown_keys, _require_str

#: The artifact categories the foundation can represent. These are the inputs named in
#: `docs/architecture/vlsi-engineering-roadmap.md` section 3. A category is a fact about the FILE, not
#: about anything the file contains.
ARTIFACT_TYPES = ("sdc", "netlist", "timing_report", "library", "tool_log")

#: How each VLSI artifact category maps onto the existing worker-context `Artifact.kind`, so a VLSI
#: input can be handed to a worker through the P1 seam with no new context type.
#:
#: `sdc`, `netlist` and `library` are authored source, so they are `code`; `timing_report` and
#: `tool_log` are machine output, so they are `log`. That distinction is load-bearing: the existing
#: `Artifact.source` label keeps `code:` and `log:` observations visibly distinct from an
#: `engineer`-stated fact, and reusing it preserves that property instead of inventing a parallel one.
TASK_ARTIFACT_KIND = {
    "sdc": "code",
    "netlist": "code",
    "library": "code",
    "timing_report": "log",
    "tool_log": "log",
}


@dataclass(frozen=True)
class VlsiArtifact:
    """One VLSI engineering input, identified by its category and repository-relative location.

    `ref` is expected to be repository-relative, matching the rule the repository adapters already
    enforce: an observation must not leak the absolute layout of the machine that produced it. This
    contract does NOT resolve or check containment - `RepositoryScope` owns that question, and
    duplicating it here would create a second, weaker answer.
    """

    kind: str
    ref: str

    FIELDS = frozenset({"kind", "ref"})

    @property
    def identity(self) -> tuple[str, str]:
        """The artifact's identity: category plus location. No synthetic id, no clock, no randomness."""
        return (self.kind, self.ref)

    @property
    def task_artifact_kind(self) -> str:
        """The worker-context kind this category maps to."""
        return TASK_ARTIFACT_KIND[self.kind]

    def provenance(self, line: int | None = None) -> Provenance:
        """Provenance for something observed in this artifact.

        With no `line`, the location is explicitly unknown rather than implied to be line 1.
        """
        return Provenance(source_type="artifact", ref=self.ref, line=line)

    def as_task_artifact(self, content: str) -> TaskArtifact:
        """Express this artifact as worker context through the existing P1 `Artifact`.

        The bridge that makes this foundation fit the current architecture instead of running beside
        it: a VLSI input reaches a worker as an ordinary task artifact, authorised by the ordinary
        seam. `content` is supplied by the caller because this contract holds no I/O.
        """
        return TaskArtifact(kind=self.task_artifact_kind, ref=self.ref, content=content)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref}

    @classmethod
    def from_dict(cls, data: Any, label: str = "VlsiArtifact", errors: list[str] | None = None) -> "VlsiArtifact":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        _reject_unknown_keys(data, cls.FIELDS, label, errors)
        kind = _require_str(data.get("kind"), f"{label}.kind", errors)
        if kind and kind not in ARTIFACT_TYPES:
            errors.append(f"{label}.kind: '{kind}' is not one of {list(ARTIFACT_TYPES)}")
        return cls(kind=kind, ref=_require_str(data.get("ref"), f"{label}.ref", errors))

    @classmethod
    def parse(cls, data: Any, label: str = "VlsiArtifact") -> "VlsiArtifact":
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        _errors_to_raise(errors, label)
        return value


def artifact_identity(artifacts: tuple[VlsiArtifact, ...] | list[VlsiArtifact]) -> tuple[tuple[str, str], ...]:
    """The sorted identities of a set of artifacts.

    Sorted, so a set of inputs has one representation regardless of the order they were supplied -
    which is what makes a serialised task context comparable across runs.
    """
    return tuple(sorted(artifact.identity for artifact in artifacts))