"""P6: the real filesystem adapters, their path boundary, and their read-only guarantee.

Until now the Code/Log Verifier and the Patch Generator were only ever driven by test fakes. These are
the tests for the production adapters that replace them, and they are organised around the three things
that could actually go wrong:

1. **The path boundary.** Everything hinges on `RepositoryScope`, so traversal, absolute paths outside
   the root, symlinks out of the root, and globs whose *prefix* escapes are each tested directly rather
   than inferred from a worker's behaviour.
2. **Read-only.** Proven twice: by shape (the ports expose no mutating method) and by effect (the
   repository's bytes are identical before and after a full verifier + patch-generator workflow).
3. **Provenance.** Every returned reference is repository-relative and POSIX-separated, so an artifact
   never leaks the checkout layout and two machines produce the same refs.

Then real integration: both workers driven through the real adapters and the real composition root, via
`Coordinator.fan_out`, with memory work alongside.

Determinism: a real temporary directory, no network. Symlink tests skip with a reason if the platform
refuses to create one, rather than passing vacuously.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path

import support
from debugagent.agents.code_log_verifier import CODE_LOG_VERIFIER, CodeLogVerifier
from debugagent.agents.coordinator import Coordinator
from debugagent.agents.memory_specialist import MemorySpecialist, build_memory_specialist_task
from debugagent.agents.patch_generator import PATCH_GENERATOR, PatchGenerator
from debugagent.agents.code_log_verifier import SourceNotFound, SourceUnavailable
from debugagent.agents.patch_generator import SourceUnavailable as PatcherSourceUnavailable
from debugagent.agents.source_files import (
    VERIFIER_VOCABULARY as VOCABULARY,
    MAX_GLOB_MATCHES,
    MAX_GREP_MATCHES,
    MAX_READ_BYTES,
    FileSourcePort,
    RepoPatchSourcePort,
    RepositoryScope,
)
from debugagent.agents.registry import AuthorizationError, RegistrationError
from debugagent.composition import build_repository_runtime
from debugagent.memory.hindsight_store import HindsightMemoryStore
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from support import FakeHindsightClient, memory_config

GATE_TIMEOUT = 1.0
MEMORY = "memory_specialist"

LOG = "app/server.log"
LOG_TEXT = "2026-09-28 ECONNRESET uploads over 2MB\nclient_max_body_size 2m\n"
SOURCE = "src/upload.py"
SOURCE_TEXT = "MAX_BODY = 2 * 1024 * 1024\n"
OUTSIDE_SECRET = "TOP-SECRET-DO-NOT-READ\n"


def tree_digest(root: Path) -> str:
    """A content hash of the whole tree, so read-only can be proven by effect."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class AdapterBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.outside = Path(self._tmp.name) / "outside"
        self._make_tree()
        self.addCleanup(self._tmp.cleanup)
        self.scope = RepositoryScope(self.root)
        self.port = FileSourcePort(self.scope)
        self.patcher = RepoPatchSourcePort(self.scope)

    def _make_tree(self):
        (self.root / "app").mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "app" / "server.log").write_text(LOG_TEXT, encoding="utf-8")
        (self.root / "src" / "upload.py").write_text(SOURCE_TEXT, encoding="utf-8")
        (self.root / "README.md").write_text("# repo\n", encoding="utf-8")
        self.outside.mkdir(parents=True)
        (self.outside / "secret.txt").write_text(OUTSIDE_SECRET, encoding="utf-8")


