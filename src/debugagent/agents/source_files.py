"""Production filesystem adapters for the P6 workers.

The Code/Log Verifier and the Patch Generator each declare a read-only Protocol seam, and until now
only test fakes satisfied them. This module provides the real ones, and it is where the repository
boundary lives.

**The boundary is here, not in the workers.** A worker should not have to police its own inputs, and
more to the point it should not know how: the workers stay independent of how sources are stored, so a
different backend - an archive, a VCS object store, an in-memory fixture - can satisfy the same Protocol.
Everything about "may this worker open that file" is decided in one place:

- only paths that RESOLVE INSIDE the configured root are readable;
- `..` traversal, absolute paths outside the root, and symlinks pointing out of the root are all
  rejected, and the check runs on the RESOLVED path, so a symlink cannot be used to step outside;
- a glob's LITERAL PREFIX is resolved the same way, because that is where a traversal hides - the
  suffix of `../../etc/*` looks harmless - and every match is re-checked afterwards, because a symlinked
  directory inside the root can still produce a match pointing out of it;
- returned references are ROOT-RELATIVE POSIX paths, so an artifact never leaks the absolute layout of
  the machine that produced it, and two checkouts of the same repository produce the same refs.

Both adapters are strictly read-only. There is no write, no apply, no subprocess and no VCS call in this
module, and the tests assert that both by shape (no mutating method on the port) and by effect (the
repository's bytes are identical before and after a full workflow).

**Each adapter speaks its own worker's error vocabulary.** The two workers define structurally identical
but DISTINCT `SourceNotFound` / `SourceUnavailable` classes, and each raises only the classes its own
worker catches. Sharing one set would mean a patcher reading a rejected path raised an exception the
patcher could not catch, so it would escape the worker and surface as a generic "worker could not be
dispatched" - a security refusal reported as an outage, which is exactly the confusion the P4/P6 failure
vocabulary exists to prevent. `Vocabulary` makes the choice explicit and local.
"""

from __future__ import annotations

import difflib
import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Sequence

from debugagent.agents.code_log_verifier import (
    SourceNotFound as VerifierSourceNotFound,
    SourceUnavailable as VerifierSourceUnavailable,
)
from debugagent.agents.patch_generator import (
    SourceNotFound as PatcherSourceNotFound,
    SourceUnavailable as PatcherSourceUnavailable,
)

#: Largest single file the adapters will read. A log or source file is small; anything larger is a
#: mistake or an attack, and reading it would turn an inspection into an out-of-memory event.
MAX_READ_BYTES = 2 * 1024 * 1024

#: Largest number of grep matches returned, so a pathological pattern cannot produce an unbounded result.
MAX_GREP_MATCHES = 500

#: Largest number of paths a single glob may return.
MAX_GLOB_MATCHES = 1_000

#: Characters that make a path unusable on any supported platform.
_UNSAFE_CHARACTERS = ("\x00",)

#: The characters that make a path SEGMENT a glob rather than a literal.
_GLOB_CHARACTERS = "*?["


@dataclass(frozen=True)
class Vocabulary:
    """The exception classes one adapter raises, and the kind used for a policy refusal.

    Bundled so an adapter passes one object to the scope rather than three related arguments, and so it
    is obvious at the call site which worker's failure contract is in play.
    """

    not_found: type
    unavailable: type
    policy_kind: str

    def refuse(self, message: str) -> Exception:
        """A policy refusal: a path or pattern the adapter will not act on."""
        return self.unavailable(self.policy_kind, message)

    def fault(self, message: str) -> Exception:
        """An environmental fault: the repository could not be read or expanded."""
        return self.unavailable("unavailable", message)

    def absent(self, reference: str) -> Exception:
        return self.not_found(reference)


VERIFIER_VOCABULARY = Vocabulary(not_found=VerifierSourceNotFound,
                                 unavailable=VerifierSourceUnavailable,
                                 policy_kind="invalid_pattern")
PATCHER_VOCABULARY = Vocabulary(not_found=PatcherSourceNotFound,
                                unavailable=PatcherSourceUnavailable,
                                policy_kind="invalid_patch")


