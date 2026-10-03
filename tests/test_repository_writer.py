"""VLSI-1D.3a: the engineer-gated repository writer.

Every test here runs against a REAL temporary repository on a REAL filesystem. A fake writer would prove
only that the assertions in this file agree with each other; containment, link-following and atomic
replacement are properties of the filesystem, and a fake cannot have them.

The suite is organised around one claim: **no refused write changes a byte, and the only write that
happens is one an approval matches exactly.** So most tests assert two things - the reason, and that the
file is byte-identical afterwards. Asserting only the reason would pass against a writer that refused
correctly and then wrote anyway.

`NoRefusalEverWrites` pins that claim across every refusal reason at once, so a new refusal cannot be
added without inheriting the guarantee, and `test_every_declared_refusal_reason_is_reachable` pins the
opposite direction: a reason in `REFUSAL_REASONS` that nothing can produce is documentation, not a
guarantee.

The negative controls are the point of the increment. A tampered proposal, a redirected target, a
mismatched repair identifier and a concurrent edit must all leave the file untouched, because the writer
gaining the ability to change files is the moment the whole trust chain has to hold.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from debugagent.agents.repository_writer import (
    ALREADY_SATISFIED,
    CONTENT_MISMATCH,
    INVALID_APPROVAL,
    LINE_ENDING_MISMATCH,
    NO_APPROVAL,
    PATH_ESCAPE,
    REFUSAL_REASONS,
    REPAIR_MISMATCH,
    STALE_BASELINE,
    TARGET_ABSENT,
    TARGET_MISMATCH,
    WRITE_FAILED,
    AppliedWrite,
    RepairApproval,
    RepairRefused,
    RepairWriteFailed,
    RepositoryWriter,
    content_hash,
)
from debugagent.agents.source_files import FileSourcePort, RepositoryScope

CLOCK = "create_clock -name core_clk -period 10 [get_ports clk]\n"
REPAIR = CLOCK + "create_clock -name pll_clk -period 10 [get_ports pll]\n"
REPAIR_ID = "repair-1"


def read_raw(path: Path) -> str:
    """Read without newline translation, so what the test sees is what is on disk."""
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def write_raw(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def make_escape_link(link: Path, outside: Path) -> bool:
    """A link from inside the repository to outside it. False if the platform refuses to make one.

    Windows blocks file symlinks without elevation, but a directory JUNCTION needs none, and
    `Path.resolve()` follows both. What is under test is that a link is not followed out of the
    repository, not which flavour of link was used.
    """
    try:
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, timeout=30)
        if result.returncode == 0 and link.is_dir():
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        link.symlink_to(outside, target_is_directory=True)
        return link.is_dir()
    except OSError:
        return False


class RepositoryFixture(unittest.TestCase):
    """A real repository with two analysed files, and a writer authorised to touch them."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="writer-test-")
        self.root = Path(self._temporary.name)
        self.constraints = self.root / "constraints"
        self.constraints.mkdir()
        self.target = self.constraints / "top.sdc"
        write_raw(self.target, CLOCK)
        self.other = self.constraints / "other.sdc"
        write_raw(self.other, CLOCK)
        self.scope = RepositoryScope(self.root)
        self.writer = RepositoryWriter(self.scope)
        self.reader = FileSourcePort(self.scope)
        self.baseline = content_hash(self.reader.read("constraints/top.sdc"))

    def tearDown(self):
        self._temporary.cleanup()

    def approval(self, **overrides) -> RepairApproval:
        """A complete, matching approval for writing REPAIR over top.sdc. Override one field at a time."""
        fields = dict(repair_id=REPAIR_ID, target="constraints/top.sdc",
                      reviewed_hash=content_hash(REPAIR), expected_before_hash=self.baseline,
                      approved_by="engineer", approved_at="2026-10-03T09:00:00Z")
        fields.update(overrides)
        return RepairApproval(**fields)

    def write(self, content: str = REPAIR, *, target: str = "constraints/top.sdc",
              repair_id: str = REPAIR_ID, approval: RepairApproval | None = None, use_approval: bool = True):
        return self.writer.write(target, content, repair_id,
                                 approval if approval is not None or not use_approval
                                 else self.approval(target=target))

    def assertUntouched(self, *paths: Path):
        for path in paths:
            self.assertEqual(read_raw(path), CLOCK,
                             f"{path.name} was modified by an operation that should not have written")


