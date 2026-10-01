"""P7: relevance matching for verifier findings - the improved behaviour and its regressions.

The audit's accepted limitation was that relevance was raw substring matching, so the two spellings an
engineering problem actually arrives in never met: the issue says "uploads over 2 MB" and nginx says
`client_max_body_size 2m`, and the culprit's own config file scored zero while the report called it
irrelevant. These tests cover the canonical matching that fixes that, and - just as importantly - the
cases where it must NOT match.

The negative tests are the point. A matcher that widens what counts as equal is easy to write and easy
to over-apply; the way to keep it honest is to pin down what it must still refuse: a number that is not
that number, a unit that is not attached to its number, and the observation template that every finding
carries. None of those may score, and each has a test below.

Deterministic and offline: no network, no clock, no model.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from debugagent.agents.coordinator import Coordinator
from debugagent.agents.tasks import Artifact, SubAgentResult
from debugagent.composition import build_repository_runtime
from debugagent.pipeline import worker_stage
from debugagent.pipeline.types import DebugInput, NormalizedDebugCase
from debugagent.pipeline.worker_stage import (
    _canonical_tokens, _phrase_present, _relevance_terms, _scorable_text, _unique_term_tokens,
    rank_findings,
)
from loop_support import FakeMemoryPort, FakeLLM, ScriptedEngineer, hyp

REPO_ROOT = Path(__file__).resolve().parents[1]

NGINX_CONF = """server {
    listen 80;
    client_max_body_size 2m;
    location /upload {
        proxy_read_timeout 30s;
        proxy_pass http://127.0.0.1:9000;
    }
}
"""

UNRELATED = """VERSION = '1.0'
TIMEOUT = 5
"""


def _case(symptoms, environment=None, signature="checkout fails"):
    return NormalizedDebugCase.from_dict({
        "problem_signature": signature, "raw_description": signature,
        "symptoms": list(symptoms), "environment": environment or {}})


def _result(ref, content, kind="code", task_id="verify:file"):
    """One worker result carrying a single observation, without running a worker."""
    return SubAgentResult(
        task_id=task_id, agent="code_log_verifier", status="success",
        observations=(Artifact(kind=kind, ref=ref, content=content),))


def _scores(observations, case):
    findings = rank_findings([(result, None) for result in observations], case)
    return {finding.ref: finding.score for finding in findings}


class CanonicalMatching(unittest.TestCase):
    """The equivalences the requirement names, at the unit level."""

    def test_two_megabytes_meets_the_nginx_abbreviation(self):
        for document in ("client_max_body_size 2m;", "client_max_body_size 2MB",
                         "client_max_body_size 2 mb", "limit is 2 megabytes"):
            with self.subTest(document=document):
                self.assertTrue(
                    _phrase_present(_canonical_tokens("2 MB"), _canonical_tokens(document)),
                    f"{document!r} should be recognised as 2 MB")

    def test_a_symptom_measurement_reaches_a_config_file(self):
        """The end-to-end shape of the fix: the measurement must become a term at all.

        "over 2 MB" contributes no term from the length filter - the number and the unit are each too
        short - so the measurement is added in canonical form. Without this the config file can never
        match, however good the matcher is.
        """
        terms = _relevance_terms(_case(["uploads over 2 MB fail"]))
        self.assertIn("2mb", terms, "the measurement never became a term")
        self.assertEqual(_scores([_result("nginx/site.conf", NGINX_CONF)],
                                 _case(["uploads over 2 MB fail"])), {"nginx/site.conf": 1})

    def test_http_status_code_and_every_reason_phrase_are_one_term(self):
        for phrase in ("413", "413 Request Entity Too Large", "413 Payload Too Large",
                       "413 Content Too Large", "HTTP 413", "status 413", "returned 413"):
            with self.subTest(phrase=phrase):
                self.assertTrue(_phrase_present(("413",), _canonical_tokens(phrase)),
                                f"the status was not matchable in {phrase!r}")
        # The whole phrase collapses to the single code, so it cannot out-vote a bare code.
        self.assertEqual(_canonical_tokens("413 Request Entity Too Large"), ("413",))
        self.assertEqual(_canonical_tokens("HTTP 413"), ("413",))

    def test_transport_wording_does_not_hide_a_status(self):
        """`HTTP 413` and a bare `413` are the same observation, so they must match each other."""
        self.assertTrue(_phrase_present(_canonical_tokens("HTTP 413"),
                                        _canonical_tokens("413 returned by upstream")))

    def test_other_statuses_too(self):
        phrases = {"429": "Too Many Requests", "502": "Bad Gateway", "503": "Service Unavailable",
                   "504": "Gateway Timeout", "404": "Not Found", "401": "Unauthorized"}
        for code, phrase in phrases.items():
            with self.subTest(code=code):
                self.assertTrue(_phrase_present((code,), _canonical_tokens(f"{code} {phrase}")),
                                f"{code} {phrase} did not match the bare code")
                self.assertTrue(_phrase_present((code,), _canonical_tokens(f"HTTP {code} upstream")),
                                f"HTTP {code} did not match the bare code")

    def test_time_units_in_their_common_spellings(self):
        for document in ("proxy_read_timeout 30s", "proxy_read_timeout 30 s",
                         "proxy_read_timeout 30sec", "timeout of 30 seconds"):
            with self.subTest(document=document):
                self.assertTrue(_phrase_present(_canonical_tokens("30 s"),
                                                _canonical_tokens(document)))

    def test_byte_units_in_their_common_spellings(self):
        for document in ("chunk 512MB", "chunk 512 mb", "chunk 512m", "chunk 512 megabytes"):
            with self.subTest(document=document):
                self.assertTrue(_phrase_present(_canonical_tokens("512 MB"),
                                                _canonical_tokens(document)))
        self.assertTrue(_phrase_present(_canonical_tokens("500 ms"), _canonical_tokens("after 500ms")))

    def test_named_errors_meet_their_prose(self):
        for prose in ("connection reset by peer", "connection reset", "ECONNRESET"):
            with self.subTest(prose=prose):
                self.assertEqual(_canonical_tokens(prose), ("econnreset",))
        self.assertEqual(_canonical_tokens("timed out"), _canonical_tokens("ETIMEDOUT"))
        self.assertEqual(_canonical_tokens("no space left on device"), _canonical_tokens("ENOSPC"))

    def test_versions_and_thousands_separators(self):
        self.assertTrue(_phrase_present(_canonical_tokens("nginx-1.25"),
                                        _canonical_tokens("running nginx/1.25")))
        self.assertTrue(_phrase_present(_canonical_tokens("1,024 records"),
                                        _canonical_tokens("1024 records returned")))

    def test_a_multi_word_term_still_needs_its_words_adjacent(self):
        """Contiguity is what stops a long document matching a short term."""
        term = _canonical_tokens("connection reset")
        self.assertFalse(_phrase_present(term, _canonical_tokens("connection was reset by peer")),
                         "scattered words must not match")


class WhatMustNotMatch(unittest.TestCase):
    """The negative half, which is what keeps the widening from becoming noise."""

    def test_a_different_number_is_not_the_same_number(self):
        for document in ("client_max_body_size 12m", "client_max_body_size 32mb",
                         "client_max_body_size 1024m"):
            with self.subTest(document=document):
                self.assertFalse(_phrase_present(_canonical_tokens("2 MB"),
                                                 _canonical_tokens(document)),
                                 f"{document!r} matched 2 MB - word boundaries are not being respected")

    def test_a_unit_does_not_attach_across_a_word(self):
        """`5 pool` must not become `5pool`, or every number would swallow the word after it."""
        self.assertEqual(_canonical_tokens("pool of 5"), ("pool", "of", "5"))
        self.assertFalse(_phrase_present(_canonical_tokens("5 mb"), _canonical_tokens("pool of 5")))

    def test_a_unit_does_not_attach_across_punctuation(self):
        """The whitespace guard, pinned directly.

        The inputs here are contrived, and deliberately so: real text rarely puts punctuation between a
        number and its unit, which is exactly why a guard nobody exercises is a guard nobody can rely on.
        `2-mb` is a hyphenated token, not the quantity two megabytes, and treating it as one would let a
        hyphenated identifier match a measurement.
        """
        for text in ("2-mb", "2.mb", "2/mb", "size 2-mb limit"):
            with self.subTest(text=text):
                self.assertNotIn("2mb", _canonical_tokens(text),
                                 f"{text!r} was read as 2 MB")

    def test_a_thousands_separator_in_a_measurement_is_stripped(self):
        """`512,000 bytes` is one number, so the term must not carry the separator.

        Otherwise the term is unreachable: no log line writes `512000b` with a comma in it, so the
        measurement would be in the term list and match nothing - the same stranded-term failure the
        audit found with `econnreset;`, one character further along.
        """
        terms = _relevance_terms(_case(["512,000 bytes rejected"]))
        self.assertIn("512000b", terms, "the measurement kept its thousands separator")
        self.assertNotIn("512,000b", terms)
        self.assertTrue(_phrase_present(("512000b",), _canonical_tokens("wrote 512000b bytes")))

    def test_irrelevant_content_still_scores_zero(self):
        case = _case(["connection pool exhausted"], {"service": "checkout"})
        self.assertEqual(_scores([_result("app/unrelated.py", UNRELATED)], case),
                         {"app/unrelated.py": 0})

    def test_the_observation_template_alone_scores_zero(self):
        """Every finding carries a fixed opening sentence; scoring it made every file look relevant.

        This is the regression the audit found, and the one an improved matcher is most likely to
        reintroduce - a wider table gives more ways for template words to coincide with a term.
        """
        signature = "media-uploader resets connections on uploads over 2 MB"
        case = _case(["uploads over 2 MB fail"], {"service": "media-uploader"}, signature=signature)
        boilerplate = (f"OBSERVED in a.py for issue {signature!r}. Current-system observation, "
                       "not a verification and not a root cause.")
        self.assertEqual(_scores([_result("a.py", boilerplate)], case), {"a.py": 0},
                         "the template scored, so a wider matcher is matching text it should not see")

    def test_single_word_terms_still_match_anywhere_in_a_document(self):
        """Unchanged lexical behaviour: a one-word term matches wherever it appears.

        Contiguity is a constraint on PHRASES, not on single words. Requiring a lone word to be adjacent
        to nothing in particular would be meaningless, and pinning it here stops a future tightening of
        the matcher from being mistaken for a bug fix.
        """
        case = _case(["pool exhausted"])
        scattered = "the pool is healthy; this worker is not exhausted by any measure"
        self.assertEqual(_scorable_text(scattered, ""), scattered)
        self.assertEqual(_scores([_result("app/x.py", scattered)], case), {"app/x.py": 2})

    def test_a_multi_word_environment_value_needs_its_words_adjacent(self):
        """Multi-token terms come from environment values, which are not word-split like symptoms.

        This is where contiguity actually does work. An issue reporting `pool=exhausted upstream` must
        not be satisfied by a file that mentions exhausted and upstream in different sentences, because
        that is a different claim about the system.
        """
        case = _case([], {"pool": "exhausted upstream"})
        self.assertEqual(_canonical_tokens("exhausted upstream"), ("exhausted", "upstream"))
        self.assertEqual(_scores([_result("app/y.py", "upstream healthy; pool exhausted here")], case),
                         {"app/y.py": 0}, "scattered words satisfied a two-word term")
        self.assertEqual(_scores([_result("app/w.py", "exhausted upstream at 10:04")], case),
                         {"app/w.py": 1})
        # Order matters: a phrase term is a phrase, not a bag of words.
        self.assertEqual(_scores([_result("app/v.py", "upstream exhausted at 10:04")], case),
                         {"app/v.py": 0})

    def test_an_aliased_phrase_collapses_and_therefore_ignores_adjacency(self):
        """A deliberate consequence, pinned rather than left to be discovered.

        `connection reset` normalises to the single token `econnreset`, because it is a known alias. A
        one-token term matches wherever it appears, so the words no longer have to be adjacent. That is
        the intended behaviour - the prose form and the errno are the same fact - but it means adjacency
        applies to phrases that are NOT aliases, and only to those.
        """
        case = _case([], {"error": "connection reset"})
        self.assertEqual(_canonical_tokens("connection reset"), ("econnreset",))
        self.assertEqual(_scores([_result("app/y.py", "connection reset by peer")], case),
                         {"app/y.py": 1})
        # The phrase must still be PRESENT in the document; the alias does not make words findable
        # from anywhere, it only renames the phrase once it is there.
        self.assertEqual(_scores([_result("app/z.py", "connection was reset by peer")], case),
                         {"app/z.py": 0})

    def test_a_unitless_number_does_not_become_a_measurement(self):
        terms = _relevance_terms(_case(["500 requests per second"]))
        self.assertNotIn("500s", terms, "a bare number glued to the next word became a measurement")

    def test_generic_english_is_not_treated_as_a_status(self):
        """501 'Not Implemented' is deliberately absent: it is ordinary source-code English."""
        self.assertEqual(_canonical_tokens("not implemented"), ("not", "implemented"),
                         "'not implemented' is ordinary English and must stay two words")
        self.assertEqual(_canonical_tokens("raise notImplementedError"), ("raise", "notimplementederror"))


class ScoringBehaviour(unittest.TestCase):
    """The score keeps its meaning as a count of the issue's own terms."""

    def test_equivalent_terms_are_counted_once(self):
        """One measurement stated twice is one fact, and must not score twice."""
        case = _case(["uploads over 2 MB fail"], {"max_body": "2 MB"})
        tokens = _unique_term_tokens(_relevance_terms(case))
        self.assertEqual(len(tokens), len(set(tokens)), "duplicate canonical terms survived")
        self.assertEqual(tokens.count(("2mb",)), 1)
        self.assertEqual(_scores([_result("nginx/site.conf", NGINX_CONF)], case),
                         {"nginx/site.conf": 1}, "one measurement scored more than once")

    def test_the_score_is_bounded_by_the_number_of_terms(self):
        """A score larger than the term count would mean the count had stopped meaning anything."""
        case = _case(["uploads over 2 MB fail with ECONNRESET and HTTP 413"],
                     {"service": "checkout", "proxy": "nginx-1.25"})
        terms = _unique_term_tokens(_relevance_terms(case))
        rich = ("OBSERVED for 'x'. 2 MB limit; ECONNRESET seen; 413 returned; checkout service; "
                "nginx/1.25 upstream")
        score = _scores([_result("logs/all.log", rich, kind="log")], case)["logs/all.log"]
        self.assertLessEqual(score, len(terms))
        self.assertGreater(score, 0)

    def test_plain_lexical_matching_still_works(self):
        """The original behaviour, untouched: an exact word in a symptom still scores."""
        case = _case(["econnreset on upload"], {"service": "checkout"})
        self.assertEqual(
            _scores([_result("logs/a.log", "fatal: econnreset while uploading", kind="log"),
                     _result("app/b.py", "logger.info('hello')")], case),
            {"logs/a.log": 1, "app/b.py": 0})

    def test_terms_keep_their_documented_strings(self):
        """Existing callers read these strings, so the set must not change shape."""
        terms = _relevance_terms(_case(["20/20 uploads of 2.5MB fail with ECONNRESET; 1MB succeed"],
                                      {"service": "checkout"}))
        self.assertIn("econnreset", terms)
        self.assertIn("fail", terms)
        self.assertIn("2.5mb", terms)
        self.assertIn("checkout", terms)
        self.assertIn("2.5mb", terms)
        for term in terms:
            self.assertFalse(term.endswith((";", ",", "/")), f"term {term!r} kept a separator")
        self.assertEqual(list(terms), sorted(terms), "the term list is no longer sorted")

    def test_punctuation_still_never_strands_a_symptom_token(self):
        terms = _relevance_terms(_case(["pool exhausted; workers blocked"]))
        self.assertIn("exhausted", terms)
        self.assertIn("blocked", terms)