class RepositoryScope:
    """The authorised repository root, and the only place path containment is decided.

    Holds no I/O policy beyond containment - no size limits, no glob budget - because those belong to the
    adapter that reads. What it owns is the single question every adapter must answer the same way: does
    this path stay inside the repository?
    """

    def __init__(self, root: str | Path):
        resolved = Path(root)
        if not resolved.is_dir():
            raise FileNotFoundError(f"repository root is not a directory: {root}")
        # `strict=True`: the root itself must exist. Resolving up front means every later containment
        # check compares two fully resolved paths, so a symlinked root cannot be stepped out of either.
        self._root = resolved.resolve(strict=True)

    @property
    def root(self) -> Path:
        return self._root

    def contains(self, path: Path) -> bool:
        """Whether an ALREADY RESOLVED path is inside the root.

        `is_relative_to` compares whole segments, so `/repo-evil` is not treated as inside `/repo` -
        which a plain string prefix check would get wrong.
        """
        try:
            return path.is_relative_to(self._root)
        except (ValueError, OSError):  # pragma: no cover - defensive across path flavours
            return False

    def resolve(self, candidate: str | Path, *, vocabulary: Vocabulary, label: str) -> Path:
        """Resolve `candidate` to a real path inside the root, or raise a refusal.

        An absolute path is accepted only if it lands inside the root; a relative one is joined to the
        root first. Either way the result is resolved THROUGH symlinks before the containment check, so
        `repo/link -> /etc` is rejected rather than followed.
        """
        text = str(candidate)
        if not text.strip():
            raise vocabulary.refuse(f"{label}: an empty path is not a repository path")
        for character in _UNSAFE_CHARACTERS:
            if character in text:
                raise vocabulary.refuse(f"{label}: path contains an unsafe character")

        raw = Path(text)
        joined = raw if raw.is_absolute() else self._root / raw
        try:
            resolved = joined.resolve()
        except OSError as exc:  # pragma: no cover - platform specific
            raise vocabulary.fault(
                f"{label}: could not resolve {text!r} ({type(exc).__name__})") from exc
        if not self.contains(resolved):
            raise vocabulary.refuse(
                f"{label}: {text!r} resolves outside the authorised repository "
                f"{self._root.name!r}. Paths may only address files inside it.")
        return resolved

    def relative(self, path: Path) -> str:
        """The provenance form of a path: repository-relative, POSIX-separated, never absolute."""
        try:
            return path.resolve().relative_to(self._root).as_posix() or path.name
        except ValueError:  # pragma: no cover - callers resolve inside the root first
            return PurePosixPath(path.name).as_posix()

    def iter_pattern(self, pattern: str, *, vocabulary: Vocabulary, label: str) -> Iterator[Path]:
        """Yield every existing path inside the root matching `pattern`, in a deterministic order.

        The literal prefix of the pattern - every segment before the first one holding a wildcard - is
        resolved through `resolve()` first, because that is where an escape hides. Each match is then
        re-checked for containment, because a symlinked directory inside the root can still produce a
        match pointing outside it. Both budgets are enforced rather than left to chance.
        """
        text = str(pattern).strip()
        if not text:
            raise vocabulary.refuse(f"{label}: an empty pattern matches nothing")
        for character in _UNSAFE_CHARACTERS:
            if character in text:
                raise vocabulary.refuse(f"{label}: pattern contains an unsafe character")

        candidate = Path(text)
        if candidate.is_absolute():
            segments = list(PurePosixPath(*candidate.parts[1:]).parts)
        else:
            segments = list(PurePosixPath(text).parts)

        first_wild = next((index for index, segment in enumerate(segments)
                           if any(character in segment for character in _GLOB_CHARACTERS)), None)

        if first_wild is None:
            # A plain path, not a pattern: resolve it and yield it if it exists.
            literal = self._root.joinpath(*segments) if segments else self._root
            resolved = self.resolve(literal, vocabulary=vocabulary, label=f"{label} pattern")
            if resolved.exists():
                yield resolved
            return

        base = self._root.joinpath(*segments[:first_wild]) if first_wild else self._root
        base = self.resolve(base, vocabulary=vocabulary, label=f"{label} pattern prefix")
        remainder = str(PurePosixPath(*segments[first_wild:]))
        try:
            candidates = sorted(base.glob(remainder))
        except (OSError, ValueError) as exc:
            raise vocabulary.fault(
                f"{label}: could not expand {text!r} ({type(exc).__name__})") from exc

        for produced, found in enumerate(candidates, start=1):
            if produced > MAX_GLOB_MATCHES:
                raise vocabulary.fault(
                    f"{label}: pattern {text!r} matched more than {MAX_GLOB_MATCHES} paths; narrow it")
            try:
                resolved = found.resolve()
            except OSError:  # pragma: no cover - a broken symlink
                continue
            if not self.contains(resolved):
                # A symlink out of the repository. Refused, not skipped: silently dropping it would
                # make the result set depend on what happens to be linked today.
                raise vocabulary.refuse(
                    f"{label}: {self.relative(found)!r} resolves outside the authorised repository")
            yield resolved