class PortShapeTests(AdapterBase):
    """The ports expose exactly their worker's authorised operations, and nothing more."""

    def test_the_verifier_port_offers_read_glob_and_grep_only(self):
        from debugagent.agents.code_log_verifier import SourcePort

        expected = {name for name in dir(SourcePort) if not name.startswith("_")}
        self.assertEqual(expected, {"read", "glob", "grep"})
        for forbidden in ("write", "apply", "commit", "push", "unlink", "mkdir"):
            self.assertFalse(hasattr(self.port, forbidden),
                             f"FileSourcePort must not offer {forbidden}")

    def test_the_patch_port_offers_read_and_diff_only(self):
        from debugagent.agents.patch_generator import PatchSourcePort

        expected = {name for name in dir(PatchSourcePort) if not name.startswith("_")}
        self.assertEqual(expected, {"read", "diff"})
        for forbidden in ("write", "apply", "commit", "push", "write_text", "replace"):
            self.assertFalse(hasattr(self.patcher, forbidden),
                             f"RepoPatchSourcePort must not offer {forbidden}")

    def test_the_adapter_module_calls_nothing_mutating(self):
        import ast
        import inspect
        import textwrap

        module = inspect.getmodule(FileSourcePort)
        tree = ast.parse(textwrap.dedent(inspect.getsource(module)))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ("subprocess", "shutil", "unlink", "rename", "mkdir", "rmdir",
                          "write_text", "write_bytes", "touch", "chmod"):
            self.assertNotIn(forbidden, attributes,
                             f"the adapters must not call {forbidden}")
        for forbidden in ("subprocess", "shutil", "socket", "urllib", "requests"):
            self.assertFalse([name for name in imported if forbidden in name],
                             f"the adapters must not import {forbidden}")


class ReadTests(AdapterBase):
    def test_reads_a_file_inside_the_root(self):
        self.assertEqual(self.port.read(LOG), LOG_TEXT)

    def test_a_missing_file_is_reported_as_absent(self):
        with self.assertRaises(SourceNotFound) as caught:
            self.port.read("app/nothing.log")
        self.assertEqual(caught.exception.kind, "not_found")
        self.assertEqual(caught.exception.path, "app/nothing.log")

    def test_a_directory_is_not_a_readable_file(self):
        with self.assertRaises(SourceUnavailable) as caught:
            self.port.read("app")
        self.assertIn("not a regular file", str(caught.exception))

    def test_an_oversized_file_is_refused_rather_than_read(self):
        (self.root / "big.log").write_text("x" * (MAX_READ_BYTES + 1), encoding="utf-8")
        with self.assertRaises(SourceUnavailable) as caught:
            self.port.read("big.log")
        self.assertIn("limit", str(caught.exception))

    def test_a_file_with_a_bad_byte_is_still_readable(self):
        (self.root / "app" / "binary.log").write_bytes(b"ok \xff\xfe done\n")
        text = self.port.read("app/binary.log")
        self.assertIn("ok", text)
        self.assertIn("done", text)

    def test_reading_normalises_separators(self):
        self.assertEqual(self.port.read("app\\server.log"), LOG_TEXT,
                         "a Windows-style separator must address the same file")


class GlobTests(AdapterBase):
    def test_glob_returns_matching_paths(self):
        self.assertEqual(self.port.glob("src/*.py"), ("src/upload.py",))

    def test_glob_is_recursive_only_when_asked(self):
        """`*` is not recursive; that is pathlib's rule and this adapter does not change it."""
        self.assertEqual(self.port.glob("*.log"), ())
        self.assertIn("app/server.log", self.port.glob("**/*.log"))

    def test_glob_is_deterministic(self):
        (self.root / "src" / "a.py").write_text("a\n", encoding="utf-8")
        (self.root / "src" / "b.py").write_text("b\n", encoding="utf-8")
        first = self.port.glob("src/*.py")
        for _ in range(5):
            self.assertEqual(self.port.glob("src/*.py"), first,
                             "the same pattern must yield the same order every time")
        self.assertEqual(list(first), sorted(first))

    def test_a_pattern_matching_nothing_is_empty_not_an_error(self):
        self.assertEqual(self.port.glob("**/*.nothing"), ())

    def test_an_empty_pattern_is_refused(self):
        with self.assertRaises(SourceUnavailable):
            self.port.glob("   ")

    def test_glob_is_budgeted(self):
        for index in range(MAX_GLOB_MATCHES + 5):
            (self.root / "src" / f"f{index:05d}.py").write_text("x\n", encoding="utf-8")
        with self.assertRaises(SourceUnavailable) as caught:
            self.port.glob("src/*.py")
        self.assertIn("narrow it", str(caught.exception))