class RankingStability(unittest.TestCase):
    """Determinism is a property of the matcher too, not only of the sort key."""

    def test_ranking_is_stable_across_repeated_runs(self):
        case = _case(["uploads over 2 MB fail with ECONNRESET"], {"proxy": "nginx-1.25"})
        observations = [_result("nginx/site.conf", NGINX_CONF),
                        _result("logs/app.log", "413 returned; ECONNRESET upstream", kind="log"),
                        _result("app/unrelated.py", UNRELATED)]
        first = [(f.ref, f.score) for f in rank_findings(
            [(r, None) for r in observations], case)]
        for _ in range(4):
            self.assertEqual([(f.ref, f.score) for f in rank_findings(
                [(r, None) for r in observations], case)], first)

    def test_ranking_does_not_depend_on_input_order(self):
        case = _case(["uploads over 2 MB fail"], {"proxy": "nginx-1.25"})
        observations = [_result("nginx/site.conf", NGINX_CONF),
                        _result("logs/app.log", "413 Request Entity Too Large", kind="log"),
                        _result("app/unrelated.py", UNREFERRED := UNRELATED)]
        forwards = [f.ref for f in rank_findings([(r, None) for r in observations], case)]
        backwards = [f.ref for f in rank_findings(
            [(r, None) for r in list(reversed(observations))], case)]
        self.assertEqual(forwards, backwards)

    def test_identical_findings_keep_a_stable_order(self):
        """Two byte-identical findings are the same finding; the order must still be reproducible."""
        case = _case(["uploads over 2 MB"])
        duplicate = _result("same.py", "identical content")
        first = rank_findings([(duplicate, None), (duplicate, None)], case)
        second = rank_findings([(duplicate, None), (duplicate, None)], case)
        self.assertEqual([(f.ref, f.score, f.content) for f in first],
                         [(f.ref, f.score, f.content) for f in second])

    def test_distinguishable_findings_cannot_tie(self):
        case = _case(["uploads over 2 MB"])
        results = [_result("a.py", "first observation"), _result("a.py", "second observation")]
        findings = rank_findings([(r, None) for r in results], case)
        keys = [(f.score, f.kind, f.ref, f.task_id, f.content) for f in findings]
        self.assertEqual(len(set(keys)), len(keys), "two different findings tied, so order is arbitrary")

    def test_canonical_tokens_are_identical_in_a_fresh_interpreter(self):
        """A different hash seed must not change a single token."""
        script = (
            "import sys; sys.path[:0] = ['src']\n"
            "from debugagent.pipeline.worker_stage import _canonical_tokens\n"
            "print('TOKENS:' + repr(_canonical_tokens('HTTP 413 for client_max_body_size 2m')))\n")
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              cwd=str(REPO_ROOT), encoding="utf-8", errors="replace")
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("TOKENS:")), None)
        self.assertIsNotNone(line, f"no output: {proc.stderr[-400:]}")
        self.assertEqual(eval(line[len("TOKENS:"):]), _canonical_tokens(
            "HTTP 413 for client_max_body_size 2m"))


