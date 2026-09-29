"""Structured issue input (JSON/YAML): ingestion only, mapped onto the existing DebugInput."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import loop_support  # noqa: F401  (path wiring)
from loop_support import RELEVANT_VIEW, FakeLLM, FakeMemoryPort, hyp
from debugagent.cli import main
from debugagent.pipeline.ingest import load_debug_input, to_debug_input
from debugagent.pipeline.normalize import InputError, NormalizationError, normalize
from debugagent.pipeline.types import DebugInput

REPO = Path(__file__).resolve().parents[1]
ISSUE = {
    "description": "media-uploader resets connections on uploads over 2 MB behind nginx",
    "measurements": ["20/20 uploads of 2.5MB fail with ECONNRESET"],
    "environment": {"service": "media-uploader", "runtime": "node20", "proxy": "nginx-1.25"},
}
YAML_ISSUE = """\
description: |
  media-uploader resets connections on uploads over 2 MB behind nginx
measurements:
  - 20/20 uploads of 2.5MB fail with ECONNRESET
environment:
  service: media-uploader
  runtime: node20
  proxy: nginx-1.25
"""


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return path

    def test_json_maps_onto_debug_input(self):
        raw = load_debug_input(self.write("issue.json", json.dumps(ISSUE)))
        self.assertEqual(raw, DebugInput(ISSUE["description"], ISSUE["measurements"], ISSUE["environment"]))

    def test_yaml_and_json_give_the_same_case(self):
        from_json = load_debug_input(self.write("issue.json", json.dumps(ISSUE)))
        from_yaml = load_debug_input(self.write("issue.yml", YAML_ISSUE))
        self.assertEqual(from_yaml, from_json)
        self.assertEqual(normalize(from_yaml).to_dict(), normalize(from_json).to_dict())

    def test_file_input_normalizes_like_typed_input(self):
        typed = DebugInput(ISSUE["description"] + "\nservice=media-uploader runtime=node20 proxy=nginx-1.25",
                           ISSUE["measurements"])
        from_file = load_debug_input(self.write("issue.json", json.dumps(ISSUE)))
        self.assertEqual(normalize(from_file).environment, normalize(typed).environment)
        self.assertEqual(normalize(from_file).symptoms, normalize(typed).symptoms)

    def test_single_string_measurement_and_unquoted_numbers(self):
        raw = load_debug_input(self.write("issue.yaml", "description: x times out\nmeasurements: 30 s timeout\n"
                                                           "environment:\n  runtime: 3.11\n  replicas: 4\n"))
        self.assertEqual(raw.measurements, ["30 s timeout"])
        self.assertEqual(raw.environment_hints, {"runtime": "3.11", "replicas": "4"})

    def test_unknown_field_is_rejected_not_ignored(self):
        with self.assertRaises(InputError) as ctx:
            load_debug_input(self.write("issue.json", json.dumps(dict(ISSUE, enviroment={"x": "y"}))))
        self.assertIn("unknown field(s) ['enviroment']", str(ctx.exception))

    def test_missing_description(self):
        with self.assertRaises(InputError) as ctx:
            to_debug_input({"measurements": ["x"]}, source="issue.json")
        self.assertIn("description: required", str(ctx.exception))

    def test_booleans_and_nulls_are_not_text(self):  # YAML reads `proxy: yes` as True
        with self.assertRaises(InputError) as ctx:
            to_debug_input({"description": "x", "environment": {"proxy": True, "region": None}})
        self.assertIn("environment.proxy: expected text, got bool", str(ctx.exception))
        self.assertIn("environment.region: expected text, got nothing", str(ctx.exception))

    def test_wrong_shapes(self):
        for bad in ({"description": "x", "measurements": {"a": 1}}, {"description": "x", "environment": ["a"]}, ["x"]):
            with self.subTest(bad=bad), self.assertRaises(InputError):
                to_debug_input(bad)

    def test_file_problems_are_one_line_input_errors(self):
        cases = {"issue.txt": "unsupported input file", "missing.json": "input file not found",
                 "bad.json": "not valid JSON (line 1", "bad.yaml": "not valid YAML"}
        self.write("bad.json", "{description: x")
        self.write("bad.yaml", "description: [unclosed")
        self.write("issue.txt", "description: x")
        for name, expected in cases.items():
            with self.subTest(name=name), self.assertRaises(InputError) as ctx:
                load_debug_input(self.dir / name)
            self.assertIn(expected, str(ctx.exception))
            self.assertNotIn("\n", str(ctx.exception))

    def test_yaml_without_pyyaml_explains_the_fix(self):
        path = self.write("issue.yaml", YAML_ISSUE)
        with mock.patch.dict("sys.modules", {"yaml": None}):
            with self.assertRaises(InputError) as ctx:
                load_debug_input(path)
        self.assertIn("pip install pyyaml, or use a .json file", str(ctx.exception))

    def test_environment_conflicting_with_text_still_fails_closed(self):  # existing normalizer rule, unchanged
        raw = to_debug_input({"description": "x service=orders-api", "environment": {"service": "media-uploader"}})
        with self.assertRaises(NormalizationError):
            normalize(raw)

    def test_demo_input_files_all_load(self):
        for path in sorted((REPO / "demo" / "inputs").iterdir()):
            with self.subTest(file=path.name):
                case = normalize(load_debug_input(path))
                self.assertTrue(case.problem_signature)
                self.assertIsNotNone(case.environment["service"])


class CliInputFlagTests(unittest.TestCase):
    def test_debug_with_input_file_skips_the_typing_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            issue = Path(tmp) / "issue.json"
            issue.write_text(json.dumps(dict(ISSUE, environment=dict(ISSUE["environment"], region="us-east-1"))))
            prompts = []
            answers = iter(["", "supported", "y", "accept", "", "supported", "accept", "", "n"])  # facts, H1, H2, resolve

            def ask(prompt):
                prompts.append(prompt)
                return next(answers)
            out, err = io.StringIO(), io.StringIO()
            code = main(["debug", "--input", str(issue)], ask=ask, out=out, err=err, port=FakeMemoryPort(RELEVANT_VIEW),
                        llm=FakeLLM({"hypotheses": [hyp(cites=["3f9a1c07b2e4d815"]), hyp(text="app-side limit")]}),
                        state_dir=tmp)
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn(f"Issue loaded from {issue}: {ISSUE['description']}", out.getvalue())
        self.assertFalse(any("Describe the issue" in p or "Measurements" in p for p in prompts))
        self.assertIn("service: media-uploader", out.getvalue())

    def test_bad_input_file_is_a_one_line_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            code = main(["debug", "--input", str(Path(tmp) / "nope.json")], ask=lambda p: "", out=out, err=err,
                        port=FakeMemoryPort(RELEVANT_VIEW), llm=FakeLLM(), state_dir=tmp)
        self.assertEqual(code, 1)
        self.assertTrue(err.getvalue().strip().startswith("error: input file not found:"))


if __name__ == "__main__":
    unittest.main()