class GrepTests(AdapterBase):
    def test_grep_reports_path_line_and_text(self):
        self.assertEqual(self.port.grep("ECONNRESET", [LOG]),
                         (("app/server.log", 1, "2026-09-28 ECONNRESET uploads over 2MB"),))

    def test_grep_across_several_files(self):
        (self.root / "src" / "other.py").write_text("# ECONNRESET here too\n", encoding="utf-8")
        found = self.port.grep("ECONNRESET", [LOG, "src/other.py"])
        self.assertEqual([path for path, _, _ in found], ["app/server.log", "src/other.py"])

    def test_grep_with_no_match_is_empty(self):
        self.assertEqual(self.port.grep("NOSUCHTOKEN", [LOG]), ())

    def test_grep_on_a_missing_file_is_reported_as_absent(self):
        with self.assertRaises(SourceNotFound):
            self.port.grep("x", ["app/nothing.log"])

    def test_an_empty_grep_pattern_is_refused(self):
        with self.assertRaises(SourceUnavailable):
            self.port.grep("", [LOG])

    def test_grep_is_budgeted(self):
        (self.root / "big.log").write_text("hit\n" * (MAX_GREP_MATCHES + 10), encoding="utf-8")
        with self.assertRaises(SourceUnavailable) as caught:
            self.port.grep("hit", ["big.log"])
        self.assertIn("narrow it", str(caught.exception))


class PathBoundaryTests(AdapterBase):
    """The security boundary. Each case is checked directly on the adapter, not through a worker."""

    def test_parent_traversal_is_rejected(self):
        for attempt in ("../outside/secret.txt", "app/../../outside/secret.txt",
                        "../../etc/passwd", "app/../app/../../outside/secret.txt"):
            with self.subTest(path=attempt):
                with self.assertRaises(SourceUnavailable) as caught:
                    self.port.read(attempt)
                self.assertIn("outside the authorised repository", str(caught.exception))

    def test_an_absolute_path_outside_the_root_is_rejected(self):
        with self.assertRaises(SourceUnavailable):
            self.port.read(str(self.outside / "secret.txt"))

    def test_an_absolute_path_inside_the_root_is_accepted(self):
        self.assertEqual(self.port.read(str(self.root / LOG)), LOG_TEXT,
                         "a legitimate absolute path within the scope must work")

    def test_traversal_that_stays_inside_is_allowed(self):
        self.assertEqual(self.port.read("app/../src/upload.py"), SOURCE_TEXT,
                         "normalising within the repository is not an escape")

    def test_a_null_byte_in_a_path_is_rejected(self):
        with self.assertRaises(SourceUnavailable) as caught:
            self.port.read("app/server.log\x00.txt")
        self.assertIn("unsafe character", str(caught.exception))

    def test_an_empty_path_is_rejected(self):
        with self.assertRaises(SourceUnavailable):
            self.port.read("   ")

    def _symlink_supported(self) -> bool:
        probe = self.root / ".symlink-probe"
        try:
            os.symlink(self.outside / "secret.txt", probe)
        except (OSError, NotImplementedError, AttributeError):
            return False
        probe.unlink()
        return True

    def test_a_symlink_out_of_the_root_is_rejected(self):
        if not self._symlink_supported():
            self.skipTest("this platform refuses to create symlinks")
        link = self.root / "escape.log"
        try:
            os.symlink(self.outside / "secret.txt", link)
        except OSError as exc:  # pragma: no cover - reported by the probe above
            self.skipTest(f"symlink creation failed: {exc}")
        # The check runs on the RESOLVED path, so a symlink cannot be used to step outside.
        with self.assertRaises(SourceUnavailable) as caught:
            self.port.read("escape.log")
        self.assertIn("outside the authorised repository", str(caught.exception))
        self.assertNotIn(OUTSIDE_SECRET.strip(), str(caught.exception))

    def test_a_symlink_inside_the_root_is_allowed(self):
        if not self._symlink_supported():
            self.skipTest("this platform refuses to create symlinks")
        link = self.root / "inside.log"
        try:
            os.symlink(self.root / LOG, link)
        except OSError as exc:  # pragma: no cover
            self.skipTest(f"symlink creation failed: {exc}")
        self.assertEqual(self.port.read("inside.log"), LOG_TEXT)

    def test_the_patch_port_enforces_the_same_boundary(self):
        # The patcher raises ITS OWN exception class, not the verifier's. Asserting the verifier's would
        # miss the refusal entirely, which is the mistake this module's `Vocabulary` exists to prevent.
        for attempt in ("../outside/secret.txt", str(self.outside / "secret.txt")):
            with self.subTest(path=attempt):
                with self.assertRaises(PatcherSourceUnavailable) as caught:
                    self.patcher.read(attempt)
                self.assertIn("outside the authorised repository", str(caught.exception))
                self.assertNotIn(OUTSIDE_SECRET.strip(), str(caught.exception))
                with self.assertRaises(PatcherSourceUnavailable):
                    self.patcher.diff(attempt, "anything\n")

    def test_a_glob_prefix_cannot_escape(self):
        """The suffix of a pattern can look harmless while its prefix walks out."""
        for pattern in ("../outside/*.txt", "app/../../outside/*.txt", "**/../../outside/*.txt"):
            with self.subTest(pattern=pattern):
                with self.assertRaises(SourceUnavailable) as caught:
                    list(self.scope.iter_pattern(pattern, vocabulary=VOCABULARY, label="glob"))
                self.assertIn("outside the authorised repository", str(caught.exception))

    def test_an_escaping_pattern_is_refused_even_when_it_matches_nothing(self):
        """The prefix is resolved BEFORE the filesystem is consulted, and that must be observable.

        Without the prefix check, an escaping pattern that happens to match no existing file would
        return an empty result - indistinguishable from a pattern that legitimately matches nothing. The
        per-match containment check is a second net, but it only fires once something is found, so this
        is the case that proves we do not go looking outside the repository at all.
        """
        with self.assertRaises(SourceUnavailable) as caught:
            list(self.scope.iter_pattern("../outside-nothing-here/*.txt",
                                         vocabulary=VOCABULARY, label="glob"))
        self.assertIn("outside the authorised repository", str(caught.exception))

    def test_a_glob_matching_a_symlink_out_is_refused_not_skipped(self):
        if not self._symlink_supported():
            self.skipTest("this platform refuses to create symlinks")
        (self.root / "secrets").mkdir(exist_ok=True)
        try:
            os.symlink(self.outside / "secret.txt", self.root / "secrets" / "leak.txt")
        except OSError as exc:  # pragma: no cover
            self.skipTest(f"symlink creation failed: {exc}")
        # Refused rather than silently dropped: a result set that depended on what happens to be
        # linked today would be worse than a visible failure.
        with self.assertRaises(SourceUnavailable):
            list(self.scope.iter_pattern("secrets/*.txt", vocabulary=VOCABULARY, label="glob"))