class BoundariesUnchanged(unittest.TestCase):
    """A better relevance signal must not become a stronger claim."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "repo"
        (root / "nginx").mkdir(parents=True)
        (root / "logs").mkdir()
        (root / "app").mkdir()
        (root / "nginx" / "site.conf").write_text(NGINX_CONF, encoding="utf-8")
        (root / "logs" / "app.log").write_text(
            "2026-01-01 ERROR 413 Request Entity Too Large\n2026-01-01 ERROR ECONNRESET upstream\n",
            encoding="utf-8")
        (root / "app" / "unrelated.py").write_text(UNRELATED, encoding="utf-8")
        self.root = root
        self.runtime = build_repository_runtime(str(root), coordinator=Coordinator())

    def _session(self, engineer=None, **kwargs):
        from debugagent.pipeline.investigate import investigate

        raw = DebugInput("uploads over 2 MB fail behind nginx", ["uploads over 2 MB fail",
                                                                "ECONNRESET upstream"],
                         {"service": "checkout", "proxy": "nginx-1.25"})
        return investigate(raw, FakeMemoryPort(), FakeLLM(
            {"hypotheses": [hyp(), hyp()]}), engineer or ScriptedEngineer(),
            coordinator=self.runtime.coordinator, repository=self.runtime, **kwargs)

    def test_the_genuine_culprit_now_scores_instead_of_being_called_irrelevant(self):
        """The limitation the audit recorded, resolved end to end with the real adapters.

        The audit's example was exact: the issue says "uploads over 2 MB" and the nginx config says
        `client_max_body_size 2m`, so the file naming the actual limit scored zero. Asserted here as a
        before-and-after, so the improvement cannot silently revert: the legacy raw-substring score is
        computed inline and shown to be zero.
        """
        session = self._session(verifier_targets=["app/unrelated.py", "logs/app.log",
                                                  "nginx/site.conf"])
        findings = session.workers["verifier"]["findings"]
        scores = {finding["ref"]: finding["score"] for finding in findings}

        conf = (self.root / "nginx" / "site.conf").read_text(encoding="utf-8")
        case_terms = _relevance_terms(session.case)
        legacy = sum(1 for term in case_terms if term in conf.lower())
        self.assertEqual(legacy, 0, "the fixture no longer reproduces the audit's limitation")
        self.assertGreater(scores["nginx/site.conf"], 0,
                           "the file stating client_max_body_size 2m still scores zero")

        # Irrelevant content gains nothing, and is therefore last.
        self.assertEqual(scores["app/unrelated.py"], 0)
        self.assertEqual(findings[-1]["ref"], "app/unrelated.py")

    def test_relevant_files_stay_above_irrelevant_ones(self):
        session = self._session(verifier_targets=["app/unrelated.py", "logs/app.log",
                                                  "nginx/site.conf"])
        findings = session.workers["verifier"]["findings"]
        scores = [finding["score"] for finding in findings]
        self.assertEqual(scores, sorted(scores, reverse=True), "the report is not score-ordered")
        self.assertGreater(scores[0], scores[-1], "nothing out-ranked the irrelevant file")

    def test_the_report_is_byte_identical_across_repeated_runs_and_target_orders(self):
        first = self._session(verifier_targets=["nginx/site.conf", "logs/app.log",
                                               "app/unrelated.py"]).workers["verifier"]["findings"]
        for _ in range(3):
            again = self._session(verifier_targets=["nginx/site.conf", "logs/app.log",
                                                   "app/unrelated.py"])
            self.assertEqual(again.workers["verifier"]["findings"], first)
        reordered = self._session(verifier_targets=["app/unrelated.py", "logs/app.log",
                                                   "nginx/site.conf"])
        self.assertEqual([f["ref"] for f in reordered.workers["verifier"]["findings"]],
                         [f["ref"] for f in first], "ordering depended on the order targets were asked for")
        self.assertEqual([f["score"] for f in reordered.workers["verifier"]["findings"]],
                         [f["score"] for f in first])

    def test_a_highly_matching_finding_is_still_not_evidence(self):
        """Matching the issue perfectly does not promote an observation into the evidence set."""
        session = self._session(verifier_targets=["nginx/site.conf", "logs/app.log"])
        self.assertGreater(session.workers["verifier"]["findings"][0]["score"], 1)
        for item in session.evidence.items:
            self.assertIn(item.source, ("case", "engineer"))
        blob = str(session.evidence.to_dict())
        for finding in session.workers["verifier"]["findings"]:
            self.assertNotIn(finding["content"][:40], blob,
                             "a well-matching finding leaked into the evidence set")

    def test_a_highly_matching_finding_is_still_labelled_an_observation(self):
        engineer = ScriptedEngineer()
        self._session(verifier_targets=["nginx/site.conf"], engineer=engineer)
        text = "\n".join(engineer.shown)
        self.assertIn("observations of the CURRENT system", text)
        self.assertIn("not a verdict", text)

    def test_the_matching_code_imports_no_model_or_network(self):
        """The improvement is arithmetic on strings. It must stay that way."""
        tree = ast.parse(Path(worker_stage.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("openai", "anthropic", "google", "httpx", "requests", "socket",
                          "urllib", "http", "subprocess", "asyncio"):
            self.assertNotIn(forbidden, imported, f"worker_stage imports {forbidden!r}")

    def test_matching_is_a_pure_function_of_its_input(self):
        text = "HTTP 413 for client_max_body_size 2m after ECONNRESET"
        with mock.patch("random.random", side_effect=AssertionError("randomness used")), \
                mock.patch("time.time", side_effect=AssertionError("clock read")):
            results = {_canonical_tokens(text) for _ in range(20)}
        self.assertEqual(len(results), 1, "the matcher is not deterministic")
        self.assertEqual(_canonical_tokens(text), _canonical_tokens(text.upper()))


if __name__ == "__main__":
    unittest.main()