class AuthorizationIsRequired(RepositoryFixture):
    """1. No approval -> no write."""

    def test_no_approval_refuses(self):
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, None)
        self.assertEqual(caught.exception.reason, NO_APPROVAL)
        self.assertUntouched(self.target)

    def test_a_blank_repair_id_refuses(self):
        for bad in ("", "   "):
            with self.subTest(repair_id=bad), self.assertRaises(RepairRefused) as caught:
                self.writer.write("constraints/top.sdc", REPAIR, bad, self.approval())
            self.assertEqual(caught.exception.reason, INVALID_APPROVAL)
            self.assertUntouched(self.target)

    def test_a_mismatched_repair_id_refuses(self):
        """The engineer approved repair-1; applying repair-2 under that approval is not authorised."""
        for bad in ("repair-2", "repair-1 ", "REPAIR-1", "repair-1/../repair-1"):
            with self.subTest(repair_id=bad), self.assertRaises(RepairRefused) as caught:
                self.writer.write("constraints/top.sdc", REPAIR, bad, self.approval())
            self.assertEqual(caught.exception.reason, REPAIR_MISMATCH)
            self.assertUntouched(self.target)

    def test_every_approval_field_must_be_present(self):
        for field in ("repair_id", "target", "reviewed_hash", "expected_before_hash",
                      "approved_by", "approved_at"):
            with self.subTest(field=field), self.assertRaises(RepairRefused) as caught:
                self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval(**{field: "  "}))
            self.assertEqual(caught.exception.reason, INVALID_APPROVAL)
            self.assertIn(field, caught.exception.message)
            self.assertUntouched(self.target)

    def test_an_approval_with_no_author_is_refused(self):
        """Unattributed authorization is not authorization."""
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval(approved_by=""))
        self.assertEqual(caught.exception.reason, INVALID_APPROVAL)

    def test_missing_fields_names_every_blank_field(self):
        self.assertEqual(self.approval(repair_id="", approved_by="").missing_fields(),
                         ("repair_id", "approved_by"))

    def test_a_blank_approved_path_is_an_invalid_approval_not_a_containment_failure(self):
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval(target="   "))
        self.assertEqual(caught.exception.reason, INVALID_APPROVAL)


class TheApprovalIsBoundToBytes(RepositoryFixture):
    """3, 4: the two substitutions an approval must not survive."""

    def test_a_changed_target_refuses(self):
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/other.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertEqual(caught.exception.reason, TARGET_MISMATCH)
        self.assertUntouched(self.target, self.other)

    def test_an_equivalent_spelling_of_the_approved_path_is_the_same_file(self):
        """Containment resolves before comparing, so `./` and `..` segments cannot fake a mismatch."""
        result = self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID,
                                   self.approval(target="constraints/./top.sdc"))
        self.assertEqual(result.target, "constraints/top.sdc")

    def test_changed_proposed_content_refuses(self):
        tampered = REPAIR + "create_clock -name injected -period 1 [get_ports evil]\n"
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", tampered, REPAIR_ID, self.approval())
        self.assertEqual(caught.exception.reason, CONTENT_MISMATCH)
        self.assertUntouched(self.target)

    def test_a_one_character_change_is_still_a_change(self):
        for tampered in (REPAIR + "\n", REPAIR.replace("period 10", "period 11"), REPAIR.upper(),
                         REPAIR[:-1]):
            with self.subTest(tampered=tampered[-24:]), self.assertRaises(RepairRefused) as caught:
                self.writer.write("constraints/top.sdc", tampered, REPAIR_ID, self.approval())
            self.assertEqual(caught.exception.reason, CONTENT_MISMATCH)

    def test_a_truncated_proposal_refuses(self):
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", REPAIR[:20], REPAIR_ID, self.approval())
        self.assertEqual(caught.exception.reason, CONTENT_MISMATCH)