class FileSourcePort:
    """The Code/Log Verifier's real seam: `read`, `glob` and `grep`, all read-only.

    Implements the Verifier's `SourcePort` Protocol. The worker knows only those three method names; it
    does not know that paths, encodings or size limits exist, which is the point.
    """

    _vocabulary = VERIFIER_VOCABULARY

    def __init__(self, scope: RepositoryScope, *, max_bytes: int = MAX_READ_BYTES,
                 vocabulary: Vocabulary = VERIFIER_VOCABULARY):
        self._scope = scope
        self._max_bytes = max_bytes
        self._vocabulary = vocabulary

    @property
    def scope(self) -> RepositoryScope:
        return self._scope

    def _resolve(self, path: str, label: str) -> Path:
        return self._scope.resolve(path, vocabulary=self._vocabulary, label=label)

    def read(self, path: str) -> str:
        resolved = self._resolve(path, "read")
        if not resolved.exists():
            raise self._vocabulary.absent(self._scope.relative(resolved))
        if not resolved.is_file():
            raise self._vocabulary.fault(
                f"read: {self._scope.relative(resolved)!r} is not a regular file")
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            raise self._vocabulary.fault(
                f"read: could not stat {self._scope.relative(resolved)!r} "
                f"({type(exc).__name__})") from exc
        if size > self._max_bytes:
            raise self._vocabulary.fault(
                f"read: {self._scope.relative(resolved)!r} is {size} bytes, over the "
                f"{self._max_bytes}-byte limit for a single inspection")
        try:
            # `errors="replace"`: a log with one bad byte is still a log worth reading, and refusing it
            # would report an encoding problem as an unavailable repository.
            return resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise self._vocabulary.fault(
                f"read: {self._scope.relative(resolved)!r} could not be read "
                f"({type(exc).__name__})") from exc

    def glob(self, pattern: str) -> tuple[str, ...]:
        return tuple(self._scope.relative(path) for path in
                     self._scope.iter_pattern(pattern, vocabulary=self._vocabulary, label="glob"))

    def grep(self, pattern: str, paths: Sequence[str]) -> tuple[tuple[str, int, str], ...]:
        if not pattern:
            raise self._vocabulary.refuse("grep: an empty pattern matches everything")
        matches: list[tuple[str, int, str]] = []
        for candidate in paths:
            resolved = self._resolve(candidate, "grep")
            relative = self._scope.relative(resolved)
            if not resolved.is_file():
                # A file that vanished between glob and grep is a finding, not a fault; the Verifier
                # reports it as absent, exactly as it would from `read`.
                raise self._vocabulary.absent(relative)
            for number, line in enumerate(self.read(candidate).splitlines(), start=1):
                if fnmatch.fnmatch(line, pattern) or pattern in line:
                    matches.append((relative, number, line))
                    if len(matches) >= MAX_GREP_MATCHES:
                        raise self._vocabulary.fault(
                            f"grep: pattern {pattern!r} matched more than {MAX_GREP_MATCHES} lines; "
                            f"narrow it")
        return tuple(matches)


class RepoPatchSourcePort:
    """The Patch Generator's real seam: `read` and `diff`. Read-only by construction.

    Implements the Patch Generator's `PatchSourcePort` Protocol. `diff` renders a unified diff with
    `difflib` and returns the LINES; it does not touch the file, and this class exposes no method that
    could. A proposal produced from this port is a string the engineer must still apply by hand.
    """

    _vocabulary = PATCHER_VOCABULARY

    def __init__(self, scope: RepositoryScope, *, max_bytes: int = MAX_READ_BYTES,
                 vocabulary: Vocabulary = PATCHER_VOCABULARY):
        self._scope = scope
        # The reader is handed this port's vocabulary rather than the Verifier's, so a path the patcher
        # refuses raises the exception class the PATCH GENERATOR catches.
        self._reader = FileSourcePort(scope, max_bytes=max_bytes, vocabulary=vocabulary)
        self._vocabulary = vocabulary

    @property
    def scope(self) -> RepositoryScope:
        return self._scope

    def read(self, path: str) -> str:
        return self._reader.read(path)

    def diff(self, path: str, proposed: str) -> tuple[str, ...]:
        """Unified diff lines turning the current `path` into `proposed`. Never writes.

        An empty result means the two are byte-identical, which the worker reports as a clean
        "no change proposed" rather than as a patch.
        """
        current = self.read(path)
        relative = self._scope.relative(
            self._scope.resolve(path, vocabulary=self._vocabulary, label="diff"))
        return tuple(difflib.unified_diff(
            current.splitlines(), proposed.splitlines(), fromfile=relative, tofile=relative,
            lineterm=""))


__all__ = [
    "MAX_GLOB_MATCHES",
    "MAX_GREP_MATCHES",
    "MAX_READ_BYTES",
    "PATCHER_VOCABULARY",
    "VERIFIER_VOCABULARY",
    "FileSourcePort",
    "RepoPatchSourcePort",
    "RepositoryScope",
    "Vocabulary",
]
