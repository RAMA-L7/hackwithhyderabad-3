"""The engineer's-authorised repository writer: the only path by which this system changes a file.

`source_files.py` reads a repository and is read-only by construction, enforced by AST tests that forbid
it from touching `unlink`, `rename`, `mkdir` or `write_text`. This module is the deliberate other half. It
lives apart from the reader on purpose - neither imports the other's mutating surface - so that "the
system can read a repository" and "the system can change a repository" remain two separately auditable
capabilities rather than one class with a `write` bolted on.

## Authorization is a value, and it is bound to bytes

A write happens only when a `RepairApproval` matches the write in every respect that could matter:

- **same target** - the path being written and the path that was approved must resolve to one file;
- **same proposed content** - `sha256` of the content to be written must equal the reviewed hash, so a
  proposal cannot be swapped between review and apply;
- **same prior state** - the file must still hash to what it hashed to when the repair was computed, so
  a repair cannot be applied on top of an edit made since;
- **an author** - an approval records who decided, because unattributed authorization is not
  authorization.

Every mismatch is a refusal and the file is left byte-identical. There is no path through this class that
writes bytes which were not hashed and approved.

## What an approval does not prove

An approval is a RECORD, and a well-formed record can be constructed by anyone holding a repository path -
including a test. What the writer enforces is *coherence*: that the bytes about to be written are exactly
the bytes that were approved, for exactly the file that was approved, in exactly the state it was approved
against. What it cannot enforce is *deliberation*: that a human actually decided. That boundary exists
only where approval can be minted solely through the engineer's decision path. Until then this is an
authorization gate, not proof of a human judgement - the same distinction the rest of the system keeps
between authorization and evidence.

## Single use falls out of the prior-state binding

An approval is spent by applying it. After a successful write the file holds the approved content, which
no longer matches the prior hash it was approved against, so the same approval cannot be applied twice.
That case is reported as `already_satisfied` - the file already contains exactly the approved content -
which is also, and unavoidably, what a no-op repair looks like. Those two situations are the same
observation about the file, so they share one reason rather than a distinction the writer cannot honestly
make.

## Line endings are part of the change

The reader normalises newlines, so a CRLF file and its LF equivalent produce one hash. Writing with
newline translation off means the bytes on disk are exactly the bytes that were hashed. Left alone, that
combination would let an approved two-line repair silently rewrite every line ending in the file - a
whole-file change nobody reviewed, hidden inside a small diff. So a change of line-ending convention is
itself a refusal, and the engineer is told why.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from debugagent.agents.source_files import RepositoryScope, Vocabulary

#: No approval was supplied. Nothing to authorise the write with.
NO_APPROVAL = "no_approval"
#: An approval field was empty. An approval missing an author or a hash authorises nothing.
INVALID_APPROVAL = "invalid_approval"
#: A path resolved outside the authorised repository, by traversal, absoluteness or symlink.
PATH_ESCAPE = "path_escape"
#: The path to write and the approved path are different files.
TARGET_MISMATCH = "target_mismatch"
#: The repair being applied is not the repair that was approved.
REPAIR_MISMATCH = "repair_mismatch"
#: The target does not exist, or is not a regular file. Repairs modify what was analysed.
TARGET_ABSENT = "target_absent"
#: The content to be written is not the content that was reviewed.
CONTENT_MISMATCH = "content_mismatch"
#: The file already holds exactly the approved content - a spent approval, or a repair with nothing to do.
ALREADY_SATISFIED = "already_satisfied"
#: The file changed after the repair was computed, so the repair may no longer apply.
STALE_BASELINE = "stale_baseline"
#: Writing would convert the file between LF and CRLF, rewriting every line it does not touch.
LINE_ENDING_MISMATCH = "line_ending_mismatch"
#: The repository could not be written. Environmental, not a policy decision.
WRITE_FAILED = "write_failed"

REFUSAL_REASONS = (
    NO_APPROVAL,
    INVALID_APPROVAL,
    PATH_ESCAPE,
    TARGET_MISMATCH,
    REPAIR_MISMATCH,
    TARGET_ABSENT,
    CONTENT_MISMATCH,
    ALREADY_SATISFIED,
    STALE_BASELINE,
    LINE_ENDING_MISMATCH,
)

FAULT_REASONS = (WRITE_FAILED,)


class TargetRefused(Exception):
    """The exception class `RepositoryScope.resolve` raises for this writer.

    Each adapter speaks its own worker's error vocabulary, so a path this writer will not touch raises a
    class this writer can catch - rather than escaping as an exception belonging to a worker that would
    report a security refusal as a malfunction.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


TARGET_VOCABULARY = Vocabulary(not_found=TargetRefused, unavailable=TargetRefused,
                              policy_kind="invalid_target")