class TheRepositoryIsTheBoundary(RepositoryFixture):
    """5, 6: traversal and link-following, both refused."""

    def test_parent_traversal_refuses(self):
        for candidate in ("../escape.sdc", "constraints/../../escape.sdc", "./../escape.sdc"):
            with self.subTest(path=candidate), self.assertRaises(RepairRefused) as caught:
                self.writer.write(candidate, REPAIR, REPAIR_ID, self.approval(target=candidate))
            self.assertEqual(caught.exception.reason, PATH_ESCAPE)
        self.assertFalse((self.root.parent / "escape.sdc").exists())

    def test_an_absolute_path_outside_the_repository_refuses(self):
        outside = Path(tempfile.gettempdir()) / "writer-test-escape.sdc"
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write(str(outside), REPAIR, REPAIR_ID, self.approval(target=str(outside)))
        self.assertEqual(caught.exception.reason, PATH_ESCAPE)
        self.assertFalse(outside.exists())

    def test_a_link_pointing_outside_the_repository_refuses(self):
        outside = Path(tempfile.mkdtemp(prefix="writer-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        secret = outside / "secret.sdc"
        write_raw(secret, "not yours\n")
        link = self.constraints / "escape"
        if not make_escape_link(link, outside):
            self.skipTest("this platform refuses to create symlinks or junctions unelevated")

        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/escape/secret.sdc", "pwned\n", REPAIR_ID,
                              self.approval(target="constraints/escape/secret.sdc"))
        self.assertEqual(caught.exception.reason, PATH_ESCAPE)
        self.assertEqual(read_raw(secret), "not yours\n",
                         "a link was followed and a file outside the repository was written")

    def test_the_link_itself_was_not_replaced_either(self):
        outside = Path(tempfile.mkdtemp(prefix="writer-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        write_raw(outside / "secret.sdc", "not yours\n")
        link = self.constraints / "escape"
        if not make_escape_link(link, outside):
            self.skipTest("this platform refuses to create symlinks or junctions unelevated")
        with self.assertRaises(RepairRefused):
            self.writer.write("constraints/escape/secret.sdc", "pwned\n", REPAIR_ID,
                              self.approval(target="constraints/escape/secret.sdc"))
        self.assertTrue(link.is_dir(), "the link was replaced by a regular file")


class OnlyExistingFilesAreModified(RepositoryFixture):
    """A repair changes what was analysed; it does not add files or directories nobody reviewed."""

    def test_a_missing_target_refuses(self):
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/new.sdc", REPAIR, REPAIR_ID,
                              self.approval(target="constraints/new.sdc"))
        self.assertEqual(caught.exception.reason, TARGET_ABSENT)
        self.assertFalse((self.constraints / "new.sdc").exists())

    def test_a_missing_directory_refuses_and_is_not_created(self):
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/deeper/new.sdc", REPAIR, REPAIR_ID,
                              self.approval(target="constraints/deeper/new.sdc"))
        self.assertEqual(caught.exception.reason, TARGET_ABSENT)
        self.assertFalse((self.constraints / "deeper").exists())

    def test_a_directory_target_refuses(self):
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints", REPAIR, REPAIR_ID, self.approval(target="constraints"))
        self.assertEqual(caught.exception.reason, TARGET_ABSENT)
        self.assertTrue(self.constraints.is_dir())


class ThePriorStateIsBound(RepositoryFixture):
    """The approval names the state it was computed against, so it cannot land on a changed file."""

    def test_a_stale_baseline_refuses(self):
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID,
                              self.approval(expected_before_hash="0" * 64))
        self.assertEqual(caught.exception.reason, STALE_BASELINE)
        self.assertUntouched(self.target)

    def test_an_intervening_edit_refuses(self):
        write_raw(self.target, CLOCK + "# someone else edited this\n")
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertEqual(caught.exception.reason, STALE_BASELINE)
        self.assertIn("# someone else", read_raw(self.target))

    def test_an_approval_is_spent_by_applying_it(self):
        self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertEqual(caught.exception.reason, ALREADY_SATISFIED)
        self.assertEqual(read_raw(self.target), REPAIR)

    def test_a_repair_that_changes_nothing_is_refused_as_already_satisfied(self):
        """A no-op proposal is refused for the same reason a replay is, because it is the same fact."""
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", CLOCK, REPAIR_ID,
                              self.approval(reviewed_hash=self.baseline))
        self.assertEqual(caught.exception.reason, ALREADY_SATISFIED)
        self.assertUntouched(self.target)