class ScopeTests(AdapterBase):
    def test_a_non_directory_root_is_refused(self):
        with self.assertRaises(FileNotFoundError):
            RepositoryScope(self.root / "README.md")
        with self.assertRaises(FileNotFoundError):
            RepositoryScope(self.root / "nope")

    def test_containment_compares_whole_segments(self):
        sibling = self.root.parent / f"{self.root.name}-evil"
        sibling.mkdir()
        try:
            target = sibling / "secret.txt"
            target.write_text(OUTSIDE_SECRET, encoding="utf-8")
            self.assertFalse(self.scope.contains(target.resolve()),
                             "a sibling directory sharing a name prefix is NOT inside the root")
        finally:
            for child in sibling.iterdir():
                child.unlink()
            sibling.rmdir()

    def test_the_root_is_resolved_once(self):
        self.assertTrue(self.scope.root.is_absolute())
        self.assertTrue(self.scope.contains(self.scope.root))


class ProvenanceTests(AdapterBase):
    def test_every_returned_reference_is_repository_relative(self):
        refs = [self.port.glob("**/*.log"), self.port.glob("**/*.py")]
        for group in refs:
            for ref in group:
                self.assertFalse(Path(ref).is_absolute(), f"{ref!r} must not be absolute")
                self.assertNotIn(str(self.root), ref,
                                 "an artifact must not disclose the checkout layout")

    def test_grep_returns_relative_references(self):
        for path, _, _ in self.port.grep("ECONNRESET", [LOG]):
            self.assertEqual(path, LOG)
            self.assertFalse(Path(path).is_absolute())

    def test_diff_uses_relative_paths_on_both_sides(self):
        lines = self.patcher.diff(SOURCE, "MAX_BODY = 10 * 1024 * 1024\n")
        self.assertIn(f"--- {SOURCE}", lines)
        self.assertIn(f"+++ {SOURCE}", lines)
        for line in lines:
            if line.startswith(("---", "+++")):
                self.assertFalse(Path(line[4:]).is_absolute())

    def test_references_are_stable_across_scopes_over_the_same_directory(self):
        first = FileSourcePort(RepositoryScope(self.root)).glob("**/*.log")
        second = FileSourcePort(RepositoryScope(self.root)).glob("**/*.log")
        self.assertEqual(first, second, "two checkouts must produce identical refs")

    def test_an_absolute_path_yields_the_same_relative_ref(self):
        self.assertEqual(
            self.scope.relative(self.scope.resolve(str(self.root / LOG), vocabulary=VOCABULARY,
                                                  label="t")), LOG)
        self.assertEqual(
            self.scope.relative(self.scope.resolve(LOG, vocabulary=VOCABULARY, label="t")), LOG)