class RepairRefused(Exception):
    """A policy decision: this write will not happen, and the file is unchanged.

    Distinct from `RepairWriteFailed` on purpose. A refusal means the write was not permitted - the
    approval did not match, the path was outside the repository - and no amount of retrying will change
    that. A fault means the repository could not be written for an environmental reason. Collapsing the
    two would let a caller retry a security decision as though it were a disk error.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message


class RepairWriteFailed(Exception):
    """The repository could not be written, for an environmental reason."""

    def __init__(self, reason: str, message: str):
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message


def content_hash(text: str) -> str:
    """The one hashing convention for repository content, used for both sides of an approval.

    Hashes the UTF-8 encoding of `text` with newline translation already applied, so the value describes
    the same text a caller sees through `FileSourcePort.read` and the same bytes this writer puts on disk.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RepairApproval:
    """An engineer's authorisation to put specific bytes into a specific file.

    `reviewed_hash` is the hash of the exact content proposed for writing; `expected_before_hash` is the
    hash of the file as it stood when the repair was computed. Together they make the approval
    single-use and unswappable. `approved_by` and `approved_at` are provenance - a record of who decided,
    which is worth keeping and is not by itself evidence that anyone did.
    """

    repair_id: str
    target: str
    reviewed_hash: str
    expected_before_hash: str
    approved_by: str
    approved_at: str

    def missing_fields(self) -> tuple[str, ...]:
        """Names of the fields that are absent or blank. An approval missing any of them authorises nothing."""
        return tuple(name for name in ("repair_id", "target", "reviewed_hash",
                                       "expected_before_hash", "approved_by", "approved_at")
                     if not str(getattr(self, name)).strip())


@dataclass(frozen=True)
class AppliedWrite:
    """What a successful write did, so a caller can report it without re-reading the file."""

    repair_id: str
    target: str
    content_hash: str
    previous_hash: str
    bytes_written: int