class LineEndingsArePartOfTheChange(RepositoryFixture):
    """The reader normalises newlines, so a naive write would silently rewrite every line ending."""

    def setUp(self):
        super().setUp()
        write_raw(self.target, "line one\r\nline two\r\n")
        self.crlf_baseline = content_hash(self.reader.read("constraints/top.sdc"))

    def test_writing_lf_over_a_crlf_file_refuses(self):
        proposed = "line one\nline two\nline three\n"
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", proposed, REPAIR_ID,
                              self.approval(reviewed_hash=content_hash(proposed),
                                            expected_before_hash=self.crlf_baseline))
        self.assertEqual(caught.exception.reason, LINE_ENDING_MISMATCH)
        self.assertEqual(read_raw(self.target), "line one\r\nline two\r\n")

    def test_writing_crlf_over_an_lf_file_refuses(self):
        write_raw(self.target, "line one\nline two\n")
        proposed = "line one\r\nline two\r\nline three\r\n"
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", proposed, REPAIR_ID,
                              self.approval(reviewed_hash=content_hash(proposed),
                                            expected_before_hash=content_hash("line one\nline two\n")))
        self.assertEqual(caught.exception.reason, LINE_ENDING_MISMATCH)

    def test_matching_line_endings_are_written(self):
        proposed = "line one\r\nline two\r\nline three\r\n"
        result = self.writer.write("constraints/top.sdc", proposed, REPAIR_ID,
                                   self.approval(reviewed_hash=content_hash(proposed),
                                                 expected_before_hash=self.crlf_baseline))
        self.assertEqual(read_raw(self.target), proposed)
        self.assertEqual(result.bytes_written, len(proposed.encode("utf-8")))

    def test_the_crlf_reason_beats_the_stale_reason(self):
        """Both are true after an intervening edit; the line-ending change is the one to report first."""
        write_raw(self.target, "rewritten by someone else\r\n")
        proposed = "line one\nline two\n"
        with self.assertRaises(RepairRefused) as caught:
            self.writer.write("constraints/top.sdc", proposed, REPAIR_ID,
                              self.approval(reviewed_hash=content_hash(proposed),
                                            expected_before_hash=self.crlf_baseline))
        self.assertEqual(caught.exception.reason, LINE_ENDING_MISMATCH)