class ReadOnlyTests(AdapterBase):
    def test_a_full_workflow_leaves_the_repository_byte_identical(self):
        before = tree_digest(self.root)
        coordinator = build_repository_runtime(self.root).coordinator
        from debugagent.agents.code_log_verifier import build_verifier_task
        from debugagent.agents.patch_generator import build_patch_task

        fan = coordinator.fan_out([
            build_verifier_task(task_id="v-1", case_signature="uploads fail", targets=[LOG]),
            build_patch_task(task_id="p-1", case_signature="uploads fail", target=SOURCE,
                             proposed="MAX_BODY = 10 * 1024 * 1024\n"),
        ], timeout=2.0)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual(tree_digest(self.root), before,
                         "the adapters must not have written anything")

    def test_diffing_does_not_modify_the_file(self):
        before = (self.root / SOURCE).read_bytes()
        self.patcher.diff(SOURCE, "completely different\n")
        self.assertEqual((self.root / SOURCE).read_bytes(), before)

    def test_grepping_does_not_modify_the_file(self):
        before = (self.root / LOG).read_bytes()
        self.port.grep("ECONNRESET", [LOG])
        self.assertEqual((self.root / LOG).read_bytes(), before)


class RegistrationTests(AdapterBase):
    def test_registering_an_unregistered_agent_is_refused(self):
        # `get_agent` runs before the swap check, so an unknown id is refused by the closed roster -
        # a stronger refusal than a registration error, and the right one to report.
        with self.assertRaises((RegistrationError, AuthorizationError)):
            Coordinator().register("omnipotent_agent", object())

    def test_silently_swapping_a_live_worker_is_refused(self):
        coordinator = Coordinator()
        coordinator.register(CODE_LOG_VERIFIER, CodeLogVerifier(self.port))
        with self.assertRaises(RegistrationError) as caught:
            coordinator.register(CODE_LOG_VERIFIER, CodeLogVerifier(self.port))
        self.assertIn("already served", str(caught.exception))

    def test_registering_the_same_worker_again_is_idempotent(self):
        coordinator = Coordinator()
        worker = CodeLogVerifier(self.port)
        coordinator.register(CODE_LOG_VERIFIER, worker)
        coordinator.register(CODE_LOG_VERIFIER, worker)
        self.assertEqual(coordinator.agent_ids, (CODE_LOG_VERIFIER,))

    def test_an_unregistered_agent_reports_absence_rather_than_running(self):
        """The pre-existing `_Absent` behaviour still holds for a roster entry with no worker."""
        from debugagent.agents.patch_generator import build_patch_task

        coordinator = Coordinator()
        result = coordinator.delegate(build_patch_task(task_id="p-1", case_signature="x",
                                                      target=SOURCE, proposed="y\n"))
        self.assertEqual(result.status, "failed")
        self.assertIn("no worker instance", result.failure_detail)


