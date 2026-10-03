"""VLSI domain: the contracts a VLSI investigation will be expressed in.

`docs/architecture/vlsi-engineering-roadmap.md` Part III (VLSI-Foundation). This package is the
"domain contracts only" milestone: typed representations, provenance, deterministic findings, and the
first three SDC constraint families.

**What exists here is data.** Four frozen dataclasses and their vocabularies, plus deterministic
serialisation. There is no parser, no analyser, no timing engine, no unit conversion and no tool
wrapper. VLSI-1 is where reading and analysis begin.

## The trust boundary, and how it is enforced

This package cannot, by construction or by test:

- touch Hindsight or any memory client - nothing here imports `debugagent.memory`;
- bypass authorisation - nothing here imports the Coordinator or the registry;
- read or write a file - the only filesystem-aware type imported is the existing task `Artifact`,
  used solely to express an already-read input as worker context;
- convert an observation into `Evidence` - nothing here imports `debugagent.pipeline`;
- make a root-cause claim, a recommendation, a repair or a decision - `VlsiFinding` has no field for
  any of them and rejects unknown keys;
- claim signoff;
- invoke an EDA tool.

`test_vlsi_foundation.py` asserts each of these, including by scanning this package's own imports, so
the boundary cannot be widened quietly by a later edit.

## Why a domain package at all

The architecture is domain-neutral by design (`docs/domain-neutral-system-design.md`): domain detail
lives in `environment` values and tag vocabularies while the abstraction, lifecycle and interfaces
stay identical across domains. So a domain adds *vocabulary and types*, never a parallel dispatch
path. A VLSI artifact becomes an ordinary task `Artifact` through `VlsiArtifact.as_task_artifact`, and
a VLSI finding becomes an ordinary worker observation. Nothing about the Coordinator, `MemoryLane`,
authorisation or the evidence boundary needs to know this package exists.
"""

from debugagent.domains.vlsi.artifacts import (
    ARTIFACT_TYPES,
    TASK_ARTIFACT_KIND,
    VlsiArtifact,
    artifact_identity,
)
from debugagent.domains.vlsi.comparison import (
    COMPARISON_OUTCOMES,
    COMPARISON_STATUSES,
    FindingComparison,
    RepairVerification,
    compare_sdc_findings,
)
from debugagent.domains.vlsi.findings import (
    DETAIL_VALUE_TYPES,
    FINDING_KINDS,
    FINDING_SEVERITIES,
    SEVERITY_RANK,
    VlsiFinding,
    finding_sort_key,
    sort_findings,
)
from debugagent.domains.vlsi.identity import (
    IDENTITY_FIELDS_BY_KIND,
    canonical_identity,
    parse_identity,
)
from debugagent.domains.vlsi.identity import (
    IDENTITY_FIELDS_BY_KIND,
    canonical_identity,
    parse_identity,
)
from debugagent.domains.vlsi.provenance import (
    DERIVED_REF,
    DERIVED_SOURCE,
    PROVENANCE_SOURCE_TYPES,
    Provenance,
    VlsiContractError,
)
from debugagent.domains.vlsi.sdc import (
    CONSTRAINT_KINDS,
    CONSTRAINT_TYPES,
    DELTA_QUALIFIERS,
    CreateClock,
    InputDelay,
    OutputDelay,
    constraint_from_dict,
    constraint_sort_key,
    sort_constraints,
)

__all__ = [
    # provenance
    "Provenance",
    "PROVENANCE_SOURCE_TYPES",
    "DERIVED_SOURCE",
    "DERIVED_REF",
    "VlsiContractError",
    # artifacts
    "VlsiArtifact",
    "ARTIFACT_TYPES",
    "TASK_ARTIFACT_KIND",
    "artifact_identity",
    # findings
    "VlsiFinding",
    "FINDING_KINDS",
    "FINDING_SEVERITIES",
    "SEVERITY_RANK",
    "DETAIL_VALUE_TYPES",
    "finding_sort_key",
    "sort_findings",
    # identity
    "IDENTITY_FIELDS_BY_KIND",
    "canonical_identity",
    "parse_identity",
    # identity
    "IDENTITY_FIELDS_BY_KIND",
    "canonical_identity",
    "parse_identity",
    # comparison
    "COMPARISON_OUTCOMES",
    "COMPARISON_STATUSES",
    "FindingComparison",
    "RepairVerification",
    "compare_sdc_findings",
    # sdc
    "CreateClock",
    "InputDelay",
    "OutputDelay",
    "CONSTRAINT_KINDS",
    "CONSTRAINT_TYPES",
    "DELTA_QUALIFIERS",
    "constraint_from_dict",
    "constraint_sort_key",
    "sort_constraints",
]