class RepositoryWriter:
    """Applies one approved repair to one file, atomically, inside the authorised repository.

    Content-agnostic by construction: it is handed text and knows nothing about what that text means.
    Rewriting a timing file and rotating a log are the same operation here, which is what keeps the
    engineering knowledge in the domain and the authority to change bytes in the core.
    """

    def __init__(self, scope: RepositoryScope):
        self._scope = scope

    @property
    def scope(self) -> RepositoryScope:
        return self._scope

    # -- containment -------------------------------------------------------------------------------

    def _resolve(self, path: str, label: str) -> Path:
        """Resolve inside the repository or refuse.

        Delegates to `RepositoryScope` verbatim, so the traversal, absolute-path and symlink rules are
        decided in exactly one place and the writer inherits them rather than reimplementing them. The
        refusal is re-raised as this writer's own exception so callers catch one type.
        """
        try:
            return self._scope.resolve(path, vocabulary=TARGET_VOCABULARY, label=label)
        except TargetRefused as exc:
            raise RepairRefused(PATH_ESCAPE, f"{label}: {exc.message}") from exc

    # -- the write ----------------------------------------------------------------------------------

    def write(self, target: str, content: str, repair_id: str,
              approval: RepairApproval | None) -> AppliedWrite:
        """Put `content` into `target`, but only under an approval that matches it in every respect.

        The caller states which repair it is applying, and the approval states which repair was
        authorised; they must agree. Without that, a repair identifier would be a label with nothing to
        check it against, and the engineer who approved repair A would have authorised repair B.

        On any refusal the file is left exactly as it was; on success the replacement is atomic, so a
        reader sees either the old file or the new one and never a partial write.
        """
        if approval is None:
            raise RepairRefused(NO_APPROVAL,
                                "no approval was supplied, so there is nothing authorising this write")

        missing = approval.missing_fields()
        if missing:
            raise RepairRefused(INVALID_APPROVAL,
                                f"the approval is missing {', '.join(missing)}; an incomplete approval "
                                f"authorises nothing")

        if not str(repair_id).strip():
            raise RepairRefused(INVALID_APPROVAL,
                                "no repair identifier was supplied, so there is nothing to match the "
                                "approval against")
        if approval.repair_id != repair_id:
            raise RepairRefused(REPAIR_MISMATCH,
                                f"the approval authorises repair {approval.repair_id!r}, but repair "
                                f"{repair_id!r} is being applied")

        resolved = self._resolve(target, "write")
        approved = self._resolve(approval.target, "approval")
        if resolved != approved:
            raise RepairRefused(TARGET_MISMATCH,
                                f"the approval authorises {approval.target!r}, which is a different file "
                                f"from {target!r}")

        # Existing files only. A repair exists to change something that was analysed, and permitting
        # creation here would let an approval quietly add files nobody reviewed.
        if not resolved.exists() or not resolved.is_file():
            raise RepairRefused(TARGET_ABSENT,
                                f"{self._scope.relative(resolved)!r} does not exist or is not a regular "
                                f"file; a repair may only modify an existing file")

        # Proposal integrity first: a tampered proposal is the more serious disagreement, and it is
        # detectable without touching the file at all.
        proposed_hash = content_hash(content)
        if proposed_hash != approval.reviewed_hash:
            raise RepairRefused(CONTENT_MISMATCH,
                                f"the content to be written hashes to {proposed_hash[:12]}..., but the "
                                f"engineer approved {approval.reviewed_hash[:12]}...; the proposal was "
                                f"changed after it was reviewed")

        current, current_uses_crlf = self._read(resolved)
        current_hash = content_hash(current)

        if current_hash == approval.reviewed_hash:
            raise RepairRefused(ALREADY_SATISFIED,
                                f"{self._scope.relative(resolved)!r} already contains exactly the "
                                f"approved content, so this approval has already been applied or the "
                                f"repair proposes no change at all")

        # Line-ending convention before staleness. Converting a CRLF file to LF changes every line while
        # the reviewed diff shows one added line, so that has to be the reported reason; checking
        # staleness first would misreport it as an intervening edit and send the engineer looking in
        # entirely the wrong place.
        if current_uses_crlf != ("\r\n" in content):
            raise RepairRefused(LINE_ENDING_MISMATCH,
                                f"writing this content would convert "
                                f"{self._scope.relative(resolved)!r} between LF and CRLF, rewriting "
                                f"every line the repair does not touch")

        if current_hash != approval.expected_before_hash:
            raise RepairRefused(STALE_BASELINE,
                                f"{self._scope.relative(resolved)!r} hashes to {current_hash[:12]}... "
                                f"but the repair was computed against "
                                f"{approval.expected_before_hash[:12]}...; the file changed after it was "
                                f"analysed, so the repair may no longer apply")

        payload = content.encode("utf-8")
        self._replace_atomically(resolved, payload)

        return AppliedWrite(repair_id=approval.repair_id, target=self._scope.relative(resolved),
                            content_hash=proposed_hash, previous_hash=current_hash,
                            bytes_written=len(payload))

    # -- mechanics ----------------------------------------------------------------------------------

    def _read(self, resolved: Path) -> tuple[str, bool]:
        """The current text and whether the file on disk uses CRLF.

        Two reads on purpose, because the two facts need different sources. The TEXT must match what
        `FileSourcePort.read` returns, so that an `expected_before_hash` computed from the reader is
        understood here - which means universal-newline translation. But that translation erases CRLF
        entirely, so asking the normalised text whether the file is a CRLF file always answers no. The
        convention therefore has to be sniffed from the RAW bytes, or a CRLF file would be silently
        rewritten as LF by any approved repair.
        """
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
            raw = resolved.read_bytes()
        except OSError as exc:
            raise RepairWriteFailed(WRITE_FAILED,
                                    f"{self._scope.relative(resolved)!r} could not be read for "
                                    f"comparison ({type(exc).__name__})") from exc
        return text, b"\r\n" in raw

    def _replace_atomically(self, resolved: Path, payload: bytes) -> None:
        """Swap in the new content atomically, or leave the original untouched.

        The temporary file is created in the destination directory so the final rename is within one
        filesystem, which is what makes it atomic; a temporary file elsewhere could be copied rather than
        renamed, and a reader would see a half-written file. On any failure the temporary file is removed
        and the destination is never opened.
        """
        handle, temp_name = tempfile.mkstemp(dir=str(resolved.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
            os.replace(temp_name, resolved)
        except OSError as exc:
            Path(temp_name).unlink(missing_ok=True)
            raise RepairWriteFailed(WRITE_FAILED,
                                    f"{self._scope.relative(resolved)!r} could not be written "
                                    f"({type(exc).__name__}); the original is unchanged") from exc


__all__ = [
    "ALREADY_SATISFIED",
    "CONTENT_MISMATCH",
    "FAULT_REASONS",
    "INVALID_APPROVAL",
    "LINE_ENDING_MISMATCH",
    "NO_APPROVAL",
    "PATH_ESCAPE",
    "REFUSAL_REASONS",
    "REPAIR_MISMATCH",
    "STALE_BASELINE",
    "TARGET_ABSENT",
    "TARGET_MISMATCH",
    "WRITE_FAILED",
    "AppliedWrite",
    "RepairApproval",
    "RepairRefused",
    "RepairWriteFailed",
    "RepositoryWriter",
    "content_hash",
]