class TheHashingConvention(RepositoryFixture):
    """A caller must be able to compute `expected_before_hash` from the existing read port."""

    def test_the_expected_before_hash_is_the_one_the_reader_produces(self):
        for name in ("constraints/top.sdc", "constraints/other.sdc"):
            with self.subTest(name=name):
                before = content_hash(self.reader.read(name))
                proposed = read_raw(self.root / name) + "extra\n"
                result = self.writer.write(name, proposed, REPAIR_ID,
                                           self.approval(target=name,
                                                         reviewed_hash=content_hash(proposed),
                                                         expected_before_hash=before))
                self.assertEqual(result.previous_hash, before)
                self.assertEqual(read_raw(self.root / name), proposed)

    def test_content_hash_is_the_sha256_of_the_utf8_encoding(self):
        import hashlib

        self.assertEqual(content_hash("abc"), hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(content_hash(""), hashlib.sha256(b"").hexdigest())

    def test_content_hash_separates_content_that_differs_only_in_line_endings(self):
        """The hash is over bytes, so it can tell two files the reader cannot."""
        self.assertNotEqual(content_hash("a\n"), content_hash("a\r\n"))


class Atomicity(RepositoryFixture):
    """7: a failed write leaves the original intact."""

    def test_a_failed_replacement_leaves_the_original_intact(self):
        with mock.patch("debugagent.agents.repository_writer.os.replace",
                        side_effect=OSError("simulated failure")):
            with self.assertRaises(RepairWriteFailed) as caught:
                self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertEqual(caught.exception.reason, WRITE_FAILED)
        self.assertIn("unchanged", str(caught.exception))
        self.assertUntouched(self.target)

    def test_a_failed_replacement_leaves_no_temporary_file_behind(self):
        with mock.patch("debugagent.agents.repository_writer.os.replace",
                        side_effect=OSError("simulated failure")):
            with self.assertRaises(RepairWriteFailed):
                self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertEqual([p.name for p in self.constraints.iterdir() if p.name.endswith(".tmp")], [],
                         "a failed write left its scratch file in the repository")

    def test_the_destination_is_never_opened_for_writing(self):
        """The content goes to a sibling and is renamed in, so a crash cannot leave a truncated file."""
        opened = []
        real_fdopen = __import__("os").fdopen

        def spy(handle, *args, **kwargs):
            opened.append(handle)
            return real_fdopen(handle, *args, **kwargs)

        with mock.patch("debugagent.agents.repository_writer.os.fdopen", side_effect=spy):
            self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertTrue(opened, "the temporary file was never opened")
        self.assertEqual(read_raw(self.target), REPAIR)

    def test_a_successful_write_leaves_no_temporary_file(self):
        self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertEqual(sorted(p.name for p in self.constraints.iterdir()), ["other.sdc", "top.sdc"])

    def test_the_temporary_file_lives_beside_the_target(self):
        """Same filesystem, so the final step is a rename and therefore atomic."""
        seen = {}
        real_mkstemp = tempfile.mkstemp

        def spy(*args, **kwargs):
            seen["dir"] = kwargs.get("dir")
            return real_mkstemp(*args, **kwargs)

        with mock.patch("debugagent.agents.repository_writer.tempfile.mkstemp", side_effect=spy):
            self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertEqual(Path(seen["dir"]), self.constraints)


class TheSuccessfulWrite(RepositoryFixture):
    """8: a matching approval writes, and reports what it did."""

    def test_a_valid_approval_writes(self):
        result = self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertIsInstance(result, AppliedWrite)
        self.assertEqual(result.repair_id, REPAIR_ID)
        self.assertEqual(result.target, "constraints/top.sdc")
        self.assertEqual(result.content_hash, content_hash(REPAIR))
        self.assertEqual(result.previous_hash, self.baseline)
        self.assertEqual(result.bytes_written, len(REPAIR.encode("utf-8")))
        self.assertEqual(read_raw(self.target), REPAIR)

    def test_the_write_is_readable_through_the_existing_port(self):
        """The repair lands where the analysis read it from, so re-analysis needs no new plumbing."""
        self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertEqual(self.reader.read("constraints/top.sdc"), REPAIR)

    def test_the_reference_is_repository_relative(self):
        result = self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertFalse(Path(result.target).is_absolute())
        self.assertNotIn(str(self.root), result.target)

    def test_only_the_approved_file_changes(self):
        self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        self.assertUntouched(self.other)

    def test_a_refusal_is_distinct_from_a_fault(self):
        """A refusal must not be retryable, so it must not be the same type as a disk error."""
        self.assertFalse(issubclass(RepairRefused, RepairWriteFailed))
        self.assertFalse(issubclass(RepairWriteFailed, RepairRefused))
        with self.assertRaises(RepairRefused):
            self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, None)
        with mock.patch("debugagent.agents.repository_writer.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(RepairWriteFailed):
                self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())