class RealIntegrationTests(AdapterBase):
    """Both workers through the real adapters and the real composition root."""

    def setUp(self):
        super().setUp()
        self._tmp2 = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp2.cleanup)
        self.runtime = build_repository_runtime(self.root)
        self.coordinator = self.runtime.coordinator

    def memory_port(self):
        store = HindsightMemoryStore(
            memory_config(Path(self._tmp2.name), bank_id="p6c",
                          ledger_path=Path(self._tmp2.name) / "l.json"),
            client=FakeHindsightClient())
        self.addCleanup(store.close)
        return HindsightMemoryPort(store)

    def test_the_composition_root_wires_both_workers(self):
        """Was two workers; VLSI-1C registers a third alongside them.

        The assertion is kept rather than loosened to "at least the two": the composition root is the
        only place allowed to attach a worker to a Coordinator, so a worker that appeared without being
        wired here would be one nothing could dispatch.
        """
        from debugagent.agents.registry import get_agent
        from debugagent.agents.sdc_analyzer_worker import SDC_ANALYZER

        self.assertEqual(self.coordinator.agent_ids,
                         tuple(sorted((CODE_LOG_VERIFIER, PATCH_GENERATOR, SDC_ANALYZER))))
        self.assertIsInstance(self.runtime.scope, RepositoryScope)
        self.assertEqual(self.runtime.scope.root, self.root.resolve())
        for agent in (CODE_LOG_VERIFIER, PATCH_GENERATOR, SDC_ANALYZER):
            self.assertFalse(get_agent(agent).client_access)

    def test_the_verifier_reads_a_real_file(self):
        from debugagent.agents.code_log_verifier import build_verifier_task

        fan = self.coordinator.fan_out([
            build_verifier_task(task_id="v-1", case_signature="uploads reset", targets=[LOG])])
        self.assertTrue(fan.outcomes[0].ok, fan.summary()["by_status"])
        content = fan.outcomes[0].result.observations[0].content
        self.assertIn("ECONNRESET", content)
        self.assertIn("not a verification", content)

    def test_the_verifier_reports_a_missing_real_file_as_absent(self):
        from debugagent.agents.code_log_verifier import build_verifier_task

        fan = self.coordinator.fan_out([
            build_verifier_task(task_id="v-1", case_signature="x", targets=["app/nothing.log"])])
        self.assertEqual(fan.outcomes[0].result.status, "partial")
        self.assertIn("DOES NOT EXIST", fan.outcomes[0].result.observations[0].content)

    def test_a_traversal_target_is_refused_through_the_real_adapter(self):
        from debugagent.agents.code_log_verifier import build_verifier_task

        fan = self.coordinator.fan_out([
            build_verifier_task(task_id="v-1", case_signature="x",
                                targets=["../outside/secret.txt"])])
        result = fan.outcomes[0].result
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema",
                         "a path outside the repository is a refusal, not a missing file")
        self.assertIn("outside the authorised repository", result.failure_detail)
        self.assertNotIn(OUTSIDE_SECRET.strip(), result.failure_detail)

    def test_the_patch_generator_proposes_a_real_diff(self):
        from debugagent.agents.patch_generator import build_patch_task

        fan = self.coordinator.fan_out([
            build_patch_task(task_id="p-1", case_signature="body limit", target=SOURCE,
                             proposed="MAX_BODY = 10 * 1024 * 1024\n")])
        result = fan.outcomes[0].result
        self.assertTrue(result.ok)
        content = result.observations[0].content
        self.assertIn("PROPOSED PATCH", content)
        self.assertIn("MAX_BODY = 10 * 1024 * 1024", content)
        self.assertIn(f"--- {SOURCE}", content)

    def test_the_patch_generator_proposes_no_change_for_identical_content(self):
        from debugagent.agents.patch_generator import build_patch_task

        fan = self.coordinator.fan_out([
            build_patch_task(task_id="p-1", case_signature="x", target=SOURCE, proposed=SOURCE_TEXT)])
        result = fan.outcomes[0].result
        self.assertTrue(result.ok, "a real comparison that found no difference is a clean success")
        self.assertIn("NO CHANGE PROPOSED", result.observations[0].content)

    def test_a_traversal_target_is_refused_by_the_patcher_too(self):
        from debugagent.agents.patch_generator import build_patch_task

        fan = self.coordinator.fan_out([
            build_patch_task(task_id="p-1", case_signature="x", target="../outside/secret.txt",
                             proposed="anything\n")])
        result = fan.outcomes[0].result
        self.assertEqual(result.status, "failed")
        self.assertIn("outside the authorised repository", result.failure_detail)
        self.assertNotIn(OUTSIDE_SECRET.strip(), result.failure_detail)

    def test_neither_worker_takes_the_memory_lane(self):
        from debugagent.agents.code_log_verifier import build_verifier_task
        from debugagent.agents.patch_generator import build_patch_task

        lane_before = self.coordinator.lane.entered
        fan = self.coordinator.fan_out([
            build_verifier_task(task_id="v-1", case_signature="x", targets=[LOG]),
            build_patch_task(task_id="p-1", case_signature="x", target=SOURCE, proposed="y\n"),
        ], timeout=2.0)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual(self.coordinator.lane.entered, lane_before)

    def test_both_workers_run_concurrently_over_real_files(self):
        """Overlap observed at the file seam, not in a fake."""
        barrier = threading.Barrier(2)
        running = {"now": 0, "max": 0}
        lock = threading.Lock()

        class GatedScope(RepositoryScope):
            """Wraps the scope so every real read rendezvouses - the I/O is still real."""
            def resolve(self, candidate, *, vocabulary, label):
                resolved = super().resolve(candidate, vocabulary=vocabulary, label=label)
                with lock:
                    running["now"] += 1
                    running["max"] = max(running["max"], running["now"])
                try:
                    barrier.wait(timeout=GATE_TIMEOUT)
                except threading.BrokenBarrierError:
                    pass
                try:
                    return resolved
                finally:
                    with lock:
                        running["now"] -= 1

        from debugagent.agents.source_files import FileSourcePort as RealPort

        scope = GatedScope(self.root)
        coordinator = Coordinator({
            CODE_LOG_VERIFIER: CodeLogVerifier(RealPort(scope)),
            PATCH_GENERATOR: PatchGenerator(RepoPatchSourcePort(scope)),
        })
        from debugagent.agents.code_log_verifier import build_verifier_task
        from debugagent.agents.patch_generator import build_patch_task

        fan = coordinator.fan_out([
            build_verifier_task(task_id="v-1", case_signature="x", targets=[LOG]),
            build_patch_task(task_id="p-1", case_signature="x", target=SOURCE, proposed="y\n"),
        ], timeout=2.0)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual(running["max"], 2,
                         "the two workers must be able to read the repository at the same time")

    def test_all_three_workers_in_one_fan_out(self):
        from debugagent.agents.code_log_verifier import build_verifier_task
        from debugagent.agents.patch_generator import build_patch_task

        memory = self.memory_port()
        coordinator = Coordinator(self.coordinator._workers)
        coordinator.register(MEMORY, MemorySpecialist(memory))
        lane_before = coordinator.lane.entered
        fan = coordinator.fan_out([
            build_memory_specialist_task(task_id="m-1", case_signature="uploads fail"),
            build_verifier_task(task_id="v-1", case_signature="uploads fail", targets=[LOG]),
            build_patch_task(task_id="p-1", case_signature="uploads fail", target=SOURCE,
                             proposed="MAX_BODY = 10 * 1024 * 1024\n"),
            build_memory_specialist_task(task_id="m-2", case_signature="uploads fail"),
        ], timeout=2.0)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual([o.task_id for o in fan.outcomes], ["m-1", "v-1", "p-1", "m-2"])
        self.assertEqual(coordinator.lane.entered - lane_before, 2,
                         "only the memory tasks may take the lane")

    def test_a_repository_fault_surfaces_as_a_failure(self):
        """Unavailability must not be reported as a missing file."""
        from debugagent.agents.code_log_verifier import build_verifier_task

        class BrokenScope(RepositoryScope):
            def resolve(self, candidate, *, vocabulary, label):
                raise vocabulary.fault("repository is not mounted")

        coordinator = Coordinator({CODE_LOG_VERIFIER: CodeLogVerifier(
            FileSourcePort(BrokenScope(self.root)))})
        fan = coordinator.fan_out([
            build_verifier_task(task_id="v-1", case_signature="x", targets=[LOG])])
        result = fan.outcomes[0].result
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertIn("not mounted", result.failure_detail)


if __name__ == "__main__":
    unittest.main()