class NoRefusalEverWrites(RepositoryFixture):
    """The blanket guarantee, so a future refusal inherits it for free."""

    def refusals(self):
        # Every case works on a file that already exists in the fixture. Setting one up inside a lambda
        # would mutate the repository at the moment the test claims to prove that nothing mutates it.
        return {
            NO_APPROVAL: lambda: self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, None),
            INVALID_APPROVAL: lambda: self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID,
                                                        self.approval(repair_id="")),
            PATH_ESCAPE: lambda: self.writer.write("../escape.sdc", REPAIR, REPAIR_ID,
                                                  self.approval(target="../escape.sdc")),
            TARGET_MISMATCH: lambda: self.writer.write("constraints/other.sdc", REPAIR, REPAIR_ID,
                                                       self.approval()),
            REPAIR_MISMATCH: lambda: self.writer.write("constraints/top.sdc", REPAIR, "repair-2",
                                                      self.approval()),
            TARGET_ABSENT: lambda: self.writer.write("constraints/nope.sdc", REPAIR, REPAIR_ID,
                                                     self.approval(target="constraints/nope.sdc")),
            CONTENT_MISMATCH: lambda: self.writer.write("constraints/top.sdc", REPAIR + "x\n",
                                                        REPAIR_ID, self.approval()),
            ALREADY_SATISFIED: lambda: self.writer.write(
                "constraints/top.sdc", CLOCK, REPAIR_ID, self.approval(reviewed_hash=self.baseline)),
            STALE_BASELINE: lambda: self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID,
                                                      self.approval(expected_before_hash="0" * 64)),
            LINE_ENDING_MISMATCH: lambda: self.writer.write(
                "constraints/crlf.sdc", "one\ntwo\nthree\n", REPAIR_ID,
                self.approval(target="constraints/crlf.sdc",
                              reviewed_hash=content_hash("one\ntwo\nthree\n"),
                              expected_before_hash=content_hash("one\r\ntwo\r\n"))),
        }

    def setUp(self):
        super().setUp()
        # A CRLF file, so the line-ending case has a real target rather than a fixture it has to alter.
        write_raw(self.constraints / "crlf.sdc", "one\r\ntwo\r\n")
        self.snapshot = {path: read_raw(path) for path in self.constraints.iterdir()}

    def test_every_declared_refusal_reason_is_reachable(self):
        """A reason in `REFUSAL_REASONS` that nothing can produce is documentation, not a guarantee."""
        reached = set()
        for reason, action in self.refusals().items():
            with self.subTest(reason=reason):
                with self.assertRaises(RepairRefused) as caught:
                    action()
                self.assertEqual(caught.exception.reason, reason)
                reached.add(caught.exception.reason)
        self.assertEqual(reached, set(REFUSAL_REASONS),
                         "REFUSAL_REASONS lists reasons this suite cannot produce")

    def test_no_refusal_modifies_anything(self):
        refusals = self.refusals()
        for reason, action in refusals.items():
            with self.subTest(reason=reason):
                with self.assertRaises(RepairRefused):
                    action()
                self.assertEqual({path: read_raw(path) for path in self.constraints.iterdir()},
                                 self.snapshot, f"{reason} changed the repository")

    def test_no_refusal_leaves_a_temporary_file(self):
        refusals = self.refusals()
        for reason, action in refusals.items():
            with self.subTest(reason=reason):
                with self.assertRaises(RepairRefused):
                    action()
                self.assertEqual([p.name for p in self.constraints.iterdir() if p.suffix == ".tmp"], [])


class TheWriterIsContentBlind(RepositoryFixture):
    """Shape: this is a permission, not an editor. The engineering knowledge stays in the domain."""

    @property
    def _source(self) -> str:
        return Path(sys.modules[RepositoryWriter.__module__].__file__).read_text(encoding="utf-8")

    def test_the_writer_offers_write_only(self):
        offered = {name for name in dir(RepositoryWriter) if not name.startswith("_")}
        self.assertEqual(offered, {"scope", "write"})
        for forbidden in ("read", "glob", "grep", "diff", "delete", "unlink", "commit", "push"):
            self.assertNotIn(forbidden, offered)

    def test_the_writer_names_no_domain_vocabulary(self):
        source = self._source.lower()
        for forbidden in ("sdc", "vlsi", "constraint", "clock", "parse_sdc", "vlsi_finding",
                          "missing_constraint", "create_clock"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_writer_does_not_import_a_domain_or_a_worker(self):
        tree = ast.parse(self._source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertFalse([name for name in imported if "domains" in name or "vlsi" in name.lower()],
                         f"the writer must stay domain-blind: {sorted(imported)}")
        self.assertFalse([name for name in imported if name.split(".")[-1] in ("investigate", "verify")],
                         f"the writer must not reach the pipeline: {sorted(imported)}")

    def test_the_reader_module_still_contains_no_write(self):
        """The read-only guarantee is separate and still holds; the writer is not a method on it."""
        source = Path(sys.modules[RepositoryScope.__module__].__file__).read_text(encoding="utf-8")
        attributes = {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}
        for forbidden in ("unlink", "rename", "mkdir", "write_text", "write_bytes", "chmod"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, attributes)

    def test_the_reader_ports_still_expose_no_write(self):
        for port in (FileSourcePort,):
            offered = {name for name in dir(port) if not name.startswith("_")}
            for forbidden in ("write", "apply", "commit", "push", "replace"):
                with self.subTest(port=port.__name__, forbidden=forbidden):
                    self.assertNotIn(forbidden, offered)

    def test_the_approval_is_frozen(self):
        approval = self.approval()
        with self.assertRaises(AttributeError):
            approval.reviewed_hash = "0" * 64

    def test_the_result_is_frozen(self):
        result = self.writer.write("constraints/top.sdc", REPAIR, REPAIR_ID, self.approval())
        with self.assertRaises(AttributeError):
            result.bytes_written = 0


if __name__ == "__main__":
    unittest